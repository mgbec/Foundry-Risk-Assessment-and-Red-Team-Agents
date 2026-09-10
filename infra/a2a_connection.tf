# Optional: manage the A2A connection to the AWS agent as Terraform state
# instead of running setup_a2a_connection.sh by hand. Uses azapi since
# RemoteA2A connections aren't yet a first-class azurerm resource.
#
# Fill in aws_a2a_target / aws_a2a_audience in variables and terraform.tfvars
# (see agent/setup_a2a_connection.sh for how to determine these values from
# the AWS agent's card and its JWT authorizer config).

variable "aws_a2a_target" {
  description = "The SCF Compliance Assessment Agent's Entra-auth RPC endpoint, from its card's additionalInterfaces."
  type        = string
  default     = "https://ar4y22vewc.execute-api.us-east-1.amazonaws.com/entra/rpc"
}

variable "aws_a2a_audience" {
  description = "Entra Application ID URI the AWS Lambda validates as `aud` in the JWT"
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
