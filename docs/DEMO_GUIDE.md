# Azure MLOps — Complete Demo Guide

**Build the entire thing from an empty subscription, in front of an audience.**

Every step below is either a **portal click path** or a **copy-paste command**. Nothing is assumed. Expected output is shown for each step so you always know whether it worked.

---

## Contents

| Part | What you do | Where | Time |
|---|---|---|---|
| [0](#part-0--before-the-demo) | Preparation (do this the day before) | — | 30 min |
| [1](#part-1--create-the-resource-group) | Create the resource group | Portal | 2 min |
| [2](#part-2--create-the-azure-ml-workspace) | Create the Azure ML workspace | Portal | 5 min |
| [3](#part-3--create-the-compute-cluster) | Create the compute cluster | Studio | 3 min |
| [4](#part-4--get-the-code) | Get the code onto your machine | CLI | 2 min |
| [5](#part-5--register-the-data-portal-upload) | Register the raw data | **Studio upload** | 5 min |
| [6](#part-6--register-the-environment) | Register the training environment | CLI | 8 min |
| [7](#part-7--run-the-pipeline) | Run the 7-stage pipeline | CLI → Studio | 12 min |
| [8](#part-8--explore-the-results-in-studio) | Explore results | Studio | 8 min |
| [9](#part-9--break-it-on-purpose-) | **Break the pipeline on purpose** | CLI → Studio | 10 min |
| [10](#part-10--model-registry--champion) | Model registry + champion | Studio | 5 min |
| [11](#part-11--deploy-the-endpoint) | Deploy + test the endpoint | Studio | 12 min |
| [12](#part-12--cicd-with-github-actions) | CI/CD with approval gate | GitHub | 10 min |
| [13](#part-13--teardown) | Teardown | Portal | 2 min |

**Live demo length:** Parts 1–12 ≈ 75 min. For a 45-min slot, do Part 0 in advance and skip Parts 6–7 (show the pre-run pipeline instead).

---

## Your environment — fill this in first

> ⚠️ **Every `<PLACEHOLDER>` below is masked on purpose.** This document is in a
> public repository, so no real subscription IDs, tenant IDs, account names, or
> resource names appear in it. Fill the table in on your own copy before the demo —
> **do not commit the filled-in version.**

| Placeholder | Where to get it | Your value |
|---|---|---|
| `<YOUR_AZURE_ACCOUNT>` | the email you sign into Azure with | |
| `<YOUR_SUBSCRIPTION_NAME>` | `az account show --query name -o tsv` | |
| `<SUBSCRIPTION_ID>` | `az account show --query id -o tsv` | |
| `<TENANT_ID>` | `az account show --query tenantId -o tsv` | |
| `<GITHUB_USER>` | your GitHub username | |
| `<GITHUB_REPO>` | the repo holding this code | |
| `<SUFFIX>` | the random suffix Azure appends to storage/KV/ACR names — read it off the resource group after Part 2 | |
| `<YOU>` | your Windows username (in file paths) | |

Print all four Azure values at once:

```powershell
az account show --query "{account:user.name, subscription:name, subscriptionId:id, tenant:tenantId}" -o table
```

**Fixed values used throughout this guide** (safe to keep as-is):

| | |
|---|---|
| Region | **East US** |
| Resource group | `rg-energy-demo` |
| Workspace | `mlw-energy-demo` |
| Compute cluster | `cpu-cluster` |
| Model | `dam_mcp_forecast` |
| Endpoint | `dam-mcp-endpoint` |

> ### ⚠️ Quota — read this before changing any VM size
> This subscription allows **4 Total Regional vCPUs**. That is the single most
> important constraint in this demo.
> - `Standard_DS2_v2` = **2 vCPUs** ✅ use this
> - `Standard_DS3_v2` = **4 vCPUs** ❌ one node eats your entire quota
>
> Cluster (2 nodes × 2 vCPU) = 4 vCPU = your whole allowance. If you also want a
> **Compute Instance** for the Studio terminal, set the cluster to **max 1 node** first.

---

# Part 0 — Before the demo

**Do this the day before. It removes every slow step from the live run.**

### 0.1 Install the tools

```powershell
# Azure CLI — check it exists
az version

# Azure ML extension (required for every `az ml` command)
az extension add -n ml -y
```

Expected: `"azure-cli": "2.85.0"` or later, and `ml` listed under extensions.

### 0.2 Fix the login problem before it happens

Windows uses a native account picker (WAM) that often opens *behind* your terminal and looks like a hang. Turn it off once:

```powershell
az config set core.enable_broker_on_windows=false
```

Then sign in — this now opens a normal browser tab:

```powershell
az login
```

Verify:

```powershell
az account show --output table
```

Expected:
```
EnvironmentName    Name                  State    IsDefault
-----------------  --------------------  -------  -----------
AzureCloud         <YOUR_SUBSCRIPTION_NAME>  Enabled  True
```

> **If `az login` hangs at "Select the account you want to log in with":** press
> `Ctrl+C`, run the `az config set` line above, and try again.
>
> **If you get `No subscriptions found`:** you signed in with the wrong account.
> Sign out at https://login.microsoftonline.com/logout, or use an InPrivate window.

### 0.3 Register resource providers (a fresh subscription has none)

```powershell
az provider register -n Microsoft.MachineLearningServices
az provider register -n Microsoft.ContainerRegistry
az provider register -n Microsoft.KeyVault
az provider register -n Microsoft.Insights
az provider register -n Microsoft.OperationalInsights
az provider register -n Microsoft.Network
```

Check (takes 2–5 min to flip to `Registered`):

```powershell
az provider show -n Microsoft.MachineLearningServices --query registrationState -o tsv
```

Expected: `Registered`

> **Why this matters:** without it, workspace creation fails with a confusing
> `MissingSubscriptionRegistration` error. It is the #1 first-time Azure ML blocker.

### 0.4 Pre-warm (the single best thing you can do)

Run the whole pipeline once, the day before. This builds the environment Docker image in ACR (~8 min, once ever) so your live run starts in seconds instead of minutes.

### 0.5 Cost check

| Resource | Cost |
|---|---|
| Compute cluster idle | **$0** (scales to 0 nodes) |
| Compute cluster running | ~$0.10/hour per DS2_v2 node |
| ACR, Key Vault, Storage, App Insights | cents/day |
| **Online endpoint** | **~$0.10/hour, always on** ← delete after the demo |

---

# Part 1 — Create the resource group

### 🖱️ Portal

1. Go to **https://portal.azure.com**
2. In the top search bar type **`Resource groups`** → click it
3. Click **+ Create**
4. Fill in:
   - **Subscription:** `<YOUR_SUBSCRIPTION_NAME>`
   - **Resource group:** `rg-energy-demo`
   - **Region:** `(US) East US`
5. Click **Review + create** → **Create**

✅ **Verify:** the resource group appears in the list, and is empty.

### 💬 Say this

> "A resource group is a folder with a lifecycle. Everything for this project goes
> in one, so at the end I delete one thing and the whole environment is gone —
> no orphaned storage account billing me for three months."

---

# Part 2 — Create the Azure ML workspace

### 🖱️ Portal

1. Search bar → **`Azure Machine Learning`** → click it
2. Click **+ Create** → **New workspace**
3. **Basics** tab:
   - **Subscription:** `<YOUR_SUBSCRIPTION_NAME>`
   - **Resource group:** `rg-energy-demo`
   - **Workspace name:** `mlw-energy-demo`
   - **Region:** `East US`
   - Leave **Storage account**, **Key vault**, **Application insights**, **Container registry** on their auto-created defaults
4. Click **Review + create** → **Create**
5. Wait ~3–5 minutes. Click **Go to resource** when done.

✅ **Verify:** open `rg-energy-demo` — it now contains **5 resources**.

### 💬 Say this — this is the key teaching moment of Part 2

> "I asked for one thing and got five. An Azure ML workspace is never alone:
>
> - **Storage account** — every dataset, job output, and model artifact
> - **Key Vault** — secrets and connection strings
> - **Container Registry** — the Docker images your jobs run inside
> - **Application Insights** — job and endpoint telemetry
>
> The workspace itself is just the control plane that ties them together. If you
> ever wonder 'where does Azure ML actually put my data?' — it's that storage
> account, and you can browse it."

---

# Part 3 — Create the compute cluster

### 🖱️ Azure ML Studio

1. From the workspace, click **Launch studio** (or go to **https://ml.azure.com**)
2. Left menu → **Compute** → **Compute clusters** tab → **+ New**
3. **Virtual machine** step:
   - **Location:** `East US`
   - **Virtual machine tier:** `Dedicated`
   - **Virtual machine type:** `CPU`
   - **Virtual machine size:** click **Select from all options**, search **`DS2_v2`**, choose **`Standard_DS2_v2`** (2 cores, 7 GB RAM)
   - Click **Next**
4. **Advanced Settings** step:
   - **Compute name:** `cpu-cluster`
   - **Minimum number of nodes:** `0`
   - **Maximum number of nodes:** `2`
   - **Idle seconds before scale down:** `180`
   - Click **Create**

✅ **Verify:** `cpu-cluster` appears with state **Succeeded** and **0 running nodes**.

### 💬 Say this

> "Minimum nodes **zero** is the whole point of managed compute. This cluster costs
> nothing right now. When I submit a job it provisions a node in about two minutes;
> three minutes after the job ends it scales back to zero. Compare that to a VM you
> forgot to turn off."

> ⚠️ **Do not pick DS3_v2.** It is 4 vCPUs — this subscription's entire quota —
> and the endpoint in Part 11 would then have nowhere to run.

---

# Part 4 — Get the code

All the pipeline code lives in the GitHub repository. Clone it once:

```powershell
cd C:\Users\<YOU>
git clone https://github.com/<GITHUB_USER>/<GITHUB_REPO>.git
cd my-mlops-azure-v2
```

Point the CLI at your new workspace so you don't repeat it in every command:

```powershell
az configure --defaults group=rg-energy-demo workspace=mlw-energy-demo
```

✅ **Verify:**

```powershell
az ml workspace show --query name -o tsv
```
Expected: `mlw-energy-demo`

### What's in the repo

```
aml/                    Azure ML control plane
  components/           one YAML per pipeline stage (ingest, validate, ...)
  data_assets/          data asset definitions
  environment/          conda environment for training
  endpoints/            online endpoint + deployment
  pipeline.yml          the 7-stage pipeline
src/                    the Python each component runs
  ingestion/            bronze -> silver
  validation/           silver -> validated + QUALITY GATE
  feature_engineering/  validated -> features
  train/ evaluate/ promote/
data/raw/               the March 2025 source data (9.2 MB)
scoring/score.py        what the online endpoint runs
docs/                   this guide + teaching modules
```

---

# Part 5 — Register the data (portal upload)

**This part answers "can I do it without the CLI?" — yes, and it's the better demo.**

### 🖱️ Azure ML Studio — upload through the browser

1. Left menu → **Data** → **Data assets** tab → **+ Create**
2. **Data type** step:
   - **Name:** `energy_raw`
   - **Description:** `Bronze layer — DAM/RTM/weather/calendar/generation, March 2025`
   - **Type:** select **`Folder (uri_folder)`** ← important, not File
   - **Next**
3. **Data source** step: choose **`From local files`** → **Next**
4. **Destination storage type**: keep **`workspaceblobstore`** (the default) → **Next**
5. **File or folder selection**: click **Upload** → **Upload folder** → browse to
   `C:\Users\<YOU>\my-mlops-azure-v2\data\raw` → select it → **Upload**
   - 162 files, ~9.2 MB. Takes about a minute.
6. **Next** → **Create**

✅ **Verify:** `energy_raw` appears with **Version 1**. Click it → **Explore** tab → you can browse `dam/`, `rtm/`, `weather/`, `calendar/`, `generation/`.

<details>
<summary><b>CLI alternative</b> (faster, if you prefer)</summary>

```powershell
az ml data create -f aml/data_assets/01_raw_energy.yml
```
</details>

### 💬 Say this — the most important idea in the whole demo

> "Look at the **URI** on this asset:
>
> `...workspaceblobstore/paths/LocalUpload/d990f879cbb669...d15d544d/raw/`
>
> That long hex string is a **content hash**. Three things follow from it:
>
> 1. **A data asset doesn't contain data — it contains the *address* of data, plus a version.**
> 2. Because the path is derived from the content, **version 1 is immutable**. If I
>    overwrite these files tomorrow, `energy_raw:1` still points at what March 2025
>    actually looked like.
> 3. Try to re-register version 1 with different content and Azure **refuses**. That
>    refusal is the feature. It's how you can still reproduce a model from six months ago."

### 💬 Then point at the file counts

> "March has 31 days. DAM has **28 files**, starting at midday on the 4th. Four days
> were never scraped. Nothing in a notebook would have told you that — but it's the
> first thing you see when your data is a registered asset."

---

# Part 6 — Register the environment

An **Environment** is the Docker image your job runs in — pinned Python and pinned libraries.

```powershell
az ml environment create -f aml/environment/train-env.yml
```

Expected output ends with:
```
name: energy-train-env
version: 4
```

✅ **Verify in Studio:** **Environments** → **Custom environments** → `energy-train-env` version 4.

> ⏱️ **First run takes ~8 minutes** — Azure is building a Docker image in your ACR.
> This is why Part 0.4 says pre-warm. It only ever happens once per environment version.

### 💬 Say this

> "This is the half of reproducibility that people forget. Versioning your data and
> your code means nothing if the library versions drift. This environment is a
> pinned conda spec baked into an image in my container registry.
>
> A real example from building this project: the environment was missing `setuptools`.
> `import mlflow` needs `pkg_resources`, which ships with setuptools. **Every single
> training run failed for a month** with a stack trace that pointed at mlflow and had
> nothing to do with mlflow. Pinning the environment is not bureaucracy."

---

# Part 7 — Run the pipeline

```powershell
az ml job create -f aml/pipeline.yml
```

Expected: prints a job name like `olive_moon_llsz5zb736` and `status: NotStarted`.

**Immediately switch to the browser** — this is a visual moment:

### 🖱️ Studio

1. Left menu → **Jobs**
2. Click the experiment **`dam_mcp_forecast`**
3. Click the newest run
4. You are looking at the **pipeline graph**. Leave it on screen — nodes turn green in sequence.

```
energy_raw → ingest → validate → build_features → prepare → train → evaluate → promote
```

⏱️ **~10 minutes** once the image is cached (first node takes ~2 min to provision).

### 💬 Say this while it runs

> "**This graph is the deliverable.** Not the model — the graph. The model is an
> output; the graph is the thing my team can re-run, schedule, audit, and hand to
> someone else.
>
> Click any node and open the **Code** tab: that's the exact source snapshot that ran.
> Not 'the code as it is today' — the code as it was *when this ran*. Combine that
> with the immutable data version and you have genuine reproducibility.
>
> Compare with the version of this project that ran locally with DVC. There,
> dependencies were file paths on one laptop, and the command ran in whatever Python
> happened to be installed. Both of those weak links are gone: paths became typed
> ports the platform mounts, and 'whatever Python' became a pinned image."

---

# Part 8 — Explore the results in Studio

### 8.1 Metrics on a *data* stage

🖱️ Click the **`ingest`** node → **Metrics** tab

| Metric | Value |
|---|---|
| `ingest_rows_dam` | 2,640 |
| `ingest_dupes_dam` | **2,639** |
| `ingest_rows_weather` | 48,360 |

> **This is a real defect the pipeline found.** The DAM source files contain every
> 15-minute block **exactly twice** — 192 rows a day for 96 real blocks, byte-identical.
> The scraper wrote each table twice and nobody noticed.
>
> Most people think of metrics as a model thing. Correct that instinct: **the earlier
> a number is tracked, the earlier a regression is caught.** A model metric tells you
> something broke. A data metric tells you *what* broke.

**Then the harder question, out loud:** *should duplicates fail the pipeline?*

> No. A duplicate is a known artifact with an obvious fix, so it's cleaned in `ingest`.
> If it failed the gate, the pipeline would fail at a 50% valid rate on every single run
> — and a gate that always fails gets switched off. But the **count is logged**, so it
> never becomes invisible.
>
> **The rule: cleaning removes what you know how to fix. Validation stops what you don't.**

### 8.2 The quality gate passing

🖱️ Click **`validate`** → **Metrics**

```
valid_rate_dam       1.0
valid_rate_rtm       1.0
valid_rate_weather   1.0
valid_rate_calendar  1.0
validation_passed    1
```

### 8.3 Lineage — the payoff

🖱️ **Data** → **Data assets**. There are now **four**, and you only created one:

| Asset | Created by |
|---|---|
| `energy_raw` | you, in Part 5 |
| `energy_validated` | the `validate` stage |
| `march_2025_prepared` | the `build_features` stage |
| `march_2025_features` | the `prepare` stage |

Click `march_2025_features` → **Lineage** tab.

> "The pipeline registered these itself. Every intermediate result is a versioned,
> discoverable asset that traces back to the job that made it, and from there back to
> the raw CSVs. **That trace is what an auditor asks for**, and it exists because the
> pipeline starts at raw data instead of a parquet someone uploaded by hand."

---

# Part 9 — Break it on purpose 🔴

**Everything so far was a tour. This is the part they remember. Do not skip it.**

### 9.1 Poison one file

```powershell
python -c "import pandas as pd; p='data/raw/dam/year=2025/month=03/date=2025-03-10/dam.csv'; d=pd.read_csv(p); d.loc[:,'MCP (Rs/MWh) *']=99999; d.to_csv(p,index=False)"
```

> Rs 99,999/MWh is impossible. IEX caps the market clearing price at
> **Rs 10,000/MWh by regulation.**

### 9.2 Register it as a new version

```powershell
az ml data create -f aml/data_assets/01_raw_energy.yml --set version=2
```

> Note it becomes **version 2**. Version 1 is untouched and still good — which is
> exactly the recovery path when this happens for real.

### 9.3 Run the pipeline on the bad data

```powershell
az ml job create -f aml/pipeline.yml --set inputs.raw_data.path=azureml:energy_raw:2
```

### 9.4 🖱️ Watch the graph

- `ingest` → **green**
- `validate` → 🔴 **RED**
- `build_features`, `prepare`, `train`, `evaluate`, `promote` → **grey, never started**

### 💬 Say this — slowly

> "No model was trained. No model was registered. Nothing was promoted.
>
> The bad batch could not reach production, because **there is no path from it to
> production.** The gate isn't a report someone reads on Monday — it's control flow."

### 9.5 Show the evidence

🖱️ Click **`validate`** → **Outputs + logs** → browse to `dam/invalid/`

Every rejected row, with a **`_reject_reason`** column saying `mcp_out_of_range`, sitting on the datastore, versioned by the run that rejected it.

> "That's your audit trail, and it costs nothing to keep. The local version of this
> project wrote that to a Postgres database someone had to keep alive. Here it's just
> an output of the run."

### 9.6 Why the thresholds are defensible

> "Every bound in this suite comes from the **domain**, not from the data.
> Rs 10,000/MWh is regulation. −20 to 60 °C is Indian climate.
>
> Contrast with what people usually write: `mcp < df.mcp.quantile(0.99)`. That bound
> **moves when the data moves**. It cannot catch a systematic shift, because the shift
> redefines the threshold. Domain bounds hold still."

### 9.7 Restore

```powershell
git checkout data/raw/dam/year=2025/month=03/date=2025-03-10/dam.csv
az ml data create -f aml/data_assets/01_raw_energy.yml --set version=3
```

---

# Part 10 — Model registry & champion

🖱️ **Models** → **`dam_mcp_forecast`**

Show the **Versions** list, then click the latest → **Tags**:

| Tag | Value |
|---|---|
| `passed_eval` | `True` |
| `eval_rmse` | `798.4230` |
| `eval_mape` | `7.64` |
| `champion` | `true` |

### 💬 Two gates, two questions — say this explicitly

> "There are two gates in this pipeline and people constantly merge them into one
> vague idea of 'quality'. They answer different questions:
>
> | Gate | Question | Stage |
> |---|---|---|
> | `min_valid_rate = 0.95` | Is this **data** fit to train on? | validate |
> | `rmse < 1000`, `mape < 15%` | Is this **model** fit to serve? | evaluate |
>
> Five models were trained — ARIMA, exponential smoothing, gradient boosting, XGBoost.
> Gradient boosting won at RMSE 798. It passed the gate, so it became champion.
>
> A worse model would still train and still register — it just would not become
> champion. **Promotion is a decision, not a side effect of training.**"

### 💬 The Azure gotcha worth teaching

> "MLflow has a first-class **alias** concept — `models:/dam_mcp_forecast@champion` —
> and nearly every tutorial uses it. **Azure ML does not implement it.** That API
> returns HTTP 404 against a workspace registry. This project's first two runs failed
> on exactly that.
>
> So the champion pointer here is a **tag**, and the promote stage guarantees only one
> version ever carries it.
>
> The general lesson: **'MLflow-compatible' is not 'MLflow-identical'.** The common path
> works everywhere; the edges are where the vendor's own model shows through. You only
> find them by running it."

**Bonus if a run produced an equal-scoring model:** show that v3 registered but champion stayed on v2 — promotion requires *strictly better*, so a tie does not displace the incumbent.

---

# Part 11 — Deploy the endpoint

### 11.1 Find the champion version

```powershell
az ml model list --name dam_mcp_forecast --query "[?tags.champion=='true'].version" -o tsv
```

Note the number it prints (e.g. `2`).

### 11.2 Create the endpoint

```powershell
az ml online-endpoint create -f aml/endpoints/endpoint.yml
```
⏱️ ~3 minutes.

### 11.3 Deploy the champion (replace `2` with your version)

```powershell
az ml online-deployment create -f aml/endpoints/deployment.yml --set model=azureml:dam_mcp_forecast:2 --all-traffic
```
⏱️ ~8 minutes — Azure builds a scoring image and starts the container.

### 11.4 🖱️ Test it in the browser

1. **Endpoints** → **`dam-mcp-endpoint`** → **Test** tab
2. Paste the contents of `scoring/sample_request.json`
3. Click **Test**

✅ A predicted price comes back — in the browser, no code.

### 💬 Say this

> "Azure runs the web server, TLS, health probes, autoscaling, and rolling deploys.
> The local version of this was Flask plus gunicorn plus a Kubernetes Deployment plus
> ArgoCD to sync it. That's four things to operate, replaced by one YAML file.
>
> The **Traffic %** setting is where blue/green lives: deploy a challenger on 10%,
> watch its metrics, then shift traffic. Same endpoint, no client changes."

> ⚠️ **This is the only always-on cost in the demo.** Delete it afterwards (Part 13).

---

# Part 12 — CI/CD with GitHub Actions

### 12.1 One-time setup

**Create the service principal** — this is the identity GitHub uses to talk to Azure:

```powershell
az ad sp create-for-rbac --name "gh-energy-demo" --role Owner --scopes /subscriptions/<SUBSCRIPTION_ID>/resourceGroups/rg-energy-demo --json-auth
```

**Copy the entire JSON output.** It is shown once and never again.

**Store it as a GitHub secret:**

```powershell
gh secret set AZURE_CREDENTIALS --repo <GITHUB_USER>/<GITHUB_REPO> --body '<paste the whole JSON here>'
```

<details>
<summary>Browser alternative</summary>

GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
Name: `AZURE_CREDENTIALS`, Value: the JSON.
</details>

**Set the drift-alert variable** — the `monitor` job fails fast without it:

```powershell
gh variable set ALERT_EMAIL --repo <GITHUB_USER>/<GITHUB_REPO> --body "you@your-domain.com"
```

<details>
<summary>Browser alternative</summary>

GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **Variables** tab → **New repository variable**
Name: `ALERT_EMAIL`, Value: the mailbox that should receive drift alerts.
</details>

**The approval gate** is already configured on this repo: **Settings** → **Environments** → **production** → *Required reviewers: <GITHUB_USER>*.

### 12.2 Point the workflow at the demo workspace

Edit `.github/workflows/mlops.yml`, change these two lines:

```yaml
env:
  RESOURCE_GROUP: rg-energy-demo      # was rg-energy-mlops
  WORKSPACE: mlw-energy-demo          # was mlw-energy-forecast
```

### 12.3 Trigger it

```powershell
git add -A
git commit -m "Point CI/CD at the demo workspace"
git push
```

### 12.4 🖱️ Watch it in GitHub

Repo → **Actions** tab → click the running workflow.

```
ci      ✅ ruff lint + 4 unit tests          (~1 min, no Azure)
  ↓
train   ✅ submits the AML pipeline, streams  (~12 min)
  ↓
deploy  ⏸  WAITING FOR APPROVAL
```

**Click "Review deployments" → "Approve and deploy"** — live, in front of them.

### 💬 Say this

> "Two things worth noticing.
>
> First: `az ml job stream` exits non-zero if the pipeline fails, so **a failed data
> quality gate fails the build**. CI/CD and data quality aren't two systems — they're
> the same control flow. Bad data can't merge.
>
> Second: everything before `deploy` is automatic, and `deploy` **stops and waits for a
> human**. That's the balance you want. **Automate the work, gate the consequence.**"

---

# Part 13 — Teardown

**Delete the endpoint** (the only always-on cost):

```powershell
az ml online-endpoint delete -n dam-mcp-endpoint --yes
```

**Or remove everything:**

🖱️ Portal → **Resource groups** → `rg-energy-demo` → **Delete resource group** → type the name → **Delete**

```powershell
az group delete --name rg-energy-demo --yes --no-wait
```

> The compute cluster costs nothing idle, so you can leave the workspace up between
> demos. **The endpoint you must delete** — it holds a VM 24/7.

---

# Appendix A — If something fails live

| Symptom | Cause | Fix |
|---|---|---|
| `az login` hangs at "Select the account" | Windows WAM broker popup hidden behind terminal | `Ctrl+C`, then `az config set core.enable_broker_on_windows=false`, then `az login` |
| `No subscriptions found` | Signed in with the wrong account | Sign out at login.microsoftonline.com/logout, or use an InPrivate window |
| `MissingSubscriptionRegistration` | Providers not registered | Part 0.3 |
| Job stuck at **Preparing** for ~8 min | ACR is building the environment image (first time only) | Wait. Pre-warm next time (Part 0.4) |
| `Not enough quota` / cluster won't scale | Asked for more than 4 vCPUs | Use `Standard_DS2_v2`, max 2 nodes |
| Endpoint deployment fails on quota | Endpoint quota is **separate** from cluster quota | Request quota, or demo Parts 1–10 + 12 and skip 11 |
| `ModuleNotFoundError: pkg_resources` | `setuptools` missing from the conda env | Already fixed in `aml/environment/conda.yml` |
| `promote` fails with a 404 on `/alias` | Azure ML has no MLflow alias API | Already fixed — champion is a tag |
| Data asset create says version exists | Versions are immutable, by design | Use `--set version=N+1` |

# Appendix B — What each stage does

| Stage | Input | Output | Purpose |
|---|---|---|---|
| `ingest` | `energy_raw` (162 CSVs) | silver parquet | Type, sort, dedup, stamp lineage columns |
| `validate` | silver | validated + rejected | Expectation suites + **quality gate** |
| `build_features` | validated | 4,608 × 39 table | Joins, lags, rolling windows, cyclical encodings |
| `prepare` | prepared table | model features | Dedup to one row per 15-min block, add `event_timestamp` |
| `train` | features | model + metrics | 5 forecasters, best by RMSE → registry |
| `evaluate` | features + model | eval metrics | Holdout scoring, **model gate**, tags the version |
| `promote` | eval signal | champion tag | Sets `champion=true` if it beats the incumbent |

# Appendix C — The three real bugs found building this

Good material — they show the pipeline earning its keep.

1. **DAM source data is 2× duplicated.** 2,639 byte-identical duplicate blocks. Deduped silently downstream before; now measured as `ingest_dupes_dam`.
2. **`setuptools` missing from the conda environment.** `import mlflow` needs `pkg_resources`. Every training run failed for a month with a stack trace that blamed mlflow.
3. **Azure ML doesn't implement MLflow aliases.** `set_registered_model_alias` → HTTP 404. Champion is a tag instead.

And one found in the *local* pipeline by porting it: its validated weather held **43,405 duplicate `(city, time)` readings out of 91,429**, so the station averages were duplicate-weighted. The Azure features are more correct — the difference is the local bug.
