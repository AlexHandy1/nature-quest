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
