import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from models.query import QueryRequest
from services import ai_observability
from services.logging_client import (
    log_llm_taxon_filters_resolved,
    log_query_outcome,
    log_query_submitted,
    log_species_enriched,
    log_species_selected,
    log_waypoints_ordered,
    timed_checkpoint,
)
from services.openrouter_taxon_client import resolve_taxon_filters
from services.query_budget import try_consume_daily_budget
from services.rate_limiter import limiter
from services.species_enrichment import enrich_species
from services.species_index import (
    TOP_SPECIES_COUNT,
    _select_species_across_groups,
    candidate_cells,
    get_connection_cursor,
    polygon_centroid,
    query_species_for_filter,
)
from services.tts import resolve_api_key as resolve_openrouter_api_key
from services.waypoints import order_waypoints

router = APIRouter()

UNRESOLVED_MESSAGE = "Sorry, we couldn't match that to a category we support yet — try something like 'birds' or 'plants'."
NO_RESULTS_MESSAGE = "We understood your request, but didn't find anything for it here right now."
DAILY_LIMIT_MESSAGE = "We've reached today's limit for this feature — please try again tomorrow."
RESOLVED_MESSAGE = "This is an early preview of a much bigger nature-walk experience to come."
MAX_TAXON_FILTERS = 10

FACT_PARQUET_PATH = os.environ["FACT_PARQUET_PATH"]
DIMENSION_PARQUET_PATH = os.environ["DIMENSION_PARQUET_PATH"]


def _resolve_openrouter_api_key() -> str | None:
    return resolve_openrouter_api_key() or os.environ.get("OPENROUTER_API_KEY")


def _resolve_taxon_filters(query: str, distinct_id: str, consent: bool) -> tuple[list[dict], dict]:
    client = ai_observability.build_openrouter_client(
        consent=consent, distinct_id=distinct_id, api_key=_resolve_openrouter_api_key()
    )
    extra_kwargs = {"posthog_distinct_id": distinct_id} if consent else {}
    usage: dict = {}

    def _capture_usage(response) -> None:
        usage["input_tokens"] = response.usage.prompt_tokens
        usage["output_tokens"] = response.usage.completion_tokens

    with timed_checkpoint("t_llm_call"):
        taxon_filters = resolve_taxon_filters(
            query, client, on_response=_capture_usage, **extra_kwargs
        )
    return taxon_filters, usage


@router.post("/api/query")
@limiter.limit("10/minute")
def submit_query(request: Request, body: QueryRequest):
    log_query_submitted(body.query, body.distinctId, body.polygon)

    if not try_consume_daily_budget(datetime.now(tz=timezone.utc).date()):
        log_query_outcome(
            body.query, "daily_limit_reached", distinct_id=body.distinctId, guardrail="daily_limit"
        )
        return JSONResponse(
            status_code=429,
            content={"error": "daily_limit_reached", "message": DAILY_LIMIT_MESSAGE},
        )

    taxon_filters, usage = _resolve_taxon_filters(body.query, body.distinctId, body.consent)
    log_llm_taxon_filters_resolved(body.query, body.distinctId, taxon_filters)
    if not taxon_filters:
        log_query_outcome(body.query, "unresolved", distinct_id=body.distinctId, **usage)
        return {"status": "unresolved", "message": UNRESOLVED_MESSAGE}

    in_budget, over_budget = (
        taxon_filters[:MAX_TAXON_FILTERS],
        taxon_filters[MAX_TAXON_FILTERS:],
    )
    unresolved_groups = [f["taxonValue"] for f in over_budget]

    cells = candidate_cells(body.polygon)
    con = get_connection_cursor()
    groups = [
        query_species_for_filter(con, FACT_PARQUET_PATH, DIMENSION_PARQUET_PATH, f, cells)
        for f in in_budget
    ]
    non_empty_groups = [g for g in groups if g]
    species = (
        _select_species_across_groups(non_empty_groups, TOP_SPECIES_COUNT)
        if non_empty_groups
        else []
    )

    log_species_selected(body.query, body.distinctId, species)

    if not species:
        log_query_outcome(
            body.query, "no_results", distinct_id=body.distinctId, gbif_result_count=0, **usage
        )
        return {
            "status": "no_results",
            "taxonFilters": in_budget,
            "unresolvedGroups": unresolved_groups,
            "message": NO_RESULTS_MESSAGE,
        }

    center_lat, center_lon = polygon_centroid(body.polygon)
    ordered_species = order_waypoints(species, center_lat, center_lon)
    log_waypoints_ordered(body.query, body.distinctId, ordered_species)
    ordered_species = enrich_species(ordered_species)
    log_species_enriched(body.query, body.distinctId, ordered_species)

    log_query_outcome(
        body.query, "resolved", distinct_id=body.distinctId, gbif_result_count=len(species), **usage
    )
    return {
        "status": "resolved",
        "taxonFilters": in_budget,
        "unresolvedGroups": unresolved_groups,
        "species": ordered_species,
        "message": RESOLVED_MESSAGE,
    }
