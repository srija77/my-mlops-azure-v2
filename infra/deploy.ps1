# Provision the Azure infrastructure (run once). Requires: az login.
param(
  [string]$ResourceGroup = "rg-energy-mlops",
  [string]$Location = "eastus"
)

$ErrorActionPreference = "Stop"

Write-Host "Creating resource group $ResourceGroup in $Location ..."
az group create --name $ResourceGroup --location $Location | Out-Null

Write-Host "Deploying main.bicep ..."
az deployment group create `
  --resource-group $ResourceGroup `
  --template-file "$PSScriptRoot/main.bicep" `
  --parameters "$PSScriptRoot/main.bicepparam" `
  --query "properties.outputs"

Write-Host "Done. Update config/config.yaml with the workspace/ACR names above."
