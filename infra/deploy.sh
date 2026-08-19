#!/usr/bin/env bash
# Provision the Azure infrastructure (run once). Requires: az login.
set -euo pipefail

RESOURCE_GROUP="${1:-rg-energy-mlops}"
LOCATION="${2:-eastus}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Creating resource group $RESOURCE_GROUP in $LOCATION ..."
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" >/dev/null

echo "Deploying main.bicep ..."
az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$HERE/main.bicep" \
  --parameters "$HERE/main.bicepparam" \
  --query "properties.outputs"

echo "Done. Update config/config.yaml with the workspace/ACR names above."
