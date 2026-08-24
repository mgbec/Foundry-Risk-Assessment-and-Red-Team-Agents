resource "random_id" "suffix" {
  byte_length = 3
}

locals {
  suffix = random_id.suffix.hex
  rg_name = "rg-${var.project_prefix}-redteam"

  # everyone who needs to run scans / read results
  scan_operator_ids = compact(concat(
    [var.ci_service_principal_object_id],
    var.additional_scan_operator_object_ids
  ))

  # This name just has to match what orchestrator.py / the agent scripts use
  # in AZURE_AI_PROJECT_ENDPOINT (see outputs.tf). The project itself is a
  # real resource below (azapi_resource.project) -- azurerm doesn't support
  # it yet, but it does have to exist; agent creation fails with
  # "ResourceNotFound: The project does not exist" otherwise.
  project_name = "proj-redteam"
}

resource "azurerm_resource_group" "this" {
  name     = local.rg_name
  location = var.location
  tags     = var.tags
}

# ---------------------------------------------------------------------------
# Storage for scan results / scorecards, and Key Vault for any secrets
# (e.g. API keys for a non-Azure target app under test)
# ---------------------------------------------------------------------------

resource "azurerm_storage_account" "results" {
  name                     = "st${var.project_prefix}${local.suffix}"
  resource_group_name      = azurerm_resource_group.this.name
  location                 = azurerm_resource_group.this.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"
  shared_access_key_enabled = false

  tags = var.tags
}

resource "azurerm_storage_container" "scorecards" {
  name                  = "scorecards"
  storage_account_id   = azurerm_storage_account.results.id
  container_access_type = "private"
}

resource "azurerm_key_vault" "this" {
  name                       = "kv-${var.project_prefix}-${local.suffix}"
  resource_group_name        = azurerm_resource_group.this.name
  location                   = azurerm_resource_group.this.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  purge_protection_enabled   = true
  soft_delete_retention_days = 7
  rbac_authorization_enabled = true

  tags = var.tags
}

# ---------------------------------------------------------------------------
# Observability: Application Insights, for client-side OpenTelemetry tracing
# from the Python scripts (see agent/observability.py). Entirely optional at
# runtime -- if APPLICATIONINSIGHTS_CONNECTION_STRING isn't set, tracing
# just doesn't happen.
#
# Wiring this up as the Foundry *project's* connected Application Insights
# (for the portal's own Traces tab) isn't supported by azurerm yet -- that's
# a one-time manual step in the Foundry portal if you want it (Agents ->
# Traces -> Connect). Traces still land in Application Insights via
# OpenTelemetry either way; the portal connection is just a convenience
# viewer on top.
# ---------------------------------------------------------------------------

resource "azurerm_log_analytics_workspace" "observability" {
  name                = "log-${var.project_prefix}-${local.suffix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "PerGB2018"
  retention_in_days   = 30

  tags = var.tags
}

resource "azurerm_application_insights" "observability" {
  name                = "appi-${var.project_prefix}-${local.suffix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  workspace_id        = azurerm_log_analytics_workspace.observability.id
  application_type    = "web"

  # Matches local_auth_enabled=false on the Foundry account and
  # shared_access_key_enabled=false on storage: no connection-string-only
  # ingestion, telemetry must carry an AAD token (agent/observability.py
  # passes DefaultAzureCredential() to configure_azure_monitor for this).
  local_authentication_enabled = false

  tags = var.tags
}

# ---------------------------------------------------------------------------
# AI Foundry account
#
# The "new" unified AI Foundry account is an azurerm_cognitive_account with
# kind = "AIServices" -- azurerm_ai_foundry/azurerm_ai_foundry_project are a
# different, ML-workspace-based "Hub" resource with its own required Key
# Vault/Storage Account wiring; that's not what we want here.
# ---------------------------------------------------------------------------

