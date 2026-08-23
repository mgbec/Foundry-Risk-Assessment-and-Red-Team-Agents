output "resource_group_name" {
  value = azurerm_resource_group.this.name
}

output "ai_foundry_account_name" {
  value = azurerm_cognitive_account.account.name
}

output "ai_foundry_project_endpoint" {
  description = "Set this as AZURE_AI_PROJECT_ENDPOINT for the Python agent."
  value       = "https://${azurerm_cognitive_account.account.name}.services.ai.azure.com/api/projects/${local.project_name}"
}

output "target_model_deployment_name" {
  value = azurerm_cognitive_deployment.target_model.name
}

output "adversarial_model_deployment_name" {
  value = azurerm_cognitive_deployment.adversarial_model.name
}

output "results_storage_account" {
  value = azurerm_storage_account.results.name
}

output "results_container_url" {
  value = "${azurerm_storage_account.results.primary_blob_endpoint}${azurerm_storage_container.scorecards.name}"
}

output "key_vault_name" {
  value = azurerm_key_vault.this.name
}

output "app_insights_connection_string" {
  description = "Set this as APPLICATIONINSIGHTS_CONNECTION_STRING to enable tracing (see agent/observability.py)."
  value       = azurerm_application_insights.observability.connection_string
  sensitive   = true
}
