#!/usr/bin/env bash
# Manual deploy path for when GitHub Actions is unavailable (e.g. a GitHub
# platform incident, see githubstatus.com) — mirrors exactly what
# .github/workflows/ci-cd.yml's build+deploy jobs do, run from the local
# machine instead. Safe to re-run; each run is tagged with the current git
# SHA, same as CI, so it doesn't collide with or overwrite prior deploys.
#
# Run from the repo root: ./infra/manual_deploy.sh

set -euo pipefail

PROJECT_ID="nature-quest-504414"
REGION="europe-west1"
SERVICE="nature-quest-production"
IMAGE="europe-west1-docker.pkg.dev/nature-quest-504414/nature-quest/app"
SHA="$(git rev-parse HEAD)"

echo "==> Fetching VITE_POSTHOG_KEY from the GitHub Actions repo variable (same source CI uses)"
VITE_POSTHOG_KEY="$(gh variable get POSTHOG_PROJECT_TOKEN)"

echo "==> Configuring docker auth for Artifact Registry (idempotent)"
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --project="$PROJECT_ID" --quiet

echo "==> Building image ${IMAGE}:${SHA}"
docker build -f app/backend/Dockerfile \
  --build-arg VITE_POSTHOG_KEY="$VITE_POSTHOG_KEY" \
  -t "${IMAGE}:${SHA}" .

echo "==> Pushing ${IMAGE}:${SHA}"
docker push "${IMAGE}:${SHA}"

echo "==> Deploying to Cloud Run service ${SERVICE}"
gcloud run deploy "$SERVICE" \
  --image="${IMAGE}:${SHA}" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --quiet

echo "==> Post-deploy smoke test"
URL="$(gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)')"
curl -sf "$URL/health" | grep -q '"status":"ok"'
curl -sf -X POST "$URL/api/interest" -H "Content-Type: application/json" -d '{"query":"manual deploy smoke test"}' | grep -q '"status":"received"'

echo "==> Deploy of ${SHA} to ${URL} succeeded and passed smoke test."
