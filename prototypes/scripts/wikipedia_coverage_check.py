"""
PROTOTYPE — wikipedia_coverage_check.py

Question this answers: of the species in the production dimension table with
real occurrence data, what fraction actually have an English Wikipedia
article at all? This is the "open unknown" flagged in
semantic_search_experiment_report_311026.md §5 ("the Wikipedia hit rate on
the long tail, which this curated set cannot measure") and the key
dependency for deciding the real embedding batch's species-set scope
(PLANNING_EMBEDDINGS_FEATURES_291026.md Track A) — cheaply, without making a
single Wikipedia API call per species.

Method: Wikipedia publishes a static dump of every page title that exists in
the main article namespace (enwiki-latest-all-titles-in-ns0.gz, ~19.4M
titles, includes redirect pages as their own titles too — e.g.
"Passer_domesticus" exists in the dump as a redirect to "House sparrow", so
matching on either the scientific name OR the common name catches both). A
local, offline exact-title lookup against this dump — download once,
normalize names into Wikipedia's title form (spaces -> underscores, first
letter capitalized), DuckDB join — avoids the ~900k individual HTTP round
trips the real batch job's Wikipedia fetch stage would otherwise cost
(6.4h sequential / ~40min at 10x concurrency for just 106k species, per the
31 Oct report).

Known limitation, stated plainly: this is a conservative, imprecise proxy,
not the real fetch. It will MISS real articles reachable only via a
multi-hop redirect chain, a disambiguated title ("Oak (plant)"), or a title
that doesn't match either name form at all despite the topic existing. It
also can't tell a stub from a rich article. Validated below against this
session's own 98 known-good COL-XR-keyed species (real titles already
confirmed via the Wikipedia REST API in semantic_search_corpus.py) as a
sanity check on recall before trusting the number on the full table.

Downloads prototypes/scratch/enwiki-all-titles-ns0.gz (gitignored, ~105MB)
if not already present.

Run: venv/bin/python prototypes/scripts/wikipedia_coverage_check.py \
       | tee prototypes/logs/wikipedia_coverage_check_$(date +%Y%m%d_%H%M%S).log
"""

import json
import os
import subprocess
import time
from pathlib import Path

import duckdb
from dotenv import load_dotenv

load_dotenv()

TITLES_DUMP_URL = "https://dumps.wikimedia.org/enwiki/latest/enwiki-latest-all-titles-in-ns0.gz"
SCRATCH = Path(__file__).resolve().parent.parent / "scratch"
TITLES_DUMP_PATH = SCRATCH / "enwiki-all-titles-ns0.gz"
ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"
CORPUS_PATH = ARTIFACTS / "semantic_search_corpus.json"
FACT_PARQUET_PATH = os.environ["FACT_PARQUET_PATH"]
DIMENSION_PARQUET_PATH = os.environ["DIMENSION_PARQUET_PATH"]
MIN_OCCURRENCE_COUNT = 1


def ensure_titles_dump() -> None:
    if TITLES_DUMP_PATH.exists():
        print(f"Using cached {TITLES_DUMP_PATH} ({TITLES_DUMP_PATH.stat().st_size / 1e6:.0f}MB)")
        return
    SCRATCH.mkdir(exist_ok=True)
    print(f"Downloading {TITLES_DUMP_URL} …")
    subprocess.run(["curl", "-sL", "-o", str(TITLES_DUMP_PATH), TITLES_DUMP_URL], check=True)
    print(f"Downloaded {TITLES_DUMP_PATH.stat().st_size / 1e6:.0f}MB")


def to_title_case(name: str) -> str:
    """Wikipedia's own title-normalization rule: spaces -> underscores,
    first character forced to uppercase, rest left as-is."""
    name = name.strip().replace(" ", "_")
    return name[:1].upper() + name[1:] if name else name


