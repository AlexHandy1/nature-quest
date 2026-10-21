variable "backend_secret_names" {
  description = "Names of runtime secrets Cloud Run reads from Secret Manager at startup (REQ-007). Add a name here when a real secret exists. Most values are set out-of-band, never in Terraform config - the gcs-index-data-hmac-* pair is the one exception, whose versions are populated in gcs_index_data.tf from a Terraform-managed google_storage_hmac_key."
  type        = list(string)
  default = [
    "anthropic-api-key",
    "openrouter-api-key",
    "gcs-index-data-hmac-key-id",
    "gcs-index-data-hmac-secret",
  ]
}

resource "google_secret_manager_secret" "backend" {
  for_each = toset(var.backend_secret_names)

  project   = var.project_id
  secret_id = each.value

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_iam_member" "backend_access" {
  for_each = google_secret_manager_secret.backend

  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.run.email}"
}
