# Optional: manage the A2A connection to the AWS agent as Terraform state
# instead of running setup_a2a_connection.sh by hand. Uses azapi since
# RemoteA2A connections aren't yet a first-class azurerm resource.
#
# Fill in aws_a2a_target / aws_a2a_audience in variables and terraform.tfvars
# (see agent/setup_a2a_connection.sh for how to determine these values from
# the AWS agent's card and its JWT authorizer config).
#
# ---------------------------------------------------------------------------
# IDENTITY CHAIN (why this file has app-role resources, not just a connection)
# ---------------------------------------------------------------------------
# The Foundry MI is an APP-ONLY caller. Entra only mints a usable token for the
# SCF API if that API grants the MI an App role (Application member type) --
# a delegated scope can't work app-only (it needs a signed-in user to consent).
# So a working call needs all four links below; the two azuread resources in
# this file are links (1) and (2).
#
#   THIS TENANT (<tenant-id>)                           AWS (us-east-1)
#   ---------------------------------------             -----------------------
#
#   SCF API app registration (<scf-app-client-id>)
#   ┌───────────────────────────────────────┐
#   │ APPLICATION object                     │
#   │  (1) azuread_application_app_role       │   defines the role on the
#   │      "Agent.Invoke"                     │   *application* object
#   │      allowed_member_types=[Application] │
#   ├───────────────────────────────────────┤
#   │ SERVICE PRINCIPAL object                │
#   │   ▲ (2) azuread_app_role_assignment     │   grants the role on the
#   │   │     grants Agent.Invoke             │   *service principal* object
#   └───┼───────────────────────────────────┘
#       │ assigned to
#   ┌───┴───────────────────────┐
#   │ Foundry account MI        │
#   │ (system-assigned identity │
#   │  of azurerm_cognitive_    │
#   │  account.account)         │
#   └───┬───────────────────────┘
#       │ used by
#   ┌───┴───────────────────────────────────┐        ┌──────────────────────┐
#   │ (3) azapi_resource                     │  Bearer│ API Gateway          │
#   │     aws_agent_a2a_connection           │  token │ /entra/rpc           │
#   │     authType=ProjectManagedIdentity    │───────▶│ JWT authorizer       │
#   │     audience=api://<scf-app-client-id> │        │ (4) checks aud + iss │
#   │     target=.../entra/rpc               │        │  -> SCF agent        │
#   └───────────────────────────────────────┘        └──────────────────────┘
#
# NOTE ON THE TWO OBJECT TYPES: an Entra app reg is two objects -- the
# APPLICATION (the blueprint, where roles are declared: link 1 uses
# scf_agent_app_object_id) and the SERVICE PRINCIPAL (the tenant-local
# instance, where assignments live: link 2 uses the SP object id, resolved by
# the data source below from scf_agent_app_client_id).
#
# NOTE ON THE AUDIENCE (link 4): the connection requests a token FOR
# api://<scf-app-client-id>, but Entra stamps a v2.0 app-only token's `aud` as
# the bare client-id GUID (<scf-app-client-id>). So the AWS authorizer's entra_audience
# must be the GUID form, while aws_a2a_audience here is the api:// form. See
# the README "Connecting to an external A2A agent" section.
# ---------------------------------------------------------------------------

variable "aws_a2a_target" {
  description = "The SCF Compliance Assessment Agent's Entra-auth RPC endpoint, from its card's additionalInterfaces."
  type        = string
  default     = "https://ar4y22vewc.execute-api.us-east-1.amazonaws.com/entra/rpc"
}

variable "aws_a2a_audience" {
  description = "Entra Application ID URI the AWS Lambda validates as `aud` in the JWT (the connection requests a token for this resource)."
  type        = string
  default     = ""
}

