# Module 2 — The Data Plane: datastores, data assets, and what replaced DVC

**Prerequisite:** Modules 0–1 (subscription + Bicep infra) are done.
**You will build:** the `energy_raw` bronze Data Asset, the pipeline's true starting point.

---

## The concept students need first

Azure ML separates three things that beginners collapse into one word, "data":

| Thing | What it is | Lives where | Analogy in the local project |
|---|---|---|---|
| **Storage account / container** | Actual bytes on Azure Blob | `stenergygqjy6fau` | your `data/` folder |
| **Datastore** | A registered *connection* to that container, holding the auth | `workspaceblobstore` | your S3 remote config in `.dvc/config` |
| **Data Asset** | A named, versioned *pointer* to a path in a datastore | `energy_raw:1` | a `.dvc` file committed to git |

The single most useful sentence for a class: **a Data Asset does not contain data — it contains the address of data, plus a version number.** That is exactly what a `.dvc` file is. When you deleted `weather.dvc` and its friends, you deleted the local version pointers; `energy_raw:1` is the Azure replacement.

---

## Why not just pass a Blob path to the job?

You can. The job would run. You would lose:

- **Immutability.** Registering uploads to a content-hashed path. Ours became
  `.../LocalUpload/d990f879cbb66974629bd853afe35fdefe20dd6f2a90ad3bd002b202d15d544d/raw/`.
  That hash is the same idea as DVC's md5 — overwrite the source folder tomorrow and `energy_raw:1` still resolves to what March 2025 actually looked like.
- **Lineage.** Studio can answer "which model versions were trained on this data?" only if the data is an asset.
- **Reproducibility of a *retrain*.** `--set inputs.raw_data.path=azureml:energy_raw:2` is a one-line diff that fully describes a data change.

---

## Choosing the asset type (the decision students get wrong)

| Type | Use when | Why not here |
|---|---|---|
| `uri_file` | Exactly one file | We have 162 files across 5 sources |
| `mltable` | One tabular schema, loadable with `mltable.load()` — carries column types and parsing rules | Bronze has five *different* schemas: DAM 15-min CSV, RTM 30-min sessions, hourly weather per station, daily calendar, daily generation. `mltable` belongs downstream where one schema is true |
| **`uri_folder`** ✅ | A directory the job walks itself | Correct: the ingest component globs the Hive partitions itself |

The old `march_2025_prepared:1` asset in this workspace is a `uri_file` — appropriate for what it was (a single pre-baked parquet), and precisely the shortcut this module removes.

---

## Build it

```powershell
az ml data create -f aml/data_assets/01_raw_energy.yml
az ml data show -n energy_raw --version 1 -o yaml
```

The asset definition: [`aml/data_assets/01_raw_energy.yml`](../aml/data_assets/01_raw_energy.yml)

### What to point at in Studio

Data → Data assets → `energy_raw` → **Explore**. Show the class:

1. The **URI** — the content-hash path. Register the same folder twice unchanged and you get the same hash.
2. **Version 1** with tags (`layer: bronze`, `granularity: dam=15min;...`). Tags are how you make an asset self-describing for someone who joins the project in month six.
3. The **Lineage** tab is empty right now. After Module 5 it will show jobs consuming this asset. That transition is the demo.

---

## What the bronze layer actually contains

```
dam/year=2025/month=03/date=YYYY-MM-DD/dam.csv     28 daily files, 15-min blocks
rtm/year=2025/month=03/date=YYYY-MM-DD/rtm.csv     31 daily files
weather/{City_State}.csv                            65 stations, hourly
calendar/calendar.csv                               33 rows (IPL double-headers)
generation/dgr_master.csv                           744 rows, daily regional
```

Two teaching points hide in those numbers, both of which surface as metrics in Module 3–4:

- **DAM has 28 files, not 31**, and starts at `2025-03-04 12:00`. March 1–4 morning was never scraped. The model's usable history is shorter than "March 2025" implies.
- **`weather/` holds 66 entries but only 65 stations** — the 66th is `run_summary.json`, an audit artifact from the local ingestion. A glob of `weather/*` would have counted a JSON file as a weather station. This is why the ingest component globs `*.csv`, not `*`.

---

## Cost

Registration cost is storage only: 9.24 MB in Standard_LRS ≈ negligible. Nothing here starts compute.

---

## Check yourself

1. Where do the *bytes* of `energy_raw:1` live — in Azure ML, or in a storage account?
2. You overwrite `data/raw/weather/Chennai_Tamil_Nadu.csv` and re-run `az ml data create` with `version: "1"`. What happens? (It fails — versions are immutable. You must create `version: "2"`.)
3. Why would `mltable` be the right choice for the *validated* layer but the wrong choice for bronze?

---

**Next:** [Module 3 — Ingestion as a component](module_03_ingestion.md)
