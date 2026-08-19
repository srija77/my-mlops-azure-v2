# Runbook — deploy & run the Azure MLOps pipeline

Copy-paste commands to stand the whole thing up from scratch. Windows uses
PowerShell; the `az ml` commands are identical cross-platform.

> Everything here **bills your Azure subscription**. Tear down at the end
> (Part 6) to stop charges. The compute cluster scales to 0 when idle.

## Prerequisites
```powershell
az version
az login
az account set --subscription "<YOUR_SUBSCRIPTION_ID>"
az extension add -n ml -y
```

---

## Part 1 — Provision infrastructure (Bicep)
```powershell
# Creates RG + Storage + Key Vault + App Insights + ACR + AML workspace + compute
./infra/deploy.ps1 -ResourceGroup rg-energy-mlops -Location eastus
```
Note the `workspaceNameOut` / `acrNameOut` in the output. Put them (and your
subscription id) into [config/config.yaml](../config/config.yaml).

Set CLI defaults so later commands are short:
```powershell
az configure --defaults group=rg-energy-mlops workspace=mlw-energy-forecast
```

---

## Part 2 — Register Azure ML assets
```powershell
az ml environment create -f aml/environment/train-env.yml
az ml compute create      -f aml/compute.yml       # skip if Bicep already made it
az ml data create         -f aml/data_asset.yml    # registers march_2025_prepared:1
```

---

## Part 3 — Run the training pipeline
```powershell
$RUN = az ml job create -f aml/pipeline.yml --query name -o tsv
az ml job stream -n $RUN            # live logs: prepare -> train -> evaluate -> promote
az ml job show   -n $RUN --query status -o tsv    # expect: Completed
```
Watch it in the studio: **Jobs → dam_mcp_forecast**. After it finishes:
```powershell
# Confirm the champion was set
az ml model show --name dam_mcp_forecast --label champion --query "[name,version]" -o tsv
```

---

## Part 4 — Deploy the real-time endpoint
```powershell
az ml online-endpoint create -f aml/endpoints/endpoint.yml

az ml online-deployment create -f aml/endpoints/deployment.yml `
  --set model=azureml:dam_mcp_forecast@champion --all-traffic

# Predict on one real March 2025 feature row
az ml online-endpoint invoke -n dam-mcp-endpoint `
  --request-file scoring/sample_request.json
```
Expect JSON like `{"predictions": [2983.4], "model": "dam_mcp_forecast", "n": 1}`.

---

## Part 5 — Enable drift monitoring
```powershell
# Snapshot the baseline (run once; or add as a pipeline step)
python src/monitoring/create_reference_data.py `
  --features-dir feature_store/feature_repo/data --output-dir monitoring
az ml data create --name dam_mcp_reference --version 1 `
  --type uri_file --path monitoring/reference_data.parquet

az ml schedule create -f aml/monitoring.yml
```

---

## Part 6 — CI/CD (Azure DevOps)
1. Push this folder to an Azure Repo (or connect GitHub).
2. Create an **ARM service connection** named `energy-mlops-sc` (Contributor +
   AzureML Data Scientist on `rg-energy-mlops`).
3. Create a **variable group** `energy-mlops` with `resourceGroup` and
   `workspaceName`.
4. Create a pipeline from [azure-pipelines.yml](../azure-pipelines.yml). Add an
   **approval** on the `energy-prod` environment to gate production deploys.

A push to `main` then runs: **CI (lint+test) → Train (submit pipeline) →
Deploy (roll champion to endpoint, smoke test)**.

---

## Part 7 — Teardown
```powershell
az ml online-endpoint delete -n dam-mcp-endpoint --yes    # stops serving charges
az group delete -n rg-energy-mlops --yes --no-wait        # removes everything
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `MLFLOW_TRACKING_URI` not resolving locally | `az login`, fill `azure.*` in config.yaml, or paste `mlflow.tracking_uri` from `az ml workspace show` |
| Pipeline `evaluate` runs before `train` | Ensure the `train_signal` / `eval_signal` edges are present in `aml/pipeline.yml` |
| Endpoint deploy fails pulling image | Grant the workspace identity **AcrPull** on the ACR |
| `evaluate` exits 1 | The model failed the quality gate (RMSE/MAPE thresholds in config.yaml) — inspect eval metrics in the studio |
| Promotion `skipped` | Latest version's `passed_eval` tag is not `True`; check the evaluate step logs |
| Endpoint invoke returns an error about columns | Send the full feature set — use `scoring/sample_request.json` as the template |
