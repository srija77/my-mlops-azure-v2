# Module 3 — Ingestion as a Component: bronze → silver

**Prerequisite:** Module 2 (`energy_raw:1` registered).
**You will build:** the `ingest` component — the first node of the pipeline graph.

---

## The concept: what a "component" actually is

A **command component** is three things bound together and given a version:

```
inputs/outputs  (a typed contract)
      +
code            (a snapshot uploaded to Blob at submit time)
      +
environment     (a container image in ACR)
      =  a reusable, versioned unit of compute
```

Compare with the local project. In `dvc.yaml` a stage was:

```yaml
build_features:
  cmd: python src/3_feature_engineering/1_build_features.py
  deps: [data/validated, src/.../1_build_features.py]
  outs: [data/features/.../march_2025_prepared.parquet]
```

DVC's `deps`/`outs` are **paths on your laptop**, and `cmd` runs in **whatever Python you happen to have**. The Azure component replaces both weak links: paths become typed input/output ports the platform mounts for you, and "whatever Python" becomes a pinned image. That is the whole difference, and it is worth dwelling on with a class — most MLOps failures in the wild are one of those two things drifting.

---

## The design decision: scraping does not belong in this component

The source project's `src/1_ingestion/1_ingest_dam_rtm_local.py` (621 lines) scrapes IEX India with Selenium *and* cleans the result. Only the cleaning survives the move to Azure. Reasons to give the class, in order of importance:

1. **Reproducibility.** A job that scrapes a live website is not reproducible. Re-run last month's pipeline and you get today's prices — or a 404. A pipeline you cannot re-run is not a pipeline, it is a script.
2. **Wrong compute.** `cpu-cluster` nodes have no browser, and they scale to zero mid-scrape.
3. **Wrong failure domain.** A website changing its HTML should not fail your *training* pipeline.

The Azure-correct split:

```
Azure Function / Data Factory (timer)  →  lands raw files in Blob  →  bronze Data Asset
                                                                          │
                                              Azure ML pipeline starts here ▼
```

So `ingest.py` starts from files that already landed. Same input → same output, always. Teach this as the **"landing job vs. processing job"** boundary; it is one of the most transferable ideas in the course.

---

## What the component does

[`src/ingestion/ingest.py`](../src/ingestion/ingest.py) — bronze CSVs → silver Parquet:

| Source | Bronze | Silver | Note |
|---|---|---|---|
| DAM | 28 daily CSVs | `dam/year=/month=/date=/part-0001.parquet` | deduped on `Datetime` |
| RTM | 31 daily CSVs | same layout | deduped on `(Datetime, Session ID)` |
| Weather | 65 station CSVs | `weather/city={station}/part-0001.parquet` | `city_name` added as entity key |
| Calendar | 1 CSV | `calendar/calendar.parquet` | `dd-mm-YYYY` → real timestamps |
| Generation | 1 CSV | `generation/generation.parquet` | landed, not yet modelled |

Every row gets three lineage columns — `ingestion_date`, `source_file`, `pipeline_run_id` — carried over from the source project. `pipeline_run_id` prefers `AZUREML_RUN_ID`, so a suspect row points back at the exact job that produced it.

### The finding this stage surfaces

```
DAM         28 day files -> 2,640 rows (2,639 duplicates dropped)
```

The March 2025 DAM files contain **every 15-min block exactly twice** (192 rows/day for 96 blocks), byte-identical. The scraper wrote each table twice. Nobody noticed because the local feature builder deduped silently much later.

**Where should dedup live?** This is a good five-minute classroom argument. The answer this project settles on: **silver, not the quality gate.** A duplicate is not a data-quality *breach* requiring a human decision — it is a known artifact with an obvious fix. Rejecting duplicates in the gate would fail the pipeline at a 50% valid rate every single run, and a gate that always fails gets disabled. But the count is still logged (`ingest_dupes_dam = 2639`), so the artifact stays visible instead of becoming invisible.

Rule of thumb for students: **cleaning removes what you know how to fix; validation stops what you don't.**

---

## Build and run it

```powershell
# Local first — components are plain scripts, always debug them off-cluster
python src/ingestion/ingest.py --raw-dir data/raw --output-dir outputs/silver
```

Component definition: [`aml/components/ingest.yml`](../aml/components/ingest.yml)

### The `.amlignore` lesson

Every component declares `code: ../..` (the repo root) so `src/` is importable. Without [`.amlignore`](../.amlignore) that snapshot was **199 MB** — mostly an unrelated `windturbine-mlops/` folder — re-uploaded per component per submit. With it: **0.13 MB**.

Two rules for the class:

- `.gitignore` controls what gets **committed**; `.amlignore` controls what gets **shipped to compute**. Different questions, separate files.
- **Data never belongs in the snapshot.** It is mounted at runtime from the Data Asset. If you find yourself shipping a parquet in `code:`, you have skipped Module 2.

---

## What to point at in Studio

Once the job runs: Jobs → the pipeline → `ingest` node →

1. **Outputs + logs** → `user_logs/std_log.txt` — the per-source row counts.
2. **Metrics** → `ingest_rows_*` and `ingest_dupes_dam`. Metrics on a *data* stage feel odd to people who think MLflow is only for models. That instinct is what to correct: the earlier a number is tracked, the earlier a regression is caught.
3. **Outputs** → the silver folder on `workspaceblobstore`. Point out it is written to a job-scoped path — outputs are artifacts of the run that made them.

---

## Check yourself

1. Why does the component glob `weather/*.csv` rather than `weather/*`?
2. The DAM duplicates are byte-identical. Would `keep="first"` vs `keep="last"` change the output? What if they were *not* identical — where should that be caught?
3. Your snapshot is 400 MB and submits take 4 minutes. Name two things to check before blaming Azure.

---

**Next:** [Module 4 — Validation and the quality gate](module_04_validation.md)
