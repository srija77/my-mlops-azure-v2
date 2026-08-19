# Azure MLOps — Console Demo Script (45 min)

Everything below is done **in the browser**: [ml.azure.com](https://ml.azure.com) and
[portal.azure.com](https://portal.azure.com). No CLI in front of the audience.

| | |
|---|---|
| Subscription | `Azure subscription 1` (`b2acf5e5-7e53-4270-a76b-71adbc172c10`) |
| Tenant | `0c1def5c-2537-4ddb-802e-4ddc7fda6013` |
| Resource group | `rg-energy-mlops` (eastus) |
| Workspace | `mlw-energy-forecast` |
| Compute | `cpu-cluster` — Standard_DS2_v2, 0→2 nodes |

> **Quota note.** This subscription allows **4 Total Regional vCPUs**. `Standard_DS2_v2`
> is 2 vCPUs, so 2 nodes is the ceiling. Do not switch to `DS3_v2` (4 vCPUs) — one node
> would consume the entire quota and the online endpoint would have nowhere to run.

---

## The story you are telling

> "A price-forecasting model for power trading. It works on my laptop. Now make it
> something a team can run, trust, and retrain — without anyone re-running a notebook."

Nine stops. The turning point is **Stop 5**, where the pipeline refuses to train on bad data.

---

## Stop 1 — The resource group (2 min) · portal.azure.com

**Resource groups → `rg-energy-mlops`**

Six resources. Ask the room: *"which of these is the ML part?"* Only one is:

| Resource | Why it exists |
|---|---|
| `mlw-energy-forecast` | The workspace — the actual MLOps control plane |
| `stenergyvuzan3y2` | Storage — every dataset, job output, and model artifact |
| `kv-energy-vuzan3y2` | Key Vault — secrets, connection strings |
| `acrenergyvuzan3y2` | Container Registry — the environment images jobs run in |
| `appi-energy-vuzan3y2` | App Insights — job and endpoint telemetry |
| Smart Detection | Auto-created alert rule |

**The point:** an Azure ML workspace is never alone. Storage, Key Vault, ACR, and App Insights are dependencies it creates on your behalf. All six were produced by one Bicep file ([`infra/main.bicep`](../infra/main.bicep)) — nobody clicked them into existence, which is why the environment can be rebuilt identically tomorrow.

---

## Stop 2 — Data assets (4 min) · ml.azure.com

**Data → Data assets → `energy_raw` → version 1**

Show the **URI**:

```
azureml://.../workspaceblobstore/paths/LocalUpload/d990f879cbb669...d15d544d/raw/
```

That long hex string is a **content hash**. Three things to say:

1. A data asset does **not** contain data. It contains the *address* of data, plus a version. It is the same object as a `.dvc` file, or a git tag for data.
2. The hash means `energy_raw:1` is immutable. Overwrite the folder tomorrow and v1 still resolves to what March 2025 actually looked like.
3. **Versions are immutable by design.** Try to re-register as v1 with different content and Azure refuses — you must create v2. That refusal *is* the feature.

Open **Explore** and show the bronze layout:

```
dam/year=2025/month=03/date=2025-03-04/dam.csv    28 daily files, 15-min blocks
rtm/...                                            31 daily files
weather/{City_State}.csv                           65 stations, hourly
calendar/calendar.csv                              33 rows
generation/dgr_master.csv                          744 rows
```

**Ask the room:** March has 31 days. Why does DAM have 28 files? *(March 1–4 morning was never scraped. The usable history is shorter than "March 2025" suggests — and nothing in a notebook would have told you.)*

---

## Stop 3 — The pipeline graph (5 min)

**Jobs → `dam_mcp_forecast` → latest run**

Seven nodes, left to right:

```
energy_raw → ingest → validate → build_features → prepare → train → evaluate → promote
```

Say plainly: **this graph is the deliverable.** Not the model — the graph. It is the thing that can be re-run, scheduled, audited, and handed to someone else.

Click any node → **Code** tab: the exact source snapshot that ran. Not "the code as it is today" — the code *as it was when this ran*. Pair that with the immutable data version and a run becomes genuinely reproducible.

**Contrast for the audience:** the local version of this project used DVC, where dependencies are file paths on one laptop and the command runs in whatever Python happens to be installed. Both weak links are gone here: paths became typed ports the platform mounts, and "whatever Python" became a pinned container image in ACR.

---

## Stop 4 — Metrics on a *data* stage (4 min)

Click **`ingest` → Metrics**:

| Metric | Value |
|---|---|
| `ingest_rows_dam` | 2,640 |
| `ingest_dupes_dam` | **2,639** |
| `ingest_rows_weather` | 48,360 |

**This is a real defect, found by the pipeline.** The DAM source files contain every 15-min block **exactly twice**, byte-identical — 192 rows/day for 96 real blocks. The scraper wrote each table twice and nobody noticed.

Most people expect metrics to be a model thing. Correct that instinct: **the earlier a number is tracked, the earlier a regression is caught.** A model metric tells you something broke; a data metric tells you *what*.

Then the harder question: **should duplicates fail the pipeline?** No — and this is worth arguing out loud. A duplicate is a known artifact with an obvious fix, so it is cleaned in `ingest`. But the count is logged, so it never becomes invisible.

> **The rule:** cleaning removes what you know how to fix. Validation stops what you don't.

---

## Stop 5 — 🔴 The money shot: break it on purpose (10 min)

Everything so far is a tour. This is the part they remember.

**First show the gate passing.** `validate` → Metrics:

```
valid_rate_dam       1.0
valid_rate_rtm       1.0
valid_rate_weather   1.0
validation_passed    1
```

**Now poison the data.** In the terminal (or pre-record it):

```powershell
python -c "import pandas as pd; p='data/raw/dam/year=2025/month=03/date=2025-03-10/dam.csv'; d=pd.read_csv(p); d.loc[:,'MCP (Rs/MWh) *']=99999; d.to_csv(p,index=False)"
az ml data create -f aml/data_assets/01_raw_energy.yml --set version=2
az ml job create -f aml/pipeline.yml --set inputs.raw_data.path=azureml:energy_raw:2
```

Rs 99,999/MWh is impossible — IEX is capped at Rs 10,000/MWh **by regulation**.

**Watch the graph in Studio:**

- `ingest` → green
- `validate` → **red**, `mcp_out_of_range`
- `build_features`, `train`, `evaluate`, `promote` → **never start**

Land it:

> "No model was trained. No model was registered. Nothing was promoted. The bad batch
> couldn't reach production, because **there is no path from it to production.**"

Then `validate` → **Outputs** → `dam/invalid/` — the rejected rows, with a `_reject_reason` column, sitting on the datastore, versioned by the run that rejected them. That is the audit trail, and it costs nothing to keep.

**Why the bounds are defensible:** every threshold comes from the domain, not the data. Rs 10,000/MWh is regulation. −20…60 °C is Indian climate. Contrast with `mcp < quantile(0.99)` — a bound that *moves when the data moves*, and therefore cannot catch a systematic shift, because the shift redefines the threshold.

Restore the file and re-register v3 before continuing.

---

## Stop 6 — Model registry and governance (5 min)

**Models → `dam_mcp_forecast`**

- **Versions** — one per training run, each linked to the job that produced it
- **Tags** on the latest: `passed_eval=True`, `eval_rmse`, `eval_mape`
- **`champion=true` tag** — held by exactly one version: the one serving production

> **Worth saying out loud, because it is a genuine Azure gotcha.** MLflow has a
> first-class *alias* concept (`model@champion`), and most tutorials use it.
> **Azure ML's registry does not implement it** — the alias API returns HTTP 404
> against a workspace registry. This project's first run failed on exactly that.
> So the champion pointer here is a **tag**, and `promote` guarantees only one
> version carries it. Deployments resolve the tag to a version number.
> The lesson generalises: *MLflow-compatible* is not *MLflow-identical*, and the
> gaps only show up when you run it.

Two gates, two different questions — say this explicitly, because people merge them:

| Gate | Question | Where |
|---|---|---|
| `min_valid_rate = 0.95` | Is this **data** fit to train on? | `validate` |
| `rmse < 1000`, `mape < 15%` | Is this **model** fit to serve? | `evaluate` |

`promote` only moves the alias if the new version passed eval **and** beats the incumbent on `eval_rmse`. A worse model trains, registers, and is kept — but never becomes champion. **Promotion is a decision, not a side effect of training.**

---

## Stop 7 — Serving (5 min)

**Endpoints → `dam-mcp-endpoint` → Test tab**

Paste [`scoring/sample_request.json`](../scoring/sample_request.json), hit **Test**, get a price back — in the browser, no code.

Point at **Details**: the deployment is pinned to a concrete model version, and CI/CD resolved that version from the `champion=true` tag at deploy time (see Stop 6 — Azure ML has no working alias API to point at directly). Promote a new champion and the next deploy picks it up automatically.

Traffic % is where blue/green lives — deploy a challenger at 10%, watch it, then shift.

---

## Stop 8 — Monitoring (4 min)

**Monitoring** — data drift, prediction drift, data quality against the training baseline.

The honest framing: everything up to Stop 7 protects you *before* deployment. Monitoring is the only thing watching *after*. For power price forecasting the model decays fast — fuel prices, a monsoon, a policy change all shift the distribution. The drift signal is what triggers a retrain.

**Retraining** — the same pipeline on a schedule (`az ml schedule create`). Note what that means: retraining re-runs the **whole graph**, including the data gate. A scheduled retrain on bad data stops at `validate`. Retraining is safe *because* the gate is upstream of it.

---

## Stop 9 — CI/CD (6 min) · GitHub Actions

[`.github/workflows/mlops.yml`](../.github/workflows/mlops.yml) — three jobs:

```
ci      lint + unit tests            every push/PR, no Azure needed
  ↓
train   submit AML pipeline, stream  fails the build if any gate fails
  ↓
deploy  champion → endpoint          ⏸ BLOCKED on human approval
```

Show a run in the **Actions** tab, then the **`production` environment** pausing for review. Approve it live and watch the deploy proceed.

The two things to say:

1. `az ml job stream` exits non-zero when the pipeline fails, so **a failed data gate fails the build**. CI/CD and data quality are the same control flow, not two systems.
2. `deploy` requires a human. Everything before it is automatic; production is not. **Automate the work, gate the consequence.**

---

## Teardown (stop the billing)

```powershell
az ml online-endpoint delete -n dam-mcp-endpoint --yes   # the only always-on cost
az group delete --name rg-energy-mlops --yes --no-wait   # everything
```

`cpu-cluster` costs nothing idle (scales to 0). The endpoint holds a VM 24/7 — delete it after the demo.

---

## If something fails live

| Symptom | Cause | Fix |
|---|---|---|
| Job stuck `Preparing` | ACR is building the env image (first run only, ~8 min) | Wait, or pre-warm before the demo |
| `Not enough quota` | Asked for more than 4 vCPUs | `Standard_DS2_v2`, max 2 nodes |
| Endpoint deploy fails | Endpoint quota is separate from cluster quota | Request quota, or demo Stops 1–6 + 9 |
| `pkg_resources` ModuleNotFound | `setuptools` missing — mlflow needs it at import | Already fixed in [`conda.yml`](../aml/environment/conda.yml) |

**Pre-warm before presenting:** run the pipeline once an hour ahead. The image gets built, the cluster has a warm node, and your live run starts in seconds instead of minutes.
