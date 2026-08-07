# Infrastructure (Terraform)

Provisions Nature Quest's GCP infrastructure per
`docs/specs/spec-infrastructure-production-foundation-300726.md` (REQ-004
through REQ-011).

## One-time bootstrap (outside Terraform)

Terraform's remote-state backend (REQ-004) needs a GCS bucket to exist
before `terraform init` can point at it — Terraform can't create the
bucket it will then use to store its own state. This one step is done
manually, once, per environment:

```bash
gcloud storage buckets create gs://nature-quest-504414-tfstate \
  --project=nature-quest-504414 \
  --location=europe-west1 \
  --uniform-bucket-level-access \
  --public-access-prevention
```

Run this again with a different bucket name if a second environment
(e.g. staging) is ever added, per REQ-006's environment-as-a-variable
design.

The bucket name and project ID are not sensitive — GCS access is
IAM-controlled, not secrecy-controlled, so publishing this command is
safe (see ADR-008). What must never be public is the state file's
*contents*, which is why the bucket itself stays private/IAM-restricted
and state is never committed to the repo (REQ-004).

Every `gcloud`/Terraform action taken against the project is also
automatically recorded in the project's Cloud Audit Logs (Admin
Activity logs, always-on) — that's the tamper-evident audit trail of
what actually happened, separate from this README's job of documenting
how to reproduce the setup.

## Everything else

All other infrastructure (Cloud Run, Artifact Registry, Secret Manager,
IAM) is managed by Terraform in this directory — see the `.tf` files.
Applying changes is a manual step (no `terraform apply` in CI/CD):

```bash
cd infra
TF_VAR_posthog_project_token=$(gh variable get POSTHOG_PROJECT_TOKEN) \
TF_VAR_alert_email="you@example.com" \
  terraform apply
```

`posthog_project_token` is sourced from the same GitHub Actions
repository variable the frontend build already uses (`vars.
POSTHOG_PROJECT_TOKEN` — see `.github/workflows/ci-cd.yml`'s `VITE_
POSTHOG_KEY` build arg), not a local `.tfvars` file — one source of
truth for a value that's already non-sensitive (PostHog project tokens
are designed for client-side/public use; it's already baked into the
public frontend JS bundle). `gh variable get` requires the caller to be
authenticated as a GitHub user with write access to this repo — GitHub
does not expose repository variables/secrets to arbitrary public-repo
readers or fork-originated PRs, only to explicit collaborators, so this
scales safely to future contributors without a manual out-of-band
credential hand-off.

`alert_email` (`CON-003`) has no equivalent existing GitHub variable —
supply it directly at apply time (as above) or via a local `.tfvars`
file (gitignored, never committed).

**Cloud Monitoring gotchas** (see `ADR-010` for full detail): a
Terraform-created email notification channel needs its verification
manually triggered (`notificationChannels:sendVerificationCode` then
`:verify` — the Console UI's own flow does this automatically, the API
doesn't); a freshly-created log-based metric can 404 in an alert
policy for a couple of minutes before it propagates; and the Console's
project selector can silently disagree with a page's `?project=` URL,
showing the wrong project's (empty) data.

## Manual deploy (CI/CD unavailable)

The normal path is merging to `main`, which deploys automatically via
GitHub Actions. If Actions itself is unavailable (e.g. a GitHub platform
incident — check githubstatus.com), `infra/manual_deploy.sh` reproduces
CI's build+deploy jobs from the local machine instead:

```bash
./infra/manual_deploy.sh
```

Requires local `gcloud`/`docker`/`gh` auth already configured. Builds
and deploys the current git `HEAD`, tagged by its commit SHA (same
tagging scheme CI uses, so it composes cleanly with normal CI deploys
and doesn't collide with or overwrite them) — includes the
`VITE_POSTHOG_KEY` build arg (sourced from the same GitHub Actions
repository variable CI uses) that a bare `docker build` would silently
omit, and runs the same post-deploy smoke test CI does.

Terraform's `lifecycle.ignore_changes` on the Cloud Run image (see
`cloud_run.tf`) means a later `terraform apply` won't revert this back
to the placeholder image.
