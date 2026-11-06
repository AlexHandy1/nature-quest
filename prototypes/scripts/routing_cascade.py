"""
PROTOTYPE — routing_cascade.py

Question this answers: does the proposed sequential routing cascade for
semantic species search — LLM taxon-resolver first, falling back to
cell-restricted semantic search only when the LLM returns an empty
taxonFilters list — work end-to-end against real production pieces
(candidate_cells, the fact/dimension parquet, the LLM resolver) and real
polygon data, and what does each stage cost in latency?

Branches, mirroring the (not yet built) production decision:
  1. LLM resolves taxon_filters (non-empty) -> species looked up by taxon,
     same as today's production path (query_species_for_filter), duplicated
     here in a simplified form (no hotspot-cell columns) since this
     prototype doesn't need them.
  2. LLM returns taxon_filters == [] -> semantic fallback:
       a. species_in_cells(): ALL species present in the candidate cells,
          unfiltered by taxon (does not exist in production — new query).
       b. semantic_rank_in_cells(): rank ONLY those species by cosine
          similarity to the embedded query (semantic_search_colxr.duckdb,
          COL XR-keyed per semantic_search_index_colxr.py). Never a global
          top-k filtered down afterward — can legitimately return fewer than
          k, or zero, if nothing locally present is a good enough match.

Deliberately NOT implemented here (out of scope for this prototype,
per this session's design discussion):
  - The semantic step's own confidence threshold (parked, needs calibration
    data from this prototype first).
  - Detecting/handling an LLM false-negative (confidently wrong non-empty
    guess for a thematic query) — left for the eval harness.
  - _select_species_across_groups diversity capping, waypoint ordering,
    Wikipedia enrichment, the full /api/query response contract.

Run via server_routing_cascade.py (interactive) or
routing_cascade_experiment.py (batch latency/branch check over fixed
queries). Needs OPENROUTER_API_KEY in .env for the real LLM call.
"""

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "app" / "backend"))

from services.openrouter_taxon_client import resolve_taxon_filters  # noqa: E402
from services.species_index import RANK_TO_COLUMN, candidate_cells  # noqa: E402

load_dotenv()

ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"
EMBED_DB_PATH = ARTIFACTS / "semantic_search_colxr.duckdb"
FACT_PARQUET_PATH = os.environ["FACT_PARQUET_PATH"]
DIMENSION_PARQUET_PATH = os.environ["DIMENSION_PARQUET_PATH"]
K = 5
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def build_llm_client() -> OpenAI:
    return OpenAI(api_key=os.environ["OPENROUTER_API_KEY"], base_url=OPENROUTER_BASE_URL)


def query_species_for_filter(con, taxon_filter: dict, cells: list[str]) -> list[dict]:
    """Taxon-filtered lookup, simplified from production's
    species_index.query_species_for_filter (drops hotspot-cell columns, not
    needed here)."""
    column = RANK_TO_COLUMN[taxon_filter["taxonRank"]]
    rows = con.execute(
        f"""
        SELECT s.specieskey, s.species, s.common_name, SUM(f.occurrence_count) AS total
        FROM read_parquet(?) f
        JOIN read_parquet(?) s ON f.specieskey = s.specieskey
        WHERE f.cell IN (SELECT UNNEST(?)) AND LOWER(s."{column}") = LOWER(?)
        GROUP BY s.specieskey, s.species, s.common_name
        ORDER BY total DESC
        """,
        [FACT_PARQUET_PATH, DIMENSION_PARQUET_PATH, cells, taxon_filter["taxonValue"]],
    ).fetchall()
    return [
        {"specieskey": k, "species": sp, "common_name": cn, "occurrence_count": int(t)}
        for k, sp, cn, t in rows
    ]


def species_in_cells(con, cells: list[str]) -> list[str]:
    """NEW query (doesn't exist in production): every specieskey present in
    these cells, unfiltered by taxon — the candidate set the semantic step
    is restricted to."""
    rows = con.execute(
        """
        SELECT DISTINCT f.specieskey
        FROM read_parquet(?) f
        WHERE f.cell IN (SELECT UNNEST(?))
        """,
        [FACT_PARQUET_PATH, cells],
    ).fetchall()
    return [r[0] for r in rows]


def semantic_rank_in_cells(embed_con, candidate_keys: list[str], query_vector: list[float], k: int = K) -> list[dict]:
    """Ranks ONLY the cell-restricted candidates by cosine similarity —
    never a global top-k filtered down afterward."""
    if not candidate_keys:
        return []
    rows = embed_con.execute(
        """
        SELECT specieskey, scientific_name, common_name, "group", occurrence_count,
               extract, image_url, wiki_url,
               array_cosine_similarity(embedding, ?::FLOAT[384]) AS score
        FROM species
        WHERE specieskey IN (SELECT UNNEST(?))
        ORDER BY score DESC
        LIMIT ?
        """,
        [list(map(float, query_vector)), candidate_keys, k],
    ).fetchall()
    cols = ["specieskey", "scientific_name", "common_name", "group", "occurrence_count",
            "extract", "image_url", "wiki_url", "score"]
    return [dict(zip(cols, row)) for row in rows]


def resolve_species_for_query(
    query: str,
    polygon_wkt: str,
    *,
    llm_client,
    embed_model,
    embed_query_fn,
    fact_con,
    embed_con,
    k: int = K,
) -> dict:
    """The full cascade. Returns branch taken, species found, and a per-stage
    latency breakdown (ms)."""
    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    taxon_filters = resolve_taxon_filters(query, llm_client)
    timings["llm_call"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    cells = candidate_cells(polygon_wkt)
    timings["candidate_cells"] = (time.perf_counter() - t0) * 1000

    if taxon_filters:
        t0 = time.perf_counter()
        groups = [query_species_for_filter(fact_con, f, cells) for f in taxon_filters]
        timings["taxon_species_lookup"] = (time.perf_counter() - t0) * 1000
        species = [s for g in groups for s in g][:k]
        timings["total"] = sum(timings.values())
        return {
            "branch": "llm_resolved",
            "taxon_filters": taxon_filters,
            "species": species,
            "timings_ms": timings,
        }

    t0 = time.perf_counter()
    candidate_keys = species_in_cells(fact_con, cells)
    timings["species_in_cells"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    vector = embed_query_fn(embed_model, query)
    timings["embed_query"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    species = semantic_rank_in_cells(embed_con, candidate_keys, vector, k=k)
    timings["semantic_rank"] = (time.perf_counter() - t0) * 1000

    timings["total"] = sum(timings.values())
    return {
        "branch": "semantic_fallback",
        "taxon_filters": [],
        "candidate_count": len(candidate_keys),
        "species": species,
        "timings_ms": timings,
    }
