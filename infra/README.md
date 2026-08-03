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

## Manual deploy (before CI/CD exists)

Until the GitHub Actions pipeline is built, deploying a new image is a
manual, one-off process — this is what CI/CD will later automate.

One-time local Docker auth (per machine, not per deploy):

```bash
gcloud auth configure-docker europe-west1-docker.pkg.dev --project=nature-quest-504414
```

Then, from the repo root, for each deploy:

```bash
docker build -f app/backend/Dockerfile \
  -t europe-west1-docker.pkg.dev/nature-quest-504414/nature-quest/app:manual .
docker push europe-west1-docker.pkg.dev/nature-quest-504414/nature-quest/app:manual
gcloud run deploy nature-quest-production \
  --image=europe-west1-docker.pkg.dev/nature-quest-504414/nature-quest/app:manual \
  --region=europe-west1 --project=nature-quest-504414
```

Terraform's `lifecycle.ignore_changes` on the Cloud Run image (see
`cloud_run.tf`) means a later `terraform apply` won't revert this back
to the placeholder image.
