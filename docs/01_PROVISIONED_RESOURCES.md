# 01 — Provisioned Azure Resources (live inventory)

Provisioned **2026-07-19** under subscription **AiSPRY** (`c93ce21f-642b-4c78-b925-1a29b6046e9d`),
account `projectlead@aispry.com`, tenant `e4a31943-da03-4fc3-aaf9-f2788a61f796`.

Resource group **`rg-energy-mlops`** — region **`eastus`**.

## Infrastructure (Bicep — `energy-infra` deployment)

| # | Resource | Name | Type / SKU | Purpose |
|---|----------|------|------------|---------|
| 1 | Storage account | `stenergygqjy6fau` | StorageV2 / Standard_LRS | Default datastore (data assets, job outputs, models) |
| 2 | Key Vault | `kv-energy-gqjy6fau` | standard, RBAC | Secrets / connection strings |
| 3 | Application Insights | `appi-energy-gqjy6fau` | web | Job + endpoint telemetry, monitoring |
| 4 | Container Registry | `acrenergygqjy6fau` | Standard (admin on) | AML environment + endpoint images |
| 5 | Azure ML workspace | `mlw-energy-forecast` | Basic, SystemAssigned identity | MLOps hub (tracking + registry + endpoints) |
| 6 | Compute cluster | `cpu-cluster` | Standard_DS3_v2, 0→2 nodes, 3 min idle | Runs the training pipeline (scales to 0) |

## AML control-plane assets (registered via `az ml`)

| # | Asset | Name : version | Created by |
|---|-------|----------------|------------|
| 3 | Environment | `energy-train-env:1` | `aml/environment/train-env.yml` |
| 4 | Data asset (March 2025 batch) | `march_2025_prepared:1` (uri_file) | `aml/data_asset.yml` — **ingestion skipped**, parquet uploaded to Blob |
| 5 | Pipeline job | `jovial_okra_q4t616z1q3` | `aml/pipeline.yml` (prepare→train→evaluate→promote) |
| 5 | Registered model | `dam_mcp_forecast` + `champion` alias | pipeline `train`/`promote` stages |
| 6 | Online endpoint | `dam-mcp-endpoint` (key auth) | `aml/endpoints/endpoint.yml` |
| 6 | Online deployment | `champion` (Standard_DS3_v2 ×1) | `aml/endpoints/deployment.yml` |
| 8 | Monitoring schedule | drift/quality monitor | `aml/monitoring.yml` |

## Studio links

- **Workspace:** https://ml.azure.com/?wsid=/subscriptions/c93ce21f-642b-4c78-b925-1a29b6046e9d/resourcegroups/rg-energy-mlops/workspaces/mlw-energy-forecast&tid=e4a31943-da03-4fc3-aaf9-f2788a61f796
- **Pipeline run:** https://ml.azure.com/runs/jovial_okra_q4t616z1q3?wsid=/subscriptions/c93ce21f-642b-4c78-b925-1a29b6046e9d/resourcegroups/rg-energy-mlops/workspaces/mlw-energy-forecast&tid=e4a31943-da03-4fc3-aaf9-f2788a61f796

## Cost notes

- Compute cluster is **$0 while idle** (scales to 0 nodes after 3 min).
- Ongoing floor: ACR Standard (~$0.67/day), Key Vault, App Insights ingestion, Storage.
- The **online endpoint holds 1× Standard_DS3_v2 always-on** while deployed — the
  biggest running cost. Delete it (Stage 9) when not demoing.

## Teardown

```powershell
az ml online-endpoint delete -n dam-mcp-endpoint --yes   # stop endpoint cost only
az group delete --name rg-energy-mlops --yes --no-wait    # remove EVERYTHING
```
