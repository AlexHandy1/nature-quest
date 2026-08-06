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

variable "alert_email" {
  description = "Maintainer email address for Cloud Monitoring alert notifications (CON-003). Deliberately undefaulted and never committed - supply at apply time (TF_VAR_alert_email or a local .tfvars file)."
  type        = string
}

variable "posthog_project_token" {
  description = "PostHog project API token for server-side AI Observability capture (REQ-019). Same value already used for the frontend's VITE_POSTHOG_KEY Docker build arg - not sensitive (PostHog project tokens are designed for client-side/public use), but kept as an undefaulted variable rather than hardcoded. Supplied at apply time via TF_VAR_posthog_project_token, sourced from the same GitHub Actions repository variable the frontend build already reads (`gh variable get POSTHOG_PROJECT_TOKEN`) - one source of truth, see infra/README.md."
  type        = string
}
