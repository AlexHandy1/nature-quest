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

variable "posthog_project_token" {
  description = "PostHog project API token for server-side AI Observability capture (REQ-019). Same value already used for the frontend's VITE_POSTHOG_KEY Docker build arg (CI: vars.POSTHOG_PROJECT_TOKEN) - not sensitive (PostHog project tokens are designed for client-side/public use), but kept as an undefaulted variable rather than hardcoded so the literal value isn't duplicated into version control."
  type        = string
}
