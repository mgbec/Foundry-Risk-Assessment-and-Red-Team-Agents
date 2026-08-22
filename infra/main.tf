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
}

resource "azurerm_resource_group" "this" {
  name     = local.rg_name
  location = var.location
  tags     = var.tags
}

# ---------------------------------------------------------------------------
# AI Foundry account + project
# ---------------------------------------------------------------------------

resource "azurerm_ai_foundry" "account" {
  name                = "aif-${var.project_prefix}-${local.suffix}"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku_name            = "S0"

  # Disabling local (key-based) auth forces DefaultAzureCredential /
  # managed identity everywhere -- recommended for a safety-testing account.
  local_authentication_enabled = false

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

resource "azurerm_ai_foundry_project" "redteam" {
  name          = "proj-redteam"
  ai_foundry_id = azurerm_ai_foundry.account.id
  location      = azurerm_resource_group.this.location

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
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
  cognitive_account_id = azurerm_ai_foundry.account.id

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
  cognitive_account_id = azurerm_ai_foundry.account.id

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
# RBAC
#   "Foundry User" (formerly "Azure AI User") is the minimum role needed to
#   run evaluations / red team scans and read the project.
# ---------------------------------------------------------------------------

resource "azurerm_role_assignment" "foundry_user" {
  for_each             = toset(local.scan_operator_ids)
  scope                = azurerm_ai_foundry_project.redteam.id
  role_definition_name = "Azure AI User" # display name mid-rename to "Foundry User"
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

# NOTE on agents: attaching a Foundry Agent (the orchestrator / the agentic
# system under test) to a specific PROJECT is not yet reliably supported via
# Terraform/ARM -- deployments and agent registration currently need to go
# through the Foundry/Agents SDK against the project endpoint. Terraform's
# job here stops at "give the agent code a project + model deployments +
# storage + RBAC to run against"; agent/orchestrator.py creates the actual
# agent resource at runtime using azure-ai-projects.