def main() -> None:
    ensure_titles_dump()
    con = duckdb.connect()

    print("\nLoading title dump into DuckDB…")
    t0 = time.perf_counter()
    con.execute(f"""
        CREATE TABLE titles AS
        SELECT page_title FROM read_csv(?, header=true, columns={{'page_title': 'VARCHAR'}})
    """, [str(TITLES_DUMP_PATH)])
    title_count = con.execute("SELECT count(*) FROM titles").fetchone()[0]
    print(f"Loaded {title_count:,} titles in {(time.perf_counter() - t0) * 1000:.0f}ms")

    # ── Sanity check: recall against this session's 98 known-good species ──
    corpus = json.loads(CORPUS_PATH.read_text())["selected"]
    known_titles = {to_title_case(r["wikipedia_title"]) for r in corpus}
    hits = con.execute(
        "SELECT count(*) FROM titles WHERE page_title IN (SELECT UNNEST(?))", [list(known_titles)]
    ).fetchone()[0]
    print(f"\nSanity check: {hits}/{len(known_titles)} of this session's known-good Wikipedia "
          f"titles found in the dump (expect at/near 100% — anything less means the matching "
          f"method itself is unreliable, not that the dump is stale)")

    # ── Real question: hit rate over the production dimension table ──
    print(f"\nAggregating occurrence counts from {FACT_PARQUET_PATH} (this is the big join)…")
    t0 = time.perf_counter()
    con.execute(f"""
        CREATE TABLE species_with_occurrence AS
        SELECT d.specieskey, d.species, d.common_name, SUM(f.occurrence_count) AS total_occurrence
        FROM read_parquet(?) f
        JOIN read_parquet(?) d ON f.specieskey = d.specieskey
        GROUP BY d.specieskey, d.species, d.common_name
        HAVING SUM(f.occurrence_count) > ?
    """, [FACT_PARQUET_PATH, DIMENSION_PARQUET_PATH, MIN_OCCURRENCE_COUNT])
    target_count = con.execute("SELECT count(*) FROM species_with_occurrence").fetchone()[0]
    print(f"{target_count:,} species with occurrence_count > {MIN_OCCURRENCE_COUNT} "
          f"({(time.perf_counter() - t0) * 1000:.0f}ms)")

    print("\nNormalizing names into Wikipedia title form and matching…")
    t0 = time.perf_counter()
    con.execute("""
        CREATE TABLE normalized AS
        SELECT specieskey, species, common_name, total_occurrence,
               upper(substr(replace(species, ' ', '_'), 1, 1)) || substr(replace(species, ' ', '_'), 2) AS sci_title,
               CASE WHEN common_name IS NOT NULL
                    THEN upper(substr(replace(common_name, ' ', '_'), 1, 1)) || substr(replace(common_name, ' ', '_'), 2)
                    ELSE NULL END AS common_title
        FROM species_with_occurrence
    """)
    result = con.execute("""
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE sci_title IN (SELECT page_title FROM titles)) AS sci_match,
            count(*) FILTER (WHERE common_title IN (SELECT page_title FROM titles)) AS common_match,
            count(*) FILTER (
                WHERE sci_title IN (SELECT page_title FROM titles)
                   OR common_title IN (SELECT page_title FROM titles)
            ) AS either_match
        FROM normalized
    """).fetchone()
    total, sci_match, common_match, either_match = result
    match_ms = (time.perf_counter() - t0) * 1000
    print(f"Matched in {match_ms:.0f}ms")

    print("\n" + "=" * 70)
    print(f"Species with occurrence_count > {MIN_OCCURRENCE_COUNT}: {total:,}")
    print(f"  Scientific-name title match:        {sci_match:,} ({sci_match/total:.1%})")
    print(f"  Common-name title match:             {common_match:,} ({common_match/total:.1%})")
    print(f"  Either match (estimated hit rate):   {either_match:,} ({either_match/total:.1%})")
    print("=" * 70)

    misses = con.execute("""
        SELECT species, common_name FROM normalized
        WHERE sci_title NOT IN (SELECT page_title FROM titles)
          AND (common_title IS NULL OR common_title NOT IN (SELECT page_title FROM titles))
        USING SAMPLE 10
    """).fetchall()
    print(f"\nSample of 10 misses (no title match either way):")
    for species, common_name in misses:
        print(f"  {species} / {common_name}")

    manifest_path = ARTIFACTS / "wikipedia_coverage_match_manifest.parquet"
    con.execute(f"""
        COPY (
            SELECT specieskey, species, common_name, total_occurrence,
                   CASE WHEN sci_title IN (SELECT page_title FROM titles) THEN sci_title
                        ELSE common_title END AS matched_title,
                   sci_title IN (SELECT page_title FROM titles) AS matched_on_scientific_name
            FROM normalized
            WHERE sci_title IN (SELECT page_title FROM titles)
               OR common_title IN (SELECT page_title FROM titles)
        ) TO '{manifest_path}' (FORMAT PARQUET)
    """)
    print(f"\nWrote starter manifest ({either_match:,} rows) to {manifest_path}")

    stats = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "min_occurrence_count": MIN_OCCURRENCE_COUNT,
        "title_dump_count": title_count,
        "sanity_check_hits": hits,
        "sanity_check_total": len(known_titles),
        "target_species_count": total,
        "sci_name_match": sci_match,
        "common_name_match": common_match,
        "either_match": either_match,
        "hit_rate": either_match / total,
    }
    out_path = ARTIFACTS / "wikipedia_coverage_check_results.json"
    out_path.write_text(json.dumps(stats, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