resource "azurerm_cognitive_account" "account" {
  name                = "aif-${var.project_prefix}-${local.suffix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  kind                = "AIServices"
  sku_name            = "S0"

  custom_subdomain_name = "aif-${var.project_prefix}-${local.suffix}"

  # Enables named "Foundry projects" under this account -- required before
  # azapi_resource.project below can be created.
  project_management_enabled = true

  # Disabling local (key-based) auth forces DefaultAzureCredential /
  # managed identity everywhere -- recommended for a safety-testing account.
  local_auth_enabled = false

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

# The Foundry *project* itself -- azurerm has no resource for this yet
# (tracked upstream; project connections/management aren't supported on
# azurerm_cognitive_account either), so this uses the azapi provider
# already declared in providers.tf as the documented fallback. Without
# this, every agent script fails with "ResourceNotFound: The project does
# not exist" -- the project doesn't get created implicitly just because
# something references its endpoint URL.
resource "azapi_resource" "project" {
  type      = "Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview"
  name      = local.project_name
  parent_id = azurerm_cognitive_account.account.id
  location  = azurerm_resource_group.this.location

  identity {
    type = "SystemAssigned"
  }

  body = {
    properties = {}
  }
}

# ---------------------------------------------------------------------------
# Model deployments
#   - target_model:      the model / app backend you are assessing
#   - adversarial_model:  generates attack prompts for the Red Teaming Agent
#     (kept as a separate deployment so attack-generation quota doesn't
#     compete with the target model's quota during a scan)
# ---------------------------------------------------------------------------

resource "azurerm_cognitive_deployment" "target_model" {
  name                 = "target-${var.target_model_name}"
  cognitive_account_id = azurerm_cognitive_account.account.id

  model {
    format  = "OpenAI"
    name    = var.target_model_name
    version = var.target_model_version
  }

  sku {
    name     = "Standard"
    capacity = var.target_model_capacity
  }
}

resource "azurerm_cognitive_deployment" "adversarial_model" {
  name                 = "adversarial-${var.adversarial_model_name}"
  cognitive_account_id = azurerm_cognitive_account.account.id

  model {
    format  = "OpenAI"
    name    = var.adversarial_model_name
    version = var.target_model_version
  }

  sku {
    name     = "Standard"
    capacity = var.target_model_capacity
  }
}

# ---------------------------------------------------------------------------
# RBAC
#   "Foundry User" (formerly "Azure AI User") is the minimum role needed to
#   run evaluations / red team scans and read the project. Scoped to the
#   account since the project itself isn't a Terraform-managed resource.
# ---------------------------------------------------------------------------

resource "azurerm_role_assignment" "foundry_user" {
  for_each             = toset(local.scan_operator_ids)
  scope                = azurerm_cognitive_account.account.id
  role_definition_name = "Foundry User" # renamed from "Azure AI User" in May 2026
  principal_id         = each.value
}

resource "azurerm_role_assignment" "storage_writer" {
  for_each             = toset(local.scan_operator_ids)
  scope                = azurerm_storage_account.results.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = each.value
}

resource "azurerm_role_assignment" "kv_secrets" {
  for_each             = toset(local.scan_operator_ids)
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = each.value
}

resource "azurerm_role_assignment" "foundry_agent_consumer" {
  # Lets scan operators call agents over A2A (e.g. a2a_target_agent.py)
  # without the broader build/configure permissions Foundry User grants.
  # Least-privilege by design, per Microsoft's own A2A auth guidance.
  for_each             = toset(local.scan_operator_ids)
  scope                = azurerm_cognitive_account.account.id
  role_definition_name = "Foundry Agent Consumer"
  principal_id         = each.value
}

resource "azurerm_role_assignment" "account_identity_agent_consumer" {
  # Draft/unpublished agents share the account's own system identity
  # (per Microsoft's A2A auth docs) rather than a per-agent one. Needed so
  # a2a_caller_agent.py's caller agent -- itself an unpublished draft --
  # can call a2a_target_agent.py's agent via AgenticIdentityToken auth.
  scope                = azurerm_cognitive_account.account.id
  role_definition_name = "Foundry Agent Consumer"
  principal_id         = azurerm_cognitive_account.account.identity[0].principal_id
}

resource "azurerm_role_assignment" "monitoring_publisher" {
  # Despite the name, this role covers publishing all telemetry types
  # (traces/logs, not just metrics) -- required since local_authentication_
  # enabled=false on the Application Insights resource above means AAD is
  # the only way in.
  for_each             = toset(local.scan_operator_ids)
  scope                = azurerm_application_insights.observability.id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = each.value
}

# NOTE on agents: Terraform creates the project itself (azapi_resource.project
# above) and gives it model deployments + storage + RBAC to run against, but
# creating/registering the AGENT resource inside that project isn't reliably
# supported via Terraform/ARM yet -- that still goes through the Foundry/
# Agents SDK. agent/orchestrator.py, sample_target_agent.py, and
# a2a_target_agent.py all create their agent at runtime for this reason.
