# Monthly automated GBIF data-update pipeline - Cloud Run Job + Cloud
# Scheduler trigger, automating the manual table-creation process from
# the 8-9 Oct sessions. Runs as the index_refresh identity already
# provisioned in gcs_index_data.tf (write-only on the index-data bucket).
# See app/backend/jobs/monthly_gbif_update.py for the pipeline itself and
# docs/status_docs/ for the full design discussion.

locals {
  monthly_gbif_update_job_name = "nature-quest-monthly-gbif-update"
}

# index_refresh already has storage.objectAdmin on the bucket
# (gcs_index_data.tf) - this job also needs to submit BigQuery jobs
# (the raw-table scan, fact/dimension derivation, extract-to-GCS, and
# the top-N aggregate query), billed to this project.
resource "google_project_iam_member" "index_refresh_bigquery_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.index_refresh.email}"
}

# CI/CD's deploy identity (wif.tf) needs to actAs index_refresh to run
# `gcloud run jobs update --image=...` - same requirement, same grant
# shape as deploy_can_run_as_runtime_sa in wif.tf for the Service's own
# runtime SA. Without this, the image-update step fails with
# PERMISSION_DENIED on iam.serviceaccounts.actAs.
resource "google_service_account_iam_member" "deploy_can_run_as_index_refresh_sa" {
  service_account_id = google_service_account.index_refresh.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_cloud_run_v2_job" "monthly_gbif_update" {
  name                = local.monthly_gbif_update_job_name
  project             = var.project_id
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.index_refresh.email
      max_retries     = 0

      # Generous on purpose: this session's own measurement found the
      # production-baseline download+query taking several minutes even
      # from a throttled test environment (expected to be faster from a
      # real same-region Cloud Run instance, but not yet confirmed) - see
      # docs/status_docs/ for the full timing discussion. Runs monthly,
      # non-interactively, so a long timeout costs nothing but headroom.
      timeout = "1800s"

      containers {
        # Placeholder - CI/CD's deploy step points this at the just-built
        # job image on every push to main (ci-cd.yml's "Update monthly
        # GBIF update Job image" step).
        image = "us-docker.pkg.dev/cloudrun/container/hello:latest"

        resources {
          limits = {
            # 4Gi OOM-killed a real execution on 24 Oct 2026 (exit
            # condition: "The configured memory limit was reached") during
            # the fact-shard recombine step. Measured locally against the
            # real ~1GB/221M-row shard set from that same run: the
            # recombine's DuckDB ORDER BY sort peaks at ~8.4GB RSS / ~13GB
            # peak memory footprint (no memory_limit pragma is set, so
            # DuckDB never spills - and Cloud Run's default filesystem is
            # memory-backed anyway, so spilling to disk wouldn't relieve
            # this without also provisioning a real disk-backed volume).
            # 16Gi gives ~3x margin above that measured peak; cost is still
            # negligible for a ~30min monthly run (Cloud Run Jobs pricing).
            memory = "16Gi"
            cpu    = "4"
          }
        }

        env {
          name  = "INDEX_DATA_BUCKET"
          value = google_storage_bucket.index_data.name
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image]
  }

  depends_on = [google_project_service.required]
}

# Cloud Scheduler needs its own identity to call the Cloud Run Jobs API
# on a schedule - deliberately not index_refresh (that one only needs
# BigQuery/GCS access, not run.invoker on itself).
resource "google_service_account" "monthly_gbif_update_scheduler" {
  project      = var.project_id
  account_id   = "nature-quest-gbif-update-sched"
  display_name = "Monthly GBIF update Scheduler identity"
}

# The only invoker binding on this Job - scoped to just this one Job
# resource, same narrow-grant pattern as the Service's public-access
# binding in cloud_run.tf is narrow to just that Service.
resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.monthly_gbif_update.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.monthly_gbif_update_scheduler.email}"
}

# 00:00 UTC on the 7th of each month - a reasoned assumption, not a
# confirmed fact (GBIF's own exact monthly-snapshot publish day is
# undocumented; see docs/status_docs/ for what was checked). The
# near-zero-change soft-warning in evaluate() is the guard against this
# assumption being wrong in a given month, not this schedule itself.
resource "google_cloud_scheduler_job" "monthly_gbif_update" {
  project   = var.project_id
  region    = var.region
  name      = local.monthly_gbif_update_job_name
  schedule  = "0 0 7 * *"
  time_zone = "UTC"

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.monthly_gbif_update.name}:run"

    oauth_token {
      service_account_email = google_service_account.monthly_gbif_update_scheduler.email
    }
  }

  depends_on = [google_project_service.required]
}

# Structured-log alert: fires on every run's outcome (pass/warn/block),
# not just failures - this runs 12x/year, so per-run noise isn't the
# concern real-time per-query alerting had (see ADR-010/monitoring.tf).
resource "google_monitoring_alert_policy" "monthly_gbif_update_outcome" {
  project      = var.project_id
  display_name = "Monthly GBIF update: run outcome"
  combiner     = "OR"

  conditions {
    display_name = "monthly_gbif_update_outcome log entry"
    condition_matched_log {
      filter = <<-EOT
        resource.type="cloud_run_job"
        resource.labels.job_name="${google_cloud_run_v2_job.monthly_gbif_update.name}"
        jsonPayload.message="monthly_gbif_update_outcome"
      EOT
      label_extractors = {
        verdict       = "EXTRACT(jsonPayload.verdict)"
        block_reasons = "EXTRACT(jsonPayload.block_reasons)"
        warn_reasons  = "EXTRACT(jsonPayload.warn_reasons)"
      }
    }
  }

  alert_strategy {
    notification_rate_limit {
      period = "300s"
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  depends_on = [google_project_service.required]
}

# GCP-level backstop: fires if the Job's execution fails outright
# (crash, OOM, permissions - anything before the pipeline ever reaches
# the point of logging its own outcome). completed_execution_count only
# increments "failed" after every retry is exhausted, which pairs
# cleanly with max_retries=0 above - one failed task is already a
# failed execution, no ambiguity/delay. This is the independent signal
# the structured-log alert above can't provide on its own, since that
# one depends on the pipeline getting far enough to log anything at all.
resource "google_monitoring_alert_policy" "monthly_gbif_update_execution_failed" {
  project      = var.project_id
  display_name = "Monthly GBIF update: execution failed (GCP-level backstop)"
  combiner     = "OR"

  conditions {
    display_name = "Cloud Run Job execution failed"
    condition_threshold {
      filter          = <<-EOT
        resource.type="cloud_run_job"
        resource.labels.job_name="${google_cloud_run_v2_job.monthly_gbif_update.name}"
        metric.type="run.googleapis.com/job/completed_execution_count"
        metric.labels.result="failed"
      EOT
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  depends_on = [google_project_service.required]
}
