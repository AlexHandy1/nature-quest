from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from models.query import QueryRequest
from services import ai_observability
from services.anthropic_client import resolve_api_key, resolve_taxon_filters
from services.gbif_client import (
    GBIF_POLYGON,
    GbifUnavailableError,
    fetch_top_species,
    polygon_centroid,
)
from services.logging_client import (
    log_llm_taxon_filters_resolved,
    log_query_outcome,
    log_query_submitted,
    log_species_selected,
    log_waypoints_ordered,
)
from services.query_budget import try_consume_daily_budget
from services.rate_limiter import limiter
from services.taxon_resolution import resolve_taxon_key
from services.waypoints import order_waypoints

router = APIRouter()

UNRESOLVED_MESSAGE = "Sorry, we couldn't match that to a category we support yet — try something like 'birds' or 'plants'."
NO_RESULTS_MESSAGE = "We understood your request, but didn't find anything for it here right now."
GBIF_UNAVAILABLE_MESSAGE = "We're having trouble reaching nature data right now — try again shortly."
DAILY_LIMIT_MESSAGE = "We've reached today's limit for this feature — please try again tomorrow."
RESOLVED_MESSAGE = "This is an early preview of a much bigger nature-walk experience to come."
MAX_TAXON_FILTERS = 10
MAX_CONCURRENT_GBIF_REQUESTS = 3
# Derived from GBIF_POLYGON (fixed to Retiro Park for this slice) so it stays
# in sync with the search area rather than being a second hardcoded constant.
CENTER_LAT, CENTER_LON = polygon_centroid(GBIF_POLYGON)


def _resolve_taxon_filters(query: str, distinct_id: str, consent: bool) -> tuple[list[dict], dict]:
    client = ai_observability.build_client(
        consent=consent, distinct_id=distinct_id, api_key=resolve_api_key()
    )
    extra_kwargs = {"posthog_distinct_id": distinct_id} if consent else {}
    usage: dict = {}

    def _capture_usage(response) -> None:
        usage["input_tokens"] = response.usage.input_tokens
        usage["output_tokens"] = response.usage.output_tokens

    taxon_filters = resolve_taxon_filters(
        query, client, on_response=_capture_usage, **extra_kwargs
    )
    return taxon_filters, usage


@router.post("/api/query")
@limiter.limit("10/minute")
def submit_query(request: Request, body: QueryRequest):
    log_query_submitted(body.query, body.distinctId)

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
    resolved, unresolved_groups = _resolve_taxon_keys(in_budget)
    unresolved_groups += [f["taxonValue"] for f in over_budget]
    if not resolved:
        log_query_outcome(body.query, "unresolved", distinct_id=body.distinctId, **usage)
        return {"status": "unresolved", "message": UNRESOLVED_MESSAGE}

    try:
        species = fetch_top_species(
            [{"taxon_rank": r["taxonRank"], "taxon_key": r["taxon_key"]} for r in resolved]
        )
    except GbifUnavailableError:
        log_query_outcome(body.query, "gbif_unavailable", distinct_id=body.distinctId, **usage)
        return JSONResponse(
            status_code=502,
            content={"status": "gbif_unavailable", "message": GBIF_UNAVAILABLE_MESSAGE},
        )

    log_species_selected(body.query, body.distinctId, species)

    resolved_filters = [
        {"taxonRank": r["taxonRank"], "taxonValue": r["taxonValue"]} for r in resolved
    ]

    if not species:
        log_query_outcome(body.query, "no_results", distinct_id=body.distinctId, gbif_result_count=0, **usage)
        return {
            "status": "no_results",
            "taxonFilters": resolved_filters,
            "unresolvedGroups": unresolved_groups,
            "message": NO_RESULTS_MESSAGE,
        }

    ordered_species = order_waypoints(species, CENTER_LAT, CENTER_LON)
    log_waypoints_ordered(body.query, body.distinctId, ordered_species)

    log_query_outcome(
        body.query, "resolved", distinct_id=body.distinctId, gbif_result_count=len(species), **usage
    )
    return {
        "status": "resolved",
        "taxonFilters": resolved_filters,
        "unresolvedGroups": unresolved_groups,
        "species": [
            {k: v for k, v in s.items() if k != "clustering"} for s in ordered_species
        ],
        "message": RESOLVED_MESSAGE,
    }


def _resolve_taxon_keys(taxon_filters: list[dict]) -> tuple[list[dict], list[str]]:
    if not taxon_filters:
        return [], []

    max_workers = min(len(taxon_filters), MAX_CONCURRENT_GBIF_REQUESTS)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        taxon_keys = list(
            executor.map(
                lambda f: resolve_taxon_key(f["taxonRank"], f["taxonValue"]), taxon_filters
            )
        )

    resolved = []
    unresolved_groups = []
    for taxon_filter, taxon_key in zip(taxon_filters, taxon_keys):
        if taxon_key is None:
            unresolved_groups.append(taxon_filter["taxonValue"])
        else:
            resolved.append({**taxon_filter, "taxon_key": taxon_key})
    return resolved, unresolved_groups
