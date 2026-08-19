# Feature Store (Feast on Azure)

Same feature contract as the source project — the `dam_mcp_forecast_v1`
FeatureService bundling three FeatureViews (market / weather / calendar). The
only thing that changed moving to Azure is where the registry and online store
live (`feature_store.yaml`).

## Layout
- `feature_repo/feature_definitions.py` — entity, FeatureViews, FeatureService (unchanged).
- `feature_repo/feature_store.yaml` — Azure config: Blob registry, Redis online store.
- `feature_repo/data/march_2025_features.parquet` — offline source (the March 2025 data).

## Commands
```bash
cd feature_store/feature_repo

# Register feature definitions into the registry
feast apply

# Push the latest feature values to the Redis online store
feast materialize-incremental $(date -u +"%Y-%m-%dT%H:%M:%S")
```

## Where features are used
- **Training / evaluation** read the offline Parquet (batch). In this Azure
  build the AML training component reads the prepared Parquet directly (fast,
  reproducible), while the FeatureService remains the single source of truth for
  *which* columns the model consumes.
- **Serving** would call `store.get_online_features(features="dam_mcp_forecast_v1", ...)`
  against Redis for sub-millisecond lookups. See `scoring/score.py` for the
  request contract.

## Alternative: Azure ML Managed Feature Store
Azure ML has a first-party managed feature store. To use it instead of Feast,
create a feature store resource (`az ml feature-store create`) and port the
FeatureViews to AML feature-set specs. Feast is kept here to stay 1:1 with the
source project's feature logic.
