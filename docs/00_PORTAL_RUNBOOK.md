# 00 — Azure Portal Runbook (No IaC) — Energy DAM MCP MLOps

Deploy the **entire MLOps stack by clicking**, no Bicep/CLI.
Portal = https://portal.azure.com · ML Studio = https://ml.azure.com
`# file:` comments show which repo file holds the same config/code.

---

## PART 1 — Provision infrastructure (portal.azure.com)

### 1. Resource group
1. Portal → search **Resource groups** → **+ Create**.
2. Subscription **AiSPRY** · Name **`rg-energy-mlops`** · Region **East US** → **Review + create** → **Create**.

### 2. Storage account
1. Portal → **Storage accounts** → **+ Create**.
2. RG **rg-energy-mlops** · Name **`stenergymlops`** · Region **East US** · Primary service **Azure Blob** · Redundancy **LRS**.
3. Security tab → **Disable** public blob access · Min TLS **1.2** → **Review + create** → **Create**.

### 3. Key Vault
1. Portal → **Key vaults** → **+ Create**.
2. RG **rg-energy-mlops** · Name **`kv-energy-mlops`** · Region **East US** · Pricing **Standard**.
3. Access config → **Azure role-based access control** → **Review + create** → **Create**.

### 4. Application Insights
1. Portal → **Application Insights** → **+ Create**.
2. RG **rg-energy-mlops** · Name **`appi-energy-mlops`** · Region **East US** · Mode **Workspace-based** → **Review + create** → **Create**.

### 5. Container Registry
1. Portal → **Container registries** → **+ Create**.
2. RG **rg-energy-mlops** · Name **`acrenergymlops`** · Region **East US** · SKU **Standard** → **Review + create** → **Create**.
3. After create → **Settings → Access keys** → toggle **Admin user = On**.

### 6. Azure ML workspace
1. Portal → **Azure Machine Learning** → **+ Create**.
2. RG **rg-energy-mlops** · Name **`mlw-energy-forecast`** · Region **East US**.
3. Link the four above: Storage **stenergymlops** · Key vault **kv-energy-mlops** · App Insights **appi-energy-mlops** · Container registry **acrenergymlops** → **Review + create** → **Create**.
4. When done → **Launch studio** (opens ml.azure.com).

### 7. Compute cluster  *(ML Studio)*
1. Studio → **Compute → Compute clusters → + New**.
2. Location **East US** · VM tier **Dedicated** · VM size **Standard_DS3_v2** → **Next**.
3. Name **`cpu-cluster`** · Min nodes **0** · Max nodes **2** · Idle seconds **180** → **Create**.
   `# file: aml/compute.yml`

---

## PART 2 — Data (ingestion skipped: register the March 2025 batch)

### 8. Register data asset  *(ML Studio)*
1. Studio → **Data → Data assets → + Create**.
2. Name **`march_2025_prepared`** · Type **File (uri_file)** → **Next**.
3. Source **From local files** → upload **`data/march_2025_prepared.parquet`** → datastore **workspaceblobstore** → **Create**.
   `# file: aml/data_asset.yml` · `# data: data/march_2025_prepared.parquet`

---

## PART 3 — Training environment

### 9. Register environment  *(ML Studio)*
1. Studio → **Environments → Custom environments → + Create**.
2. Name **`energy-train-env`** · Source **Existing docker image + conda**.
3. Image **`mcr.microsoft.com/azureml/openmpi4.1.0-ubuntu22.04:latest`**.
4. Paste conda spec → **Create**.
   `# file: aml/environment/train-env.yml` · `# conda: aml/environment/conda.yml`

---

## PART 4 — Training pipeline (prepare → train → evaluate → promote)

### 10. Create pipeline job  *(ML Studio Designer or Jobs)*
Portal has no native "submit YAML pipeline" button; use the Studio **Jobs** import:
1. Studio → **Jobs → + Create → Pipeline job** (Designer) — add one **command component** per stage, in order:
   `# components: aml/components/prepare_features.yml, train.yml, evaluate.yml, promote.yml`
2. Wire: prepare.output→train.features ; train.model→evaluate.train_signal ; evaluate.eval_metrics→promote.eval_signal.
   `# file: aml/pipeline.yml` (exact graph + inputs)
3. Pipeline input **input_data** = data asset **`march_2025_prepared:1`**.
4. Set params: registered_model_name **`dam_mcp_forecast`** · rmse_threshold **1000** · mape_threshold **15**.
5. Default compute **cpu-cluster** · Experiment name **`dam_mcp_forecast`** → **Submit**.
6. Watch **Jobs → dam_mcp_forecast** until all 4 stages = **Completed**.
   `# entry code: src/feature_engineering, src/train, src/evaluate, src/promote`

### 11. Confirm model + champion alias  *(ML Studio)*
1. Studio → **Models → `dam_mcp_forecast`** → newest version present.
2. Open version → **Tags** shows `passed_eval`; **Aliases** shows **`champion`** (set by the promote stage).
   `# logic: src/promote/promote.py, src/evaluate/evaluate.py`

---

## PART 5 — Real-time serving (Managed Online Endpoint)

