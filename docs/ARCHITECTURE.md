# Architecture — Azure MLOps for Energy Market Forecasting

This document maps the source (local/GCP-style) MLOps stack onto Azure services
and explains the data + control flow.

## End-to-end flow

```mermaid
flowchart TD
    subgraph Source["Azure ML Data Asset"]
      A[march_2025_prepared.parquet<br/>Blob-backed, versioned]
    end

    subgraph Pipeline["Azure ML Pipeline (aml/pipeline.yml) on cpu-cluster"]
      P1[prepare_features] --> P2[train<br/>5 models]
      P2 --> P3[evaluate<br/>quality gate]
      P3 --> P4[promote<br/>champion alias]
    end

    subgraph Hub["Azure ML Workspace"]
      MLF[(MLflow tracking<br/>runs + metrics + plots)]
      REG[(Model Registry<br/>dam_mcp_forecast<br/>@champion)]
    end

    subgraph Serve["Managed Online Endpoint"]
      EP[dam-mcp-endpoint<br/>scoring/score.py]
    end

    subgraph Ops["Monitoring"]
      MON[Model Monitoring<br/>data + prediction drift]
      AI[Application Insights]
    end

    A --> P1
    P2 -. logs runs .-> MLF
    P2 -. registers .-> REG
    P3 -. tags version .-> REG
    P4 -. sets @champion .-> REG
    REG -->|@champion| EP
    EP -. telemetry .-> AI
    EP -. inference data .-> MON
    A -. baseline .-> MON

    subgraph CICD["Azure DevOps (azure-pipelines.yml)"]
      CI[CI: lint+test] --> TR[Train stage:<br/>submit pipeline] --> DP[Deploy stage:<br/>roll champion]
    end
    TR --> Pipeline
    DP --> EP
```

## Component-by-component mapping

### Orchestration — DVC → Azure ML Pipeline
The source `dvc.yaml` declared stages (`prepare_feast_data → train → evaluate →
promote`) with file deps/outs and `dvc repro` for incremental runs. Here each
stage is an **Azure ML command component** (`aml/components/*.yml`) wired into a
**pipeline job** (`aml/pipeline.yml`). AML gives the same DAG + caching
(`force_rerun: false` reuses unchanged step outputs) but runs on managed compute
and records full lineage in the studio.

Because `train` writes to the *registry* (not a file the next step reads),
`evaluate` and `promote` are ordered with explicit **signal edges** — they
consume the prior step's output folder purely to force execution order (see the
`--train-signal` / `--eval-signal` passthrough args).

### Tracking & registry — local MLflow → Azure ML
`train.py` still calls `mlflow.log_*` and `mlflow.register_model`, but inside an
AML job `MLFLOW_TRACKING_URI` points at the **workspace** automatically, so runs
land in Azure ML Studio and versions land in the **Azure ML Model Registry**.
Promotion uses the MLflow **alias** `champion` (the registry's production
pointer), matching the source's alias-based promotion.

### Data versioning — DVC+S3 → Data Assets
The March 2025 parquet is registered as a versioned **Data Asset**
(`aml/data_asset.yml`). Bumping `--version` creates an immutable new snapshot,
the same guarantee DVC gave over S3.

### Feature store — Feast/SQLite → Feast/Azure
`feature_definitions.py` is copied verbatim (same `dam_mcp_forecast_v1`
FeatureService). `feature_store.yaml` swaps the local SQLite registry/online
store for a **Blob registry + Azure Cache for Redis** online store. Training
reads the offline parquet for reproducibility; serving would hit Redis.

### Serving — Flask/minikube → Managed Online Endpoint
The Flask `/predict` route becomes `scoring/score.py` with `init()`/`run()`.
Azure ML runs the web server, TLS, autoscaling, health probes, and rolling
deploys — replacing the hand-rolled gunicorn + k8s Deployment + ArgoCD sync.
The endpoint always serves whatever the `@champion` alias points to.

### CI/CD — GitHub Actions/ArgoCD → Azure DevOps
`azure-pipelines.yml` has three stages: **CI** (ruff + pytest, like the source
`test` job), **Train** (register assets + submit the AML pipeline + gate on
completion, like the DVC `train` job), and **Deploy** (roll the champion to the
endpoint behind an approval `environment`, replacing the GitOps `build-push` +
`deploy` + ArgoCD chain).

### Monitoring — Prometheus/Grafana/Evidently → Azure ML Model Monitoring
`aml/monitoring.yml` schedules a daily monitor computing **data drift**,
**prediction drift**, and **data quality** against the March 2025 training
baseline (`src/monitoring/create_reference_data.py`), emailing on breach.
Endpoint request/latency telemetry flows to **Application Insights**. This
replaces the Prometheus scrape + Grafana dashboards + Evidently drift report,
and the drift signal can trigger a retrain (re-run the pipeline) — the analogue
of the source `retrain-on-drift.yml`.

## Security & identity notes
- The workspace uses a **system-assigned managed identity**; grant it AcrPull +
  Storage Blob Data Contributor (Bicep wires the resource links).
- CI/CD authenticates via an **ARM service connection** (OIDC/workload identity
  federation recommended) — no long-lived secrets in the repo.
- Secrets (Redis connection, etc.) belong in **Key Vault**, referenced by env,
  never committed.
