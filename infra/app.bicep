// ============================================================================
// app.bicep — hosting for the inference web app (app/).
//
// Deployed separately from main.bicep on purpose: the ML footprint (workspace,
// ACR, Key Vault, compute) changes rarely, while the app ships on every merge.
// Re-running the ML template to move the app would put the workspace in the
// blast radius of a routine deploy.
//
//   az deployment group create -g rg-energy-mlops -f infra/app.bicep \
//     -p acrName=<acr> keyVaultName=<kv> appInsightsName=<appi> \
//        amlEndpointUrl=<https://.../score>
//
// The scoring key is NOT a parameter. It lives in Key Vault as `aml-endpoint-key`
// and the container resolves it at runtime through a user-assigned identity, so
// it never appears in a template, a deployment history entry, or a CI log.
// ============================================================================

@description('Base name used to derive resource names.')
param baseName string = 'energy'

@description('Azure region.')
param location string = resourceGroup().location

@description('Existing container registry holding dam-mcp-app.')
param acrName string

@description('Existing Key Vault holding the aml-endpoint-key secret.')
param keyVaultName string

@description('Existing Application Insights component for app telemetry.')
param appInsightsName string

@description('Scoring URI of the Azure ML managed online endpoint.')
param amlEndpointUrl string

@description('Deployment to pin requests to. Empty means honour the traffic split.')
param amlDeployment string = 'champion'

@description('Container image tag to run.')
param imageTag string = 'latest'

@description('Name of the Key Vault secret holding the endpoint key.')
param endpointKeySecretName string = 'aml-endpoint-key'

@description('Scale floor. 0 saves cost but adds cold-start latency to a demo.')
@minValue(0)
@maxValue(5)
param minReplicas int = 1

@description('Scale ceiling.')
@minValue(1)
@maxValue(10)
param maxReplicas int = 3

var suffix = toLower(substring(uniqueString(resourceGroup().id), 0, 8))
var identityName = 'id-${baseName}-app'
var envName = 'cae-${baseName}-${suffix}'
var appName = 'ca-${baseName}-forecast'
var logsName = 'log-${baseName}-${suffix}'

// Built-in role definition ids.
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

// ── Identity ────────────────────────────────────────────────────────────────
// User-assigned rather than system-assigned because the container app needs its
// role assignments to exist BEFORE it is created: it resolves the Key Vault
// secret and pulls the image during creation. A system-assigned identity does
// not exist until the app exists, so those grants would always be one deployment
// too late and the first deploy would fail on a secret it cannot read.
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, identity.id, acrPullRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource kvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, identity.id, kvSecretsUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ── Log Analytics (required backing store for a Container Apps environment) ──
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// ── Container Apps environment ──────────────────────────────────────────────
resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

// ── The app ─────────────────────────────────────────────────────────────────
resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  // Without these the app is created before it can pull its image or read its
  // secret, and the first revision fails to activate.
  dependsOn: [
    acrPull
    kvSecretsUser
  ]
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: identity.id
        }
      ]
      secrets: [
        {
          // Resolved from Key Vault at revision start, not stored in the
          // template. Rotating the key in Key Vault plus a revision restart
          // picks up the new value with no redeploy of this file.
          name: 'aml-endpoint-key'
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/${endpointKeySecretName}'
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'forecast-app'
          image: '${acr.properties.loginServer}/dam-mcp-app:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'AML_ENDPOINT_URL', value: amlEndpointUrl }
            { name: 'AML_ENDPOINT_KEY', secretRef: 'aml-endpoint-key' }
            { name: 'AML_DEPLOYMENT', value: amlDeployment }
            { name: 'APP_REVISION', value: imageTag }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsights.properties.ConnectionString
            }
          ]
          probes: [
            {
              // Liveness hits /health, which makes no outbound call. Pointing it
              // at /ready instead would let an endpoint outage restart every
              // replica, converting a partial outage into a total one.
              type: 'Liveness'
              httpGet: { path: '/health', port: 8000 }
              initialDelaySeconds: 10
              periodSeconds: 30
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: { path: '/ready', port: 8000 }
              initialDelaySeconds: 5
              periodSeconds: 15
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: '20'
              }
            }
          }
        ]
      }
    }
  }
}

output appUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output appNameOut string = containerApp.name
output identityPrincipalId string = identity.properties.principalId
output environmentNameOut string = environment.name
