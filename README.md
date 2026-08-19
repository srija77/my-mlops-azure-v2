# Energy Market Forecasting — MLOps on Microsoft Azure

An **Azure ML-native** re-implementation of the local energy-market forecasting
MLOps pipeline, running on the **same March 2025 data**. Same modelling logic
(5 forecasters, best-by-RMSE, champion promotion), re-platformed from the
local/GCP-style stack (DVC + local MLflow + Feast/SQLite + Flask on minikube +
Prometheus/Grafana + GitHub Actions/ArgoCD) onto managed Azure services.

## Service mapping (what replaced what)

| Concern | Source project | This Azure project |
|---|---|---|
| Pipeline orchestration | DVC (`dvc.yaml`, `dvc repro`) | **Azure ML Pipeline** (`aml/pipeline.yml`, component per stage) |
| Experiment tracking | Local MLflow server (`:5000`) | **Azure ML built-in MLflow** (workspace tracking URI) |
| Model registry | MLflow registry + `champion` alias | **Azure ML Model Registry** + `champion=true` tag (AML has no alias API) |
| Data versioning | DVC + S3 remote | **Azure ML Data Assets** (versioned, Blob-backed) |
| Feature store | Feast (SQLite registry + online) | **None** — features are a versioned Azure ML **Data Asset** (`march_2025_features`). See docs/module_05_features.md for why, and what AML Managed Feature Store would require |
| Serving | Flask + gunicorn on minikube | **Azure ML Managed Online Endpoint** (`scoring/score.py`) |
| Container registry | GHCR | **Azure Container Registry** (created by Bicep) |
| CI/CD | GitHub Actions + ArgoCD | **Azure DevOps Pipelines** (`azure-pipelines.yml`) |
| Monitoring / drift | Prometheus + Grafana + Evidently | **Azure ML Model Monitoring** + Application Insights |
| Infra | docker-compose / k8s manifests | **Bicep** (`infra/main.bicep`) |

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full mapping and data flow.

## Pipeline stages (unchanged logic)

```
energy_raw (bronze Data Asset)
        │
        ▼  prepare_features   → Feast-style features parquet
        ▼  train              → 5 models, best→AML Model Registry, MLflow runs
        ▼  evaluate  (gate)   → holdout RMSE/MAPE, tag version passed_eval
        ▼  promote            → champion=true tag if it beats production
        ▼  deploy (CD)        → Managed Online Endpoint serves the champion
```

## Repository layout

```
infra/                  Bicep IaC (workspace + storage/KV/ACR/App Insights + compute)
aml/                    Azure ML control plane
  components/           one command component per pipeline stage
  environment/          training conda environment
  endpoints/            managed online endpoint + deployment
  pipeline.yml          the DVC-replacement pipeline job
  compute.yml           CPU cluster (scales to 0)
  data_asset.yml        March 2025 data registration
  monitoring.yml        data/prediction/quality drift monitor
src/                    pipeline code (component entry points)
  feature_engineering/  prepare_features.py
  train/ evaluate/ promote/ monitoring/
  common/config.py      central config + MLflow URI resolver
scoring/                score.py + conda for the online endpoint
data/                   raw/ (bronze, -> energy_raw asset) + features/
config/config.yaml      resource names, gates, asset versions
azure-pipelines.yml     Azure DevOps CI/CD (CI → Train → Deploy)
tests/                  unit tests (run in CI, no Azure needed)
docs/                   ARCHITECTURE.md, RUNBOOK.md
```

## Quickstart

Full step-by-step is in [docs/RUNBOOK.md](docs/RUNBOOK.md). The short version:

```bash
az login

# 1. Provision infrastructure
./infra/deploy.sh rg-energy-mlops eastus         # or infra/deploy.ps1 on Windows

# 2. Point config/config.yaml at the created workspace/ACR (names printed above)

# 3. Register AML assets and run the training pipeline
az extension add -n ml -y
az configure --defaults group=rg-energy-mlops workspace=mlw-energy-forecast
az ml environment create -f aml/environment/train-env.yml
az ml compute create      -f aml/compute.yml
az ml data create         -f aml/data_asset.yml
az ml job create          -f aml/pipeline.yml

# 4. Deploy the champion to a real-time endpoint
az ml online-endpoint   create -f aml/endpoints/endpoint.yml
az ml online-deployment create -f aml/endpoints/deployment.yml \
    --set model=azureml:dam_mcp_forecast:$VER --all-traffic   # $VER from the champion=true tag

# 5. Predict
az ml online-endpoint invoke -n dam-mcp-endpoint --request-file scoring/sample_request.json
```

## Run the tests locally

```bash
pip install -r environment/requirements.txt pytest ruff
pytest tests/ -q
```

> **Note:** this repo is deploy-ready but does **not** provision live Azure
> resources on its own — running the `az`/Bicep commands above bills your
> subscription. Everything is parameterised through `config/config.yaml` and the
> Bicep params so you can point it at your own subscription.
