"""Manifest-generation step feeding the Wikipedia-embeddings pipeline (fetch
-> embed -> build table -> upload - see docs/status_docs/WORK_SUMMARY_201126.md).
Production port of prototypes/scripts/wikipedia_coverage_check.py: matches
production species (occurrence_count > min_occurrence_count) against
Wikipedia's static title dump (enwiki-latest-all-titles-in-ns0.gz) to produce
the manifest the fetch step reads - specieskey, species, common_name,
total_occurrence, matched_title. An offline, local title-exists lookup
instead of a per-species Wikipedia API call (~900k candidate species would
otherwise mean ~900k round trips just to find out which ones have an
article at all).

Deliberately drops the prototype's own sanity check against its
session-specific known-good corpus (semantic_search_corpus.json) - that was
a one-off validation for the prototype itself, not a repeatable production
concern."""

import os
import subprocess

import duckdb

MIN_OCCURRENCE_COUNT = 1
TITLES_DUMP_URL = "https://dumps.wikimedia.org/enwiki/latest/enwiki-latest-all-titles-in-ns0.gz"


def _default_download(url: str, local_path: str) -> None:
    subprocess.run(["curl", "-sL", "-o", local_path, url], check=True)


def ensure_titles_dump(local_path: str, download_fn=_default_download) -> None:
    if os.path.exists(local_path):
        return
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    download_fn(TITLES_DUMP_URL, local_path)


def to_title_case(name: str) -> str:
    """Wikipedia's own title-normalization rule: spaces -> underscores,
    first character forced to uppercase, rest left as-is. Registered as a
    DuckDB UDF (see build_manifest) so the SQL matching step and this
    directly-tested pure function share one implementation, rather than
    duplicating the rule as inline SQL string logic."""
    name = name.strip().replace(" ", "_")
    return name[:1].upper() + name[1:] if name else name


def load_candidate_species(
    con: duckdb.DuckDBPyConnection, fact_path: str, dimension_path: str, min_occurrence_count: int
) -> list[dict]:
    """Aggregates occurrence_count across cells per species (the ~221M-row
    fact table is read entirely in DuckDB SQL, never materialized row-by-row
    in Python) into a species_with_occurrence table - build_manifest reads
    this directly - and also returns the same rows as the manageable
    candidate set, for direct inspection/testing."""
    con.execute(f"""
        CREATE OR REPLACE TABLE species_with_occurrence AS
        SELECT d.specieskey, d.species, d.common_name, SUM(f.occurrence_count) AS total_occurrence
        FROM read_parquet('{fact_path}') f
        JOIN read_parquet('{dimension_path}') d ON f.specieskey = d.specieskey
        GROUP BY d.specieskey, d.species, d.common_name
        HAVING SUM(f.occurrence_count) > {min_occurrence_count}
    """)
    rows = con.execute("SELECT * FROM species_with_occurrence").fetchall()
    columns = ["specieskey", "species", "common_name", "total_occurrence"]
    return [dict(zip(columns, row, strict=True)) for row in rows]


def load_titles(con: duckdb.DuckDBPyConnection, dump_path: str) -> None:
    con.execute(
        "CREATE OR REPLACE TABLE titles AS SELECT page_title FROM read_csv(?, header=true, "
        "columns={'page_title': 'VARCHAR'})",
        [dump_path],
    )


def build_manifest(con: duckdb.DuckDBPyConnection, output_path: str) -> dict:
    """Requires to_title_case already registered on con as a DuckDB UDF
    (con.create_function), titles already loaded (load_titles), and
    species_with_occurrence already created (load_candidate_species) -
    matches each candidate's scientific-name title first, falling back to
    its common-name title, and writes only the matched rows."""
    con.execute("""
        CREATE OR REPLACE TABLE normalized AS
        SELECT specieskey, species, common_name, total_occurrence,
               to_title_case(species) AS sci_title,
               CASE WHEN common_name IS NOT NULL THEN to_title_case(common_name) ELSE NULL END AS common_title
        FROM species_with_occurrence
    """)
    total, sci_match, common_match, either_match = con.execute("""
        SELECT
            count(*),
            count(*) FILTER (WHERE sci_title IN (SELECT page_title FROM titles)),
            count(*) FILTER (WHERE common_title IN (SELECT page_title FROM titles)),
            count(*) FILTER (
                WHERE sci_title IN (SELECT page_title FROM titles)
                   OR common_title IN (SELECT page_title FROM titles)
            )
        FROM normalized
    """).fetchone()  # type: ignore[misc]

    con.execute(f"""
        COPY (
            SELECT specieskey, species, common_name, total_occurrence,
                   CASE WHEN sci_title IN (SELECT page_title FROM titles) THEN sci_title
                        ELSE common_title END AS matched_title,
                   sci_title IN (SELECT page_title FROM titles) AS matched_on_scientific_name
            FROM normalized
            WHERE sci_title IN (SELECT page_title FROM titles)
               OR common_title IN (SELECT page_title FROM titles)
        ) TO '{output_path}' (FORMAT PARQUET)
    """)

    return {
        "candidate_species": total,
        "scientific_name_match": sci_match,
        "common_name_match": common_match,
        "either_match": either_match,
        "hit_rate": (either_match / total) if total else 0.0,
    }


def run(
    fact_path: str,
    dimension_path: str,
    titles_dump_path: str,
    output_path: str,
    min_occurrence_count: int = MIN_OCCURRENCE_COUNT,
    download_fn=_default_download,
) -> dict:
    con = duckdb.connect()
    con.create_function("to_title_case", to_title_case)

    print(f"Step 1/3: ensuring titles dump is present at {titles_dump_path}", flush=True)
    ensure_titles_dump(titles_dump_path, download_fn=download_fn)
    load_titles(con, titles_dump_path)

    print(f"Step 2/3: loading candidate species (occurrence_count > {min_occurrence_count})", flush=True)
    candidates = load_candidate_species(con, fact_path, dimension_path, min_occurrence_count)
    print(f"  {len(candidates)} candidate species", flush=True)

    print("Step 3/3: matching against Wikipedia titles and writing the manifest", flush=True)
    stats = build_manifest(con, output_path)
    print(
        f"  candidate_species={stats['candidate_species']} "
        f"either_match={stats['either_match']} hit_rate={stats['hit_rate']:.1%}",
        flush=True,
    )
    return stats


def main() -> None:
    fact_path = os.environ["FACT_PARQUET_PATH"]
    dimension_path = os.environ["DIMENSION_PARQUET_PATH"]
    titles_dump_path = os.environ["WIKIPEDIA_TITLES_DUMP_PATH"]
    output_path = os.environ["WIKIPEDIA_MANIFEST_OUTPUT_PATH"]
    run(fact_path, dimension_path, titles_dump_path, output_path)


if __name__ == "__main__":
    main()