### 12. Create endpoint  *(ML Studio)*
1. Studio → **Endpoints → Real-time endpoints → + Create**.
2. Model **`dam_mcp_forecast`** alias **champion** → **Next**.
3. Endpoint name **`dam-mcp-endpoint`** · Auth **Key** → **Next**.
   `# file: aml/endpoints/endpoint.yml`

### 13. Create deployment
1. Deployment name **`champion`** · Env **`dam-mcp-scoring-env`** (image + conda below) · Scoring script **`score.py`**.
   `# file: aml/endpoints/deployment.yml` · `# code: scoring/score.py` · `# conda: scoring/conda.yml`
2. VM **Standard_DS3_v2** · Instance count **1** · Traffic **100%** → **Create** (wait ~10 min → **Healthy**).
3. Endpoint → **Deployment → Data collection**: enable inputs + outputs (needed for monitoring).

### 14. Test  *(ML Studio)*
1. Endpoint → **Test** tab → paste request body → **Test**.
   `# file: scoring/sample_request.json`
2. Copy **Consume → REST endpoint** + **Primary key** for external callers.

---

## PART 6 — CI/CD (Azure DevOps — dev.azure.com)

### 15. Service connection
1. dev.azure.com → your project → **Project settings → Service connections → New → Azure Resource Manager → Workload identity/Service principal (automatic)**.
2. Scope **rg-energy-mlops** · Name **`energy-mlops-sc`** → Save.
3. Portal → **rg-energy-mlops → Access control (IAM)**: grant that SP **Contributor** + **AzureML Data Scientist**.

### 16. Variable group
1. DevOps → **Pipelines → Library → + Variable group** name **`energy-mlops`**.
2. Add vars: `resourceGroup=rg-energy-mlops` · `workspaceName=mlw-energy-forecast` → Save.

### 17. Create pipeline
1. DevOps → **Pipelines → New pipeline → GitHub/Azure Repos** → select this repo → **Existing YAML file**.
2. Path **`/azure-pipelines.yml`** → **Continue**.
   `# file: azure-pipelines.yml` (stages: CI → Train → Deploy)
3. **Run**. Stages: **CI** (ruff+pytest) → **Train** (submits pipeline) → **Deploy** (rolls champion to endpoint).

### 18. Deploy approval gate
1. DevOps → **Pipelines → Environments → + New environment** name **`energy-prod`**.
2. Environment → **Approvals and checks → + Approvals** → add approver → Save.
   `# ref: azure-pipelines.yml stage 'Deploy' -> environment: energy-prod`

---

## PART 7 — Retraining (scheduled pipeline)

### 19. Schedule the training pipeline  *(ML Studio)*
1. Studio → **Jobs → dam_mcp_forecast** → open a completed pipeline run → **Schedule → Create schedule**.
2. Name **`retrain-weekly`** · Recurrence **Weekly**, e.g. Mon 02:00 · Status **Enabled** → **Create**.
   `# equivalent CLI: az ml schedule create (recurrence on aml/pipeline.yml)`
3. Retrain path is automatic: each run re-runs evaluate (gate) → promote (champion alias) → CI/CD Deploy stage ships the new champion.

---

## PART 8 — Monitoring & drift → auto-retrain

### 20. Reference data for monitor
1. Studio → **Data → + Create** → name **`dam_mcp_reference`** (uri_file) from the March 2025 baseline.
   `# code: src/monitoring/create_reference_data.py`

### 21. Create monitor  *(ML Studio)*
1. Studio → **Monitoring → + Add** (or **Jobs → Monitoring**).
2. Deployment **`dam-mcp-endpoint : champion`** · Task **Regression**.
3. Signals: **Data drift**, **Prediction drift**, **Data quality** · Reference **`dam_mcp_reference:1`** · Threshold JS distance **0.1**.
4. Compute **Standard_E4s_v3** · Schedule **Daily 03:00** · Alert email → **Create**.
   `# file: aml/monitoring.yml`

### 22. Alerts / dashboards
1. Portal → **appi-energy-mlops → Alerts → + Create → Alert rule** for endpoint failures/latency.
2. Portal → **Monitor → Workbooks** for endpoint traffic dashboards.
3. Drift breach → email fires → trigger **retrain-weekly** manually or let the weekly schedule pick it up.

---

## PART 9 — Teardown (stop billing)

### 23. Endpoint only (biggest running cost)
- Studio → **Endpoints → dam-mcp-endpoint → Delete**.

### 24. Everything
- Portal → **rg-energy-mlops → Delete resource group** → type name → **Delete**.

---

### Sequence
```
1 RG → 2 Storage → 3 KeyVault → 4 AppInsights → 5 ACR → 6 Workspace → 7 Compute
→ 8 Data → 9 Environment → 10 Pipeline → 11 Model/champion
→ 12 Endpoint → 13 Deployment → 14 Test
→ 15 SvcConn → 16 VarGroup → 17 Pipeline(CI/CD) → 18 Approval
→ 19 Retrain schedule → 20 RefData → 21 Monitor → 22 Alerts
→ 23/24 Teardown
```
> CLI equivalent of every step: [00_CONSOLE_RUNBOOK.md](00_CONSOLE_RUNBOOK.md) · live resources: [01_PROVISIONED_RESOURCES.md](01_PROVISIONED_RESOURCES.md)
