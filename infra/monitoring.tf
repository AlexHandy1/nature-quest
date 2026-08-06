resource "google_monitoring_notification_channel" "email" {
  project      = var.project_id
  display_name = "Maintainer email"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }

  depends_on = [google_project_service.required]
}

# Log-based metric counting every POST /api/query outcome (REQ-017 emits one
# "query_outcome" structured log line per request, regardless of outcome).
resource "google_logging_metric" "query_outcome" {
  project = var.project_id
  name    = "query-outcome-count"
  filter  = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="${google_cloud_run_v2_service.app.name}"
    jsonPayload.message="query_outcome"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
  }
}

# Deliberately real-time, per-request alerting - fires on every single query
# submission, not a rollup. Accepted as a short-term, pre-launch choice
# (near-zero current traffic); revisit before real launch traffic makes this
# noisy (see docs/FEATURE_IDEAS_BACKLOG.md's daily-digest idea as the
# eventual replacement).
resource "google_monitoring_alert_policy" "query_submitted" {
  project      = var.project_id
  display_name = "Query submitted (real-time, per-request - not a rollup)"
  combiner     = "OR"

  conditions {
    display_name = "Any query_outcome log entry observed"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND metric.type=\"logging.googleapis.com/user/${google_logging_metric.query_outcome.name}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_COUNT"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  depends_on = [google_project_service.required]
}
