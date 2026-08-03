variable "project_id" {
  description = "GCP project ID"
  type        = string
  default     = "nature-quest-504414"
}

variable "region" {
  description = "GCP region for all regional resources"
  type        = string
  default     = "europe-west1"
}

variable "environment" {
  description = "Deployment environment name (REQ-006: variable, not hardcoded, so a second environment can be added later)"
  type        = string
  default     = "production"
}
