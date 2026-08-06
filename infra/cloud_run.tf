resource "google_service_account" "run" {
  project      = var.project_id
  account_id   = "nature-quest-run"
  display_name = "Nature Quest Cloud Run runtime identity"
}

resource "google_cloud_run_v2_service" "app" {
  name     = "nature-quest-${var.environment}"
  project  = var.project_id
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.run.email

    # Cost-creep guardrail (see docs/decisions/ADR-007): caps worst-case
    # Cloud Run cost instead of the full Cloud Armor/load-balancer setup,
    # which was deferred as disproportionate to current traffic.
    scaling {
      max_instance_count = 2
    }

    containers {
      # Placeholder image - CI/CD's deploy step replaces this with the
      # real built image on every deploy (see lifecycle block below).
      image = "us-docker.pkg.dev/cloudrun/container/hello:latest"

      env {
        name  = "POSTHOG_PROJECT_TOKEN"
        value = var.posthog_project_token
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  depends_on = [google_project_service.required]
}

resource "google_cloud_run_v2_service_iam_member" "public_access" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