variable "scf_agent_app_client_id" {
  description = <<-EOT
    Client (application) ID of the AWS SCF agent's Entra app registration --
    the resource the Foundry managed identity gets a token for. This is the
    GUID inside aws_a2a_audience (api://<this-guid>). Needed to grant the MI
    the app role below. Leave empty to skip the app-role grant (e.g. if the
    app reg lives in a different tenant and is managed elsewhere).
  EOT
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Entra app-role grant: let the Foundry account's managed identity obtain an
# app-only token for the SCF agent's API. Without this, the MI's token carries
# no `roles` claim and (depending on the resource app's config) can't be minted
# for the api://... audience at all -- which is why a first-time setup hits a
# 401 at the AWS /entra/rpc JWT authorizer. Mirrors the manual steps:
#   az ad app update --app-roles ...        (defines Agent.Invoke, App members)
#   POST /servicePrincipals/{mi}/appRoleAssignments   (assigns it to the MI)
# Gated on scf_agent_app_client_id so the whole block is opt-in.
# ---------------------------------------------------------------------------
data "azuread_service_principal" "scf_agent_api" {
  count     = var.scf_agent_app_client_id != "" ? 1 : 0
  client_id = var.scf_agent_app_client_id
}

# The SCF API app registration must expose an Application-type app role for
# app-only callers. If you own that app reg in this tenant, manage the role
# here; if it's owned elsewhere, define the role on that side instead and just
# keep the assignment below.
resource "azuread_application_app_role" "scf_agent_invoke" {
  count          = var.scf_agent_app_client_id != "" && var.manage_scf_app_role ? 1 : 0
  application_id = "/applications/${var.scf_agent_app_object_id}"
  role_id        = "75d9ce7a-40ce-421b-8716-096562877ae4"

  allowed_member_types = ["Application"]
  description          = "A2A callers may invoke the SCF Compliance Agent"
  display_name         = "Agent.Invoke"
  value                = "Agent.Invoke"
}

resource "azuread_app_role_assignment" "foundry_mi_scf_invoke" {
  count               = var.scf_agent_app_client_id != "" ? 1 : 0
  app_role_id         = "75d9ce7a-40ce-421b-8716-096562877ae4"
  principal_object_id = azurerm_cognitive_account.account.identity[0].principal_id
  resource_object_id  = data.azuread_service_principal.scf_agent_api[0].object_id
}

variable "manage_scf_app_role" {
  description = "Whether to define the Agent.Invoke app role on the SCF app registration from here (true if you own that app reg in this tenant). Requires scf_agent_app_object_id."
  type        = bool
  default     = false
}

variable "scf_agent_app_object_id" {
  description = "Object ID of the SCF agent's Entra APPLICATION (not the service principal) -- only needed when manage_scf_app_role = true."
  type        = string
  default     = ""
}

resource "azapi_resource" "aws_agent_a2a_connection" {
  # gated on audience (not target) now that target has a working default --
  # audience still has to come from the AWS Lambda authorizer's config
  count     = var.aws_a2a_audience != "" ? 1 : 0
  type      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview"
  name      = "aws-redteam-agent-a2a"
  parent_id = azapi_resource.project.id

  # RemoteA2A is a newer preview category not yet reflected in the azapi
  # provider's bundled schema (as of writing, its local validator doesn't
  # know about it and also flags `group` as read-only even though
  # Microsoft's own REST example for this exact resource includes it).
  # Skip client-side validation and let ARM validate server-side instead.
  schema_validation_enabled = false

  body = {
    properties = {
      authType      = "ProjectManagedIdentity" # per ARM server-side validation, one of the few authTypes valid for category=RemoteA2A
      category      = "RemoteA2A"
      target        = var.aws_a2a_target
      isSharedToAll = true
      sharedUserList = []
      audience      = var.aws_a2a_audience
      useWorkspaceManagedIdentity = true
      metadata = {
        ApiType = "Azure"
      }
    }
  }
}

output "aws_a2a_connection_name" {
  value = length(azapi_resource.aws_agent_a2a_connection) > 0 ? azapi_resource.aws_agent_a2a_connection[0].name : null
}
