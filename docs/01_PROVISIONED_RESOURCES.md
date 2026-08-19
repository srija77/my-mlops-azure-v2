# 01 — Provisioned Azure Resources (live inventory)

Provisioned **2026-08-19** under subscription **Azure subscription 1**
(`b2acf5e5-7e53-4270-a76b-71adbc172c10`), account `srija.360digitmg@rediffmail.com`,
tenant `0c1def5c-2537-4ddb-802e-4ddc7fda6013` (`Default Directory`).

Resource group **`rg-energy-mlops`** — region **`eastus`**.

## Subscription constraints (read before changing VM sizes)

| Quota (eastus) | Limit |
|---|---|
| Total Regional vCPUs | **4** |
| Standard DSv2 Family vCPUs | **4** |
| Total Regional Low-priority vCPUs | 3 |

`Standard_DS3_v2` is 4 vCPUs — a single node would consume the entire allowance.
The cluster therefore uses **`Standard_DS2_v2` (2 vCPUs), max 2 nodes**, set in
[`infra/main.bicepparam`](../infra/main.bicepparam) and mirrored in
[`config/config.yaml`](../config/config.yaml) for the endpoint.

Resource providers registered on this subscription (all were `NotRegistered` —
a fresh subscription needs this before anything deploys):
`Microsoft.MachineLearningServices`, `Microsoft.ContainerRegistry`,
`Microsoft.KeyVault`, `Microsoft.Insights`, `Microsoft.OperationalInsights`,
`Microsoft.Network`.

## Infrastructure (Bicep — `energy-infra` deployment)

| # | Resource | Name | Type / SKU | Purpose |
|---|----------|------|------------|---------|
| 1 | Storage account | `stenergyvuzan3y2` | StorageV2 / Standard_LRS | Default datastore (data assets, job outputs, models) |
| 2 | Key Vault | `kv-energy-vuzan3y2` | standard, RBAC | Secrets / connection strings |
| 3 | Application Insights | `appi-energy-vuzan3y2` | web | Job + endpoint telemetry |
| 4 | Container Registry | `acrenergyvuzan3y2` | Standard | AML environment + endpoint images |
| 5 | Azure ML workspace | `mlw-energy-forecast` | Basic, SystemAssigned identity | MLOps control plane |
| 6 | Compute cluster | `cpu-cluster` | **Standard_DS2_v2**, 0→2 nodes | Runs the pipeline (scales to 0) |

## AML control-plane assets

| Asset | Name : version | Created by |
|-------|----------------|------------|
| Environment | `energy-train-env:4` | [`aml/environment/train-env.yml`](../aml/environment/train-env.yml) |
| Data asset (bronze) | `energy_raw:1` (uri_folder, 9.24 MB) | [`aml/data_assets/01_raw_energy.yml`](../aml/data_assets/01_raw_energy.yml) |
| Pipeline job | `olive_moon_llsz5zb736` | [`aml/pipeline.yml`](../aml/pipeline.yml) — 7 stages, ingest→promote |

> **`energy-train-env:4` matters.** Versions 1–3 in the previous subscription had no
> `setuptools` in the conda env, so `import mlflow` died with
> `ModuleNotFoundError: No module named 'pkg_resources'` and every training run failed.
> Fixed in [`aml/environment/conda.yml`](../aml/environment/conda.yml) with `setuptools<81`.

## Source control / CI-CD

| Item | Value |
|---|---|
| Repository | https://github.com/srija77/my-mlops-azure-v2 (**private**) |
| Workflow | [`.github/workflows/mlops.yml`](../.github/workflows/mlops.yml) — ci → train → deploy |
| Environment | `production` (created; **no approval gate** — required reviewers need a public repo or a paid plan on GitHub Free) |
| Secret needed | `AZURE_CREDENTIALS` — output of `az ad sp create-for-rbac ... --json-auth` |

## Studio links

- **Workspace:** https://ml.azure.com/?wsid=/subscriptions/b2acf5e5-7e53-4270-a76b-71adbc172c10/resourcegroups/rg-energy-mlops/workspaces/mlw-energy-forecast&tid=0c1def5c-2537-4ddb-802e-4ddc7fda6013
- **Pipeline run:** https://ml.azure.com/runs/olive_moon_llsz5zb736?wsid=/subscriptions/b2acf5e5-7e53-4270-a76b-71adbc172c10/resourcegroups/rg-energy-mlops/workspaces/mlw-energy-forecast&tid=0c1def5c-2537-4ddb-802e-4ddc7fda6013

## Cost notes

- Compute cluster is **$0 while idle** (scales to 0 nodes).
- Ongoing floor: ACR Standard, Key Vault, App Insights ingestion, Storage — cents/day.
- An online endpoint holds **1× Standard_DS2_v2 always-on** while deployed — the biggest
  running cost. Delete it when not demoing.

## Teardown

```powershell
az ml online-endpoint delete -n dam-mcp-endpoint --yes   # stop endpoint cost only
az group delete --name rg-energy-mlops --yes --no-wait   # remove EVERYTHING
```
