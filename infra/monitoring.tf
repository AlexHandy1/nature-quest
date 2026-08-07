resource "google_monitoring_notification_channel" "email" {
  project      = var.project_id
  display_name = "Maintainer email"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }

  depends_on = [google_project_service.required]
}

# Deliberately real-time, per-request alerting - fires on every single query
# submission, not a rollup. Accepted as a short-term, pre-launch choice
# (near-zero current traffic); revisit before real launch traffic makes this
# noisy (see docs/FEATURE_IDEAS_BACKLOG.md's daily-digest idea as the
# eventual replacement).
#
# Uses a log-based (condition_matched_log) policy rather than a
# log-based-metric + threshold condition: threshold conditions notify on
# incident open/close state transitions, not once per matching log line, so
# with sparse traffic the very first matching log line opens an incident
# that then never closes (no explicit "0" datapoint is ever emitted for a
# quiet period) - every subsequent query is treated as "still violating" and
# never re-notifies. See ADR-010 for the full incident writeup.
resource "google_monitoring_alert_policy" "query_submitted" {
  project      = var.project_id
  display_name = "Query submitted (real-time, per-request - not a rollup)"
  combiner     = "OR"

  conditions {
    display_name = "query_outcome log entry"
    condition_matched_log {
      filter = <<-EOT
        resource.type="cloud_run_revision"
        resource.labels.service_name="${google_cloud_run_v2_service.app.name}"
        jsonPayload.message="query_outcome"
      EOT
      label_extractors = {
        outcome = "EXTRACT(jsonPayload.outcome)"
        query   = "EXTRACT(jsonPayload.query)"
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
