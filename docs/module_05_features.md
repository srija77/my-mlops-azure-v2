# Module 5 — Features on Azure ML: Data Assets, and when you actually need a Feature Store

**Prerequisite:** Module 4 (validated data + quality gate).
**You will build:** the `build_features` component, and features registered as a versioned Data Asset.

---

## The honest starting point

This project used to ship a **Feast** feature store: `feature_store.yaml`, `feature_definitions.py`, a Blob registry, an Azure Cache for Redis online store. It looked complete.

**Nothing ever called it.**

```bash
grep -rn "^import feast\|FeatureStore(\|get_online_features" src/ scoring/ aml/
# (no matches)
```

`train.py` read the parquet straight from the previous component's output folder. The "feature store" was three config files and a directory name. It was deleted.

Say this to a class directly, because it is the most common failure in MLOps portfolios: **a feature store that is never called is not a feature store, it is decoration.** The cost of pretending is real — the next engineer wires against an abstraction that does not exist.

---

## What replaced it: a named pipeline output

In [`aml/pipeline.yml`](../aml/pipeline.yml):

```yaml
outputs:
  model_features:
    type: uri_folder
    name: march_2025_features     # <- this line is the whole feature

jobs:
  prepare:
    outputs:
      features: ${{parent.outputs.model_features}}
```

Naming an output promotes it from a job-scoped temp folder into a **first-class Data Asset**. You get:

| Capability | How |
|---|---|
| **Discovery** | Data → Data assets → `march_2025_features` — browsable by anyone in the workspace |
| **Versioning** | Every pipeline run adds a version (omit `version:` and AML auto-increments) |
| **Lineage** | Studio traces the asset back to the job, the code snapshot, and `energy_raw` |
| **Reuse** | Any other job can consume `azureml:march_2025_features:3` by name |
| **Immutability** | A version's contents never change |

Cost: **zero extra infrastructure.** No Spark, no Redis, no ADLS Gen2, no second workspace.

For a batch-training pipeline with one consumer, that is most of what people actually want from a feature store — and it is native Azure ML.

---

## What a Feature Store adds that this does not

Be fair to the real thing. A Data Asset is a *versioned folder*; a feature store is a *serving system*. Three capabilities you cannot fake with parquet:

1. **Point-in-time correct joins.** Given a label at 14:00 on 12 March, fetch every feature *as it was known at 14:00* — never later. Our `build_features` handles this by hand with `.shift()` and lag columns, which works because there is one table and one author. It does not survive twenty feature definitions and five teams.

2. **Online/offline consistency.** The same feature definition serves both training (batch, historical) and inference (single row, milliseconds). Training/serving skew is one of the top causes of "great offline, bad in production", and a feature store exists largely to eliminate it. Our endpoint receives fully-formed features from the caller — we sidestep the problem rather than solve it.

3. **Reuse across teams.** `dam_mcp_lag_7d` computed once, discovered and consumed by other models, with an owner and a contract.

**The decision rule for students:** you need a feature store when you have **online serving with features computed from history**, or **feature reuse across teams**. One batch model with one consumer does not qualify. Adopting one anyway buys you a Spark dependency and a Redis bill in exchange for nothing.

---

## Azure ML Managed Feature Store — the native option

If you do need one on Azure, **do not reach for Feast** — Azure ML has a first-party managed feature store:

```powershell
az ml feature-store create --name fs-energy --resource-group rg-energy-mlops
az ml feature-set create   --name dam_mcp_features --version 1 --feature-store-name fs-energy
az ml feature-set list-features --name dam_mcp_features --feature-store-name fs-energy
```

Concepts map cleanly from Feast: `FeatureView` → **feature set**, `Entity` → **entity**, `FeatureService` → **feature retrieval spec**.

### Its prerequisites (why this course does not use it)

| Requirement | Why | This subscription |
|---|---|---|
| **ADLS Gen2** offline store | Materialised features need hierarchical namespace | `stenergyvuzan3y2` has HNS **disabled** — and HNS **cannot be enabled after creation**. Needs a new storage account |
| **Serverless Spark** | Materialisation *and* retrieval run on Spark, not on your cluster | Quota is **4 total vCPUs**. AML's smallest Spark instance is 4 cores for the driver alone, before any executor. Unusable |
| **Azure Cache for Redis** | Online store for low-latency lookups | ~$16+/month, always-on |
| Separate feature-store workspace | It is its own resource kind | — |

That table is worth showing as-is. It teaches something more durable than the feature store itself: **managed services have prerequisites that only appear when you try to provision them.** "Azure ML has a feature store" is true and useless without "…and it needs ADLS Gen2, Spark quota, and Redis."

---

## The component

[`src/feature_engineering/build_features.py`](../src/feature_engineering/build_features.py) — ported from the source project with the transformation logic **unchanged**, so the Azure output is comparable to the local one. Only I/O changed: hard-coded `PROJECT_ROOT` paths became `--validated-dir` / `--output-dir`, and the Marquez lineage wrapper was dropped (Azure ML records lineage from the pipeline graph).

Output: **4,608 rows × 39 columns**, one row per 15-min block, `dam_mcp` as target.

### The parity check — and the bug it found

Comparing the Azure-built table against the known-good parquet from the local pipeline:

```
old (4608, 39)   new (4608, 39)     columns identical: True
range 2025-03-08 00:00 -> 2025-03-31 23:45   (both)

largest per-column absolute difference:
   total_rainfall      46.4
   avg_cloud_cover      1.30
   avg_humidity         0.55
   avg_temp             0.12
   hour_sin             0        <- every market/time/lag/calendar feature: exact
```

Every market, time, cyclical, lag, rolling, and calendar feature matches **exactly**. Only the weather aggregates differ — and the cause is instructive:

> The local pipeline's validated weather held **43,405 duplicate `(city, time)` readings out of 91,429**. Averaging across stations therefore double-counted whichever stations happened to be duplicated. The Azure pipeline dedups at silver, so its weather features are **more correct**. The gap is the local bug, not a porting error.

This is the payoff of building the raw front-half. A pre-baked feature parquet cannot tell you it was computed from duplicated inputs.

---

## Check yourself

1. Your model serves online, and one feature is "average price over the last 24h". Why is a parquet Data Asset not enough?
2. `march_2025_features` gains a version on every pipeline run. How would you find which version trained model v2?
3. Your team says "we need a feature store" for a nightly batch model with one consumer. What do you ask them?

---

**Next:** Module 6 — training, MLflow tracking, and the model registry
