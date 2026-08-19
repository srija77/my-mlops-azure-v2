# Module 4 — Validation and the Quality Gate: silver → validated

**Prerequisite:** Module 3 (`ingest` component runs).
**You will build:** the `validate` component — the gate that decides whether training is allowed to happen.

---

## The concept: a gate is a control-flow decision, not a report

Most teams have "data validation" that writes a report nobody reads. The Azure lesson here is smaller and sharper:

> **A failed expectation must fail the job, because a failed job stops the pipeline.**

`validate.py` raises `SystemExit` on a breach. Azure ML marks the job `Failed`, and `build_features`, `train`, `evaluate`, `promote` never start. No model is registered. No champion moves. The bad batch cannot reach production because *there is no path from it to production*.

Contrast with the local project, where `gx_validate_local.py` wrote pass/fail to Postgres and a human was expected to look. Same expectations, completely different guarantee.

---

## What got dropped in the port, and why

The source project's [`5_gx_validate_local.py`](../../my-mlops-project/src/2_validation/5_gx_validate_local.py) is 625 lines doing three jobs. Two do not survive:

| Local piece | Azure replacement | Why |
|---|---|---|
| Great Expectations suites | plain pandas expectations | Avoids a second ACR image built solely to validate. Swappable — see below |
| Postgres audit tables | MLflow metrics + rejected-rows output | Nobody has to keep a database alive to answer "what did we throw away in March?" The answer is an artifact of the run that produced it |
| GX Data Docs | the job's Metrics tab + `_validation_report.json` | One fewer service to host, back up, and explain |

**If you want GX back for the course** — and there is a decent argument for it, since students should see Data Docs at least once — register a second environment carrying `great-expectations`, point `validate.yml` at it, and swap the `check_*` functions for a GX suite. The valid/invalid contract and the gate stay identical. That swap is itself a good exercise: it demonstrates that **the component contract is what matters, not the library inside it.**

---

## The expectations

[`src/validation/validate.py`](../src/validation/validate.py). Each source gets a suite; each row that fails gets a `_reject_reason` so rejects are explainable rather than merely counted.

| Source | Expectations |
|---|---|
| DAM / RTM | `Datetime` non-null; MCP non-null and within the **regulated 0–10,000 Rs/MWh band**; volumes non-negative; no duplicate blocks |
| Weather | `time` + `city_name` non-null; temperature in −20…60 °C; humidity 0–100; wind/cloud/rain non-negative |
| Calendar | `date` non-null; `impact` numeric in 0–10 |

Note what makes these good expectations: **every bound is defensible from the domain, not from the data.** IEX MCP is capped at Rs 10,000/MWh *by regulation*. Indian ambient temperature has never left −20…60 °C. A student who writes `mcp < df.mcp.quantile(0.99)` has written a bound that moves when the data moves — which cannot catch a systematic shift, because the shift redefines the threshold.

---

## Two numbers that must not be confused

```
--min-valid-rate 0.95     # data gate    — is this batch fit to train on?
--rmse-threshold 1000     # model gate   — is this model fit to serve?
```

Both live in [`aml/pipeline.yml`](../aml/pipeline.yml); both stop the pipeline; they answer completely different questions. Students routinely merge them into one idea of "quality". Keeping them visibly separate in one YAML is the cheapest way to teach the distinction.

---

## The layout trap (worth showing on purpose)

Validated output is partitioned so that **every date-partitioned source sits exactly one directory below `month=MM`**:

```
dam/valid/year=2025/month=03/date=2025-03-08/part-0001.parquet
rtm/valid/     ... same ...
weather/valid/ ... same ...
calendar/valid/calendar.parquet
```

This is not cosmetic. `build_features.py` (ported unchanged) globs `month=MM/**/*.parquet` **without** `recursive=True`, so `**` matches exactly one level. The first version of this component wrote weather to `month=03/part-0001.parquet` — flat — and the feature builder reported `No validated data for ['weather']` even though the file was right there.

Good teaching moment because the lesson is not "use recursive=True". It is: **a component's output layout is part of its contract.** Change it and you break a consumer that never imported your code.

---

## Build and run it

```powershell
python src/validation/validate.py --silver-dir outputs/silver --output-dir outputs/validated
```

Expected on the March 2025 batch:

```
  DAM       2,640 rows ->  2,640 valid,     0 invalid
  RTM       2,976 rows ->  2,976 valid,     0 invalid
  WEATHER  48,360 rows -> 48,360 valid,     0 invalid  (65 stations)
  CALENDAR     33 rows ->     33 valid,     0 invalid
  QUALITY GATE PASSED
```

Component definition: [`aml/components/validate.yml`](../aml/components/validate.yml)

### Demonstrate the gate failing (do this in class)

A gate nobody has seen fail is a gate nobody trusts. Corrupt one file and re-run:

```powershell
# Push DAM MCP outside the regulated band for one day
python -c "import pandas as pd; p='data/raw/dam/year=2025/month=03/date=2025-03-10/dam.csv'; d=pd.read_csv(p); d.loc[:, 'MCP (Rs/MWh) *']=99999; d.to_csv(p, index=False)"
```

Re-register as `energy_raw:2`, submit, and watch `validate` fail with `mcp_out_of_range` — and watch `train` never start. Then restore the file. **This is the single most convincing five minutes in the whole course**, because it shows the pipeline refusing to do the wrong thing on its own.

---

## What to point at in Studio

1. The **graph** — `validate` red, everything downstream grey/never-started. Failure containment, visible.
2. **Metrics** → `valid_rate_dam`, `invalid_rows_dam`, `validation_passed`. These are trendable across runs: a valid rate sliding 100% → 97% → 94% over three weeks is a data pipeline decaying upstream, visible before it breaks anything.
3. **Outputs** → `{dataset}/invalid/` — the rejected rows with `_reject_reason`, on the datastore, versioned by the run. This is the Postgres audit table's replacement, and it costs nothing to keep.

---

## Check yourself

1. Why must the gate `raise` rather than log a warning and continue?
2. `min_valid_rate` is 0.95. Your batch comes in at 0.94 because a station went offline. Is failing correct? (Defensible either way — that is the point. The number encodes a policy, so it deserves a policy discussion, not a default.)
3. Why are duplicates dropped in Module 3 rather than rejected here?

---

**Next:** [Module 5 — Feature engineering and the closed lineage chain](module_05_features.md)
