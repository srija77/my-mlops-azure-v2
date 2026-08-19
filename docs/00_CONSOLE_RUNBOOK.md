# 00 — Azure MLOps Console Runbook (Energy DAM MCP Forecasting)

**Run every stage in order.** Each stage is one console block you can paste into
PowerShell (Windows) after `az login`. Data ingestion is **skipped** — we register
the existing `data/march_2025_prepared.parquet` batch directly as the pipeline input.

| | |
|---|---|
| Subscription | `<YOUR_SUBSCRIPTION_NAME>` (`<SUBSCRIPTION_ID>`) |
| Account | `<YOUR_AZURE_ACCOUNT>` |
| Tenant | `<TENANT_ID>` |
| Resource group | `rg-energy-mlops` |
| Region | `eastus` |
| Workspace | `mlw-energy-forecast` |

> Resource names with a `<suffix>` (storage/KV/ACR/App Insights) are filled in by
> Stage 1's deploy output — see [`01_PROVISIONED_RESOURCES.md`](01_PROVISIONED_RESOURCES.md)
> for the exact names created in your subscription.

---

## Stage 0 — Sign in & select subscription  *(one-time)*

```powershell
az login --use-device-code
az account set --subscription "<SUBSCRIPTION_ID>"
az account show --query "{sub:name, state:state, user:user.name}" -o table
az extension add -n ml -y          # Azure ML CLI v2
```

---

## Stage 1 — Provision infrastructure (Bicep)  ✅ *(done in this session)*

Creates: Storage + Key Vault + App Insights + ACR + **Azure ML workspace** + CPU compute cluster.

```powershell
az group create --name rg-energy-mlops --location eastus

az deployment group create `
  --name energy-infra `
  --resource-group rg-energy-mlops `
  --template-file infra/main.bicep `
  --parameters infra/main.bicepparam `
  --query "properties.outputs"
```

Record the printed `storageNameOut`, `acrNameOut`, `workspaceNameOut`,
`computeClusterNameOut` into [`01_PROVISIONED_RESOURCES.md`](01_PROVISIONED_RESOURCES.md).

---

## Stage 2 — Set CLI defaults & point config at the workspace

```powershell
az configure --defaults group=rg-energy-mlops workspace=mlw-energy-forecast

# In config/config.yaml set:
#   azure.subscription_id: "<SUBSCRIPTION_ID>"
#   azure.acr_name:        "<acrNameOut from Stage 1>"
```

---

## Stage 3 — Register the training environment

```powershell
az ml environment create -f aml/environment/train-env.yml
```

---

## Stage 4 — Register the March 2025 batch data asset  *(ingestion skipped)*

Uploads `data/march_2025_prepared.parquet` into the workspace Blob datastore and
versions it as `march_2025_prepared:1` — the pipeline's input.

```powershell
az ml data create -f aml/data_asset.yml
az ml data show -n march_2025_prepared --version 1 -o table
```

---

## Stage 5 — Run the training pipeline (prepare → train → evaluate → promote)

Runs on `cpu-cluster` (scales 0→2). Trains 5 forecasters, registers the best to the
model registry, gates on RMSE/MAPE, and promotes to the `champion` alias if it wins.

```powershell
$run = az ml job create -f aml/pipeline.yml `
  --set inputs.input_data.path=azureml:march_2025_prepared:1 `
  --query name -o tsv
az ml job stream -n $run                     # follow logs to completion
az ml job show   -n $run --query status -o tsv
```

Verify the champion after it finishes:

```powershell
az ml model list --name dam_mcp_forecast -o table
az ml model show --name dam_mcp_forecast --label champion -o table
```

---

## Stage 6 — Deploy the champion to a Managed Online Endpoint

```powershell
az ml online-endpoint create -f aml/endpoints/endpoint.yml

az ml online-deployment create -f aml/endpoints/deployment.yml `
  --set model=azureml:dam_mcp_forecast@champion `
  --all-traffic
```

---

## Stage 7 — Invoke / smoke-test the endpoint

```powershell
az ml online-endpoint invoke `
  --name dam-mcp-endpoint `
  --request-file scoring/sample_request.json

az ml online-endpoint show -n dam-mcp-endpoint --query "scoring_uri" -o tsv
```

---

## Stage 8 — CI/CD (Azure DevOps)  *(one-time wiring, then git-push driven)*

```powershell
# Prereqs done in the DevOps UI (see 00_PORTAL_RUNBOOK.md Part 6):
#   - ARM service connection 'energy-mlops-sc' (Contributor + AzureML Data Scientist on RG)
#   - Variable group 'energy-mlops' = { resourceGroup, workspaceName }
#   - Environment 'energy-prod' with approval check
# Create the pipeline from the YAML, then every push to main runs CI -> Train -> Deploy:
#   file: azure-pipelines.yml
az devops configure --defaults organization=https://dev.azure.com/<org> project=<project>
az pipelines create --name energy-mlops --repository <repo> --branch main `
  --yml-path azure-pipelines.yml --service-connection energy-mlops-sc
```

---

## Stage 9 — Retraining (scheduled pipeline)

```powershell
# Recurring retrain: re-runs prepare->train->evaluate(gate)->promote(champion).
# CI/CD Deploy stage then ships the new champion. file: aml/pipeline.yml
az ml schedule create --name retrain-weekly `
  --set trigger.type=recurrence trigger.frequency=week trigger.interval=1 `
  --set create_job.type=pipeline create_job.job=./aml/pipeline.yml
az ml schedule list -o table
```

---

## Stage 10 — Enable model monitoring (drift / quality)

```powershell
# Needs reference data asset 'dam_mcp_reference' first. code: src/monitoring/create_reference_data.py
az ml data create --name dam_mcp_reference --version 1 --type uri_file `
  --path azureml:march_2025_prepared:1
# Daily drift + prediction + quality monitor over the champion deployment.
az ml schedule create -f aml/monitoring.yml   # file: aml/monitoring.yml
az ml schedule list -o table
```

---

## Stage 11 — Teardown  *(stop billing)*

```powershell
# Delete just the endpoint (keeps workspace/models):
az ml online-endpoint delete -n dam-mcp-endpoint --yes

# OR nuke everything provisioned in this runbook:
az group delete --name rg-energy-mlops --yes --no-wait
```

---

### Sequence at a glance

```
0 login → 1 infra(Bicep) → 2 defaults+config → 3 environment →
4 data asset (March 2025) → 5 pipeline → 6 endpoint → 7 invoke →
8 CI/CD → 9 retraining → 10 monitoring → 11 teardown
```

> Portal (no-IaC) click-through equivalent: [00_PORTAL_RUNBOOK.md](00_PORTAL_RUNBOOK.md)
