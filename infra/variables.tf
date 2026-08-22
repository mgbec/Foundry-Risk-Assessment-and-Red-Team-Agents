variable "project_prefix" {
  description = "Short prefix used to name resources, e.g. 'aisafety'."
  type        = string
  default     = "aisafety"
}

variable "location" {
  description = "Azure region. Pick a region where your target model SKU is available."
  type        = string
  default     = "eastus2"
}

variable "target_model_name" {
  description = "Model to deploy as the system under test."
  type        = string
  default     = "gpt-4o"
}

variable "target_model_version" {
  type    = string
  default = "2024-11-20"
}

variable "target_model_capacity" {
  description = "Deployment capacity in TPM units (x1000)."
  type        = number
  default     = 10
}

variable "adversarial_model_name" {
  description = "Model used by the Red Teaming Agent to GENERATE attack prompts. Can be the same or different from the target model."
  type        = string
  default     = "gpt-4o"
}

variable "ci_service_principal_object_id" {
  description = "Object ID of the GitHub Actions / Azure DevOps federated identity (or SP) that will run scans in CI. Leave empty and assign roles manually if not using CI yet."
  type        = string
  default     = ""
}

variable "additional_scan_operator_object_ids" {
  description = "Object IDs of humans/groups who should be able to trigger scans and read results."
  type        = list(string)
  default     = []
}

variable "tags" {
  type = map(string)
  default = {
    workload = "ai-safety-redteam"
  }
}
