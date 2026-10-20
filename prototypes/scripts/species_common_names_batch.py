"""
Throwaway prototype — NOT production code.

Question being tested: can species_dimension gain a common_name column by
looking up ChecklistBank's per-taxon vernacular endpoint for just the
species that matter to this app -- the top-N species_dimension/fact-table
entries ranked by total occurrence_count, where N is the smallest count
covering 99% of all recorded occurrences -- rather than bulk-crawling
every vernacular name in the whole COL XR release?

A full bulk-crawl approach was explored and ruled out: ChecklistBank's
/dataset/{key}/vernacular endpoint hard-caps `offset` at 100,000, and the
pinned COL XR release has 399,396 English rows, so plain or sector-filtered
pagination can't reach the whole table (one single sector alone has
157,491 rows -- over the cap on its own, with no further filter dimension
to split it). See docs/status_docs/WORK_SUMMARY_191026.md and the
following session's conversation for the full exploration trail.

Occurrence volume is heavily long-tailed (verified against the real fact
table): the top 10,000 species already cover ~93% of all occurrences, top
100,000 covers ~99%. Prioritizing lookups by observation frequency gets
near-complete practical coverage (what the app actually surfaces to
users) for a small, bounded number of per-taxon API calls instead of
needing every taxon in the full COL tree.

Standalone — does not import from other prototype scripts.
"""

import concurrent.futures
import os

import duckdb
import pandas as pd
import requests

GBIF_COL_XR_DATASET_KEY = "7ddf754f-d193-4cc9-b351-99906754a03b"
CHECKLISTBANK_BASE = "https://api.checklistbank.org"

FACT_PARQUET = "prototypes/scratch/fact_sorted_by_cell.parquet"
DIM_PARQUET = "prototypes/scratch/species_dimension.parquet"
DIM_WITH_COMMON_NAMES_PARQUET = "prototypes/scratch/species_dimension_with_common_names.parquet"

COVERAGE_TARGET = 0.99
MAX_WORKERS = 20

# Reused across all per-taxon requests so the lookup pass isn't paying a
# fresh TCP/TLS handshake per call -- verified live (20 concurrent
# requests, 0 errors) that ChecklistBank has no documented or observed
# rate limit at this concurrency.
_session = requests.Session()
_session.mount("https://", requests.adapters.HTTPAdapter(pool_maxsize=MAX_WORKERS, pool_connections=MAX_WORKERS))


def resolve_pinned_col_xr_dataset_key(gbif_dataset_key=GBIF_COL_XR_DATASET_KEY):
    """GBIF's own registry record for its backbone-of-record dataset carries
    the ChecklistBank dataset key it's actually pinned to (CLB_DATASET_KEY),
    per WORK_SUMMARY_191026.md. This is the correct way to find which COL XR
    release to query — not browsing ChecklistBank's release list directly."""
    resp = requests.get(f"https://api.gbif.org/v1/dataset/{gbif_dataset_key}", timeout=30)
    resp.raise_for_status()
    identifiers = resp.json().get("identifiers", [])
    for identifier in identifiers:
        if identifier.get("type") == "CLB_DATASET_KEY":
            return int(identifier["identifier"])
    raise ValueError(f"No CLB_DATASET_KEY identifier found on GBIF dataset {gbif_dataset_key}")


def _is_code_like(name):
    """Looks like a banding/abbreviation code (e.g. "COST", "GRTI"), not a name."""
    stripped = name.replace(" ", "").replace("-", "").replace(".", "")
    return stripped.isalpha() and stripped.isupper() and len(stripped) <= 5


def _is_informal_lowercase(name):
    """Informal/slang entries in ChecklistBank's crowd-sourced data start
    lowercase (e.g. "pigeon", "kildeer"), unlike real English common names."""
    return name[0].islower()


def _is_non_ascii(name):
    """Catches names in another script mistagged as English in the source
    data (verified real example: an Indigenous-language name for a
    species, tagged language=eng)."""
    return not name.isascii()


def _is_junk(name):
    return _is_code_like(name) or _is_informal_lowercase(name) or _is_non_ascii(name)


def pick_best_name(names):
    """Dedup policy for multiple English vernacular names per species
    (ChecklistBank has no "preferred" flag). Rejects banding codes,
    informal lowercase entries, and non-ASCII/mistagged names, then picks
    the longest of what's left (more qualified/standard names tend to be
    longer than slang, e.g. "American Goldfinch" vs "Golden Finch"), ties
    broken alphabetically for determinism. Falls back to the original,
    unfiltered list if every candidate looks like junk, rather than
    losing the species' name entirely."""
    candidates = [name for name in names if not _is_junk(name)]
    if not candidates:
        candidates = names
    return sorted(candidates, key=lambda name: (-len(name), name))[0]


