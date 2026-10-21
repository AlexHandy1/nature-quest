# Local DuckDB/Parquet index data (fact + dimension tables), served live to
# the app via DuckDB's httpfs support instead of baked into the Docker
# image - see docs/decisions/ADR-016-local-index-data-deployment-gcs-fetch-not-bake-in.md.
#
# Two separate identities by design (ADR-016): the running app only ever
# reads this bucket; whatever eventually writes to it (today: a one-time
# manual upload; later: the Phase 4 monthly refresh job) only ever writes.
# Neither identity holds both permissions on this bucket.

resource "google_storage_bucket" "index_data" {
  project  = var.project_id
  name     = "${var.project_id}-index-data"
  location = var.region # single-region, matching Cloud Run's region - same-region access is the latency assumption this bucket exists to validate.

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  depends_on = [google_project_service.required]
}

resource "google_service_account" "index_refresh" {
  project      = var.project_id
  account_id   = "nature-quest-index-refresh"
  display_name = "Nature Quest index data refresh identity (write-only)"
}

# Read-only: the live app's existing runtime identity, scoped to just this
# bucket - never project-wide, and never combined with write access.
resource "google_storage_bucket_iam_member" "app_reads_index_data" {
  bucket = google_storage_bucket.index_data.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.run.email}"
}

# Write-only: the refresh identity, scoped to just this bucket.
resource "google_storage_bucket_iam_member" "refresh_writes_index_data" {
  bucket = google_storage_bucket.index_data.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.index_refresh.email}"
}

# Lets the operator impersonate the write-only refresh identity for the
# one-time manual upload (Phase 1), ahead of the automated refresh job
# (Phase 4) that will use this same identity later.
resource "google_service_account_iam_member" "operator_can_impersonate_refresh" {
  service_account_id = google_service_account.index_refresh.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = var.operator_member
}

# DuckDB's httpfs extension is cloud-provider-agnostic - it has no concept
# of GCP's metadata-server/ADC auth, so it needs a credential handed to it
# directly. A long-lived HMAC key (rather than a short-lived OAuth token
# the app would have to refresh itself) for the read-only app identity,
# stored in Secret Manager alongside the other backend secrets
# (secret_manager.tf) rather than new app-level token-refresh logic.
resource "google_storage_hmac_key" "app_reads_index_data" {
  service_account_email = google_service_account.run.email
}

resource "google_secret_manager_secret_version" "gcs_hmac_key_id" {
  secret      = google_secret_manager_secret.backend["gcs-index-data-hmac-key-id"].id
  secret_data = google_storage_hmac_key.app_reads_index_data.access_id
}

resource "google_secret_manager_secret_version" "gcs_hmac_secret" {
  secret      = google_secret_manager_secret.backend["gcs-index-data-hmac-secret"].id
  secret_data = google_storage_hmac_key.app_reads_index_data.secret
}
