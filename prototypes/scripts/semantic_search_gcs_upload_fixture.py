"""
PROTOTYPE — semantic_search_gcs_upload_fixture.py

Builds the synthetic embedding table used by
semantic_search_gcs_latency_probe.py and uploads it to
gs://<project>-index-data/test/ — a separate, inspectable step from the
benchmark itself (see that script's docstring for the full question this is
answering). Run this once; re-run the benchmark script against the same
uploaded object as many times as needed without re-uploading.

Real COL XR specieskeys from wikipedia_coverage_match_manifest.parquet
(239,880 rows), random unit vectors as placeholder embeddings — measures
read/scan cost only, no embedding quality claim.

Needs: gcloud authenticated as a principal with
roles/iam.serviceAccountTokenCreator on nature-quest-index-refresh (granted
to var.operator_member in infra/gcs_index_data.tf).

Run: venv/bin/python prototypes/scripts/semantic_search_gcs_upload_fixture.py

Does NOT delete anything — use
  gcloud storage rm gs://nature-quest-504414-index-data/test/synthetic_embeddings_239880.parquet \\
    --impersonate-service-account nature-quest-index-refresh@nature-quest-504414.iam.gserviceaccount.com \\
    --project nature-quest-504414
when the benchmark work is done.
"""

import subprocess
from pathlib import Path

import duckdb
import numpy as np

DIM = 384
RNG_SEED = 0

ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"
MANIFEST_PATH = ARTIFACTS / "wikipedia_coverage_match_manifest.parquet"
LOCAL_PARQUET = ARTIFACTS / "synthetic_embeddings_239880.parquet"

PROJECT_ID = "nature-quest-504414"
BUCKET = f"{PROJECT_ID}-index-data"
GCS_OBJECT = "test/synthetic_embeddings_239880.parquet"
GCS_URI = f"gs://{BUCKET}/{GCS_OBJECT}"
IMPERSONATE_SA = "nature-quest-index-refresh@nature-quest-504414.iam.gserviceaccount.com"


def build_local_synthetic_parquet() -> int:
    con = duckdb.connect()
    keys = con.execute("SELECT specieskey FROM read_parquet(?)", [str(MANIFEST_PATH)]).fetchall()
    keys = [k[0] for k in keys]
    n = len(keys)
    rng = np.random.default_rng(RNG_SEED)
    vecs = rng.standard_normal((n, DIM)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)

    con.execute(f"CREATE TABLE synthetic (specieskey VARCHAR, embedding FLOAT[{DIM}])")
    con.execute(
        f"INSERT INTO synthetic SELECT * FROM (SELECT unnest(?::VARCHAR[]) AS specieskey, unnest(?::FLOAT[{DIM}][]) AS embedding)",
        [keys, vecs.tolist()],
    )
    con.execute(f"COPY synthetic TO '{LOCAL_PARQUET}' (FORMAT PARQUET)")
    return n


def upload_to_gcs() -> None:
    subprocess.run(
        [
            "gcloud", "storage", "cp", str(LOCAL_PARQUET), GCS_URI,
            "--impersonate-service-account", IMPERSONATE_SA,
            "--project", PROJECT_ID,
        ],
        check=True,
    )


def main():
    print(f"Building local synthetic embedding table from {MANIFEST_PATH.name}...")
    n = build_local_synthetic_parquet()
    print(f"  {n} rows, {LOCAL_PARQUET.stat().st_size / 1e6:.1f} MB local parquet: {LOCAL_PARQUET}")

    print(f"Uploading to {GCS_URI} (impersonating {IMPERSONATE_SA})...")
    upload_to_gcs()
    print("Done. Verify with:")
    print(
        f"  gcloud storage ls -l {GCS_URI} --impersonate-service-account {IMPERSONATE_SA} --project {PROJECT_ID}"
    )


if __name__ == "__main__":
    main()
