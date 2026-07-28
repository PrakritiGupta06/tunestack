variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for resources"
  type        = string
  default     = "us-central1"
}

variable "grafana_admin_password" {
  description = "Value stored in Secret Manager for Grafana's admin password"
  type        = string
  sensitive   = true
}