def build_common_name_map(records):
    names_by_taxon_id = {}
    for record in records:
        names_by_taxon_id.setdefault(record["taxonID"], []).append(record["name"])
    return {taxon_id: pick_best_name(names) for taxon_id, names in names_by_taxon_id.items()}


def join_common_names(dim_rows, common_name_map):
    return [
        {**row, "common_name": common_name_map.get(row["specieskey"])}
        for row in dim_rows
    ]


def compute_top_n_cutoff(fact_parquet=FACT_PARQUET, coverage=COVERAGE_TARGET):
    """Smallest N such that the top-N species by total occurrence_count
    (summed across all cells) account for at least `coverage` of all
    recorded occurrences in the fact table."""
    con = duckdb.connect()
    n = con.execute(f"""
        WITH species_totals AS (
            SELECT specieskey, SUM(occurrence_count) AS total_occ
            FROM read_parquet('{fact_parquet}')
            GROUP BY specieskey
        ),
        ranked AS (
            SELECT specieskey, total_occ,
                   SUM(total_occ) OVER (ORDER BY total_occ DESC ROWS UNBOUNDED PRECEDING) AS cum_occ,
                   ROW_NUMBER() OVER (ORDER BY total_occ DESC) AS rn
            FROM species_totals
        ),
        grand_total AS (SELECT SUM(total_occ) AS t FROM species_totals)
        SELECT MIN(rn) FROM ranked, grand_total WHERE cum_occ >= {coverage} * t
    """).fetchone()
    return n[0]


def top_n_specieskeys(n, fact_parquet=FACT_PARQUET):
    con = duckdb.connect()
    rows = con.execute(f"""
        SELECT specieskey
        FROM read_parquet('{fact_parquet}')
        GROUP BY specieskey
        ORDER BY SUM(occurrence_count) DESC
        LIMIT {n}
    """).fetchall()
    return [r[0] for r in rows]


def fetch_taxon_vernacular_names(dataset_key, taxon_id, lang="eng"):
    resp = _session.get(
        f"{CHECKLISTBANK_BASE}/dataset/{dataset_key}/taxon/{taxon_id}/vernacular",
        params={"lang": lang},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_common_names_for_taxa(dataset_key, taxon_ids, fetch_fn=fetch_taxon_vernacular_names, max_workers=MAX_WORKERS):
    """Fans out one request per taxon, concurrently (bounded by
    max_workers). fetch_fn is injected so this is testable without a
    network call -- real network reliability (retries, partial failures)
    is out of scope for this throwaway prototype."""
    records = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_fn, dataset_key, taxon_id): taxon_id for taxon_id in taxon_ids}
        for future in concurrent.futures.as_completed(futures):
            taxon_id = futures[future]
            for name_row in future.result():
                records.append({"taxonID": taxon_id, "name": name_row["name"]})
    return records


def build_species_dimension_with_common_names(
    fact_parquet=FACT_PARQUET,
    dim_parquet=DIM_PARQUET,
    output_parquet=DIM_WITH_COMMON_NAMES_PARQUET,
    coverage=COVERAGE_TARGET,
    dataset_key=None,
    max_workers=MAX_WORKERS,
):
    dataset_key = dataset_key or resolve_pinned_col_xr_dataset_key()

    n = compute_top_n_cutoff(fact_parquet, coverage)
    print(f"Cutoff for {coverage * 100:.0f}% occurrence coverage: top {n:,} species", flush=True)

    taxon_ids = top_n_specieskeys(n, fact_parquet)
    print(
        f"Looking up ChecklistBank dataset {dataset_key} per-taxon vernacular "
        f"names for {len(taxon_ids):,} species ({max_workers} workers)...",
        flush=True,
    )

    records = fetch_common_names_for_taxa(dataset_key, taxon_ids, max_workers=max_workers)
    print(f"  fetched {len(records):,} English vernacular name rows", flush=True)

    common_name_map = build_common_name_map(records)
    print(f"  deduped to {len(common_name_map):,} unique taxonID -> common_name entries", flush=True)

    common_names_df = pd.DataFrame(
        {"specieskey": list(common_name_map.keys()), "common_name": list(common_name_map.values())}
    )
    con = duckdb.connect()
    con.register("common_names", common_names_df)
    con.execute(f"""
        COPY (
            SELECT d.*, c.common_name
            FROM read_parquet('{dim_parquet}') d
            LEFT JOIN common_names c ON d.specieskey = c.specieskey
        ) TO '{output_parquet}' (FORMAT PARQUET)
    """)
    print(f"  wrote {output_parquet}", flush=True)
    return output_parquet


if __name__ == "__main__":
    os.makedirs("prototypes/scratch", exist_ok=True)
    build_species_dimension_with_common_names()
