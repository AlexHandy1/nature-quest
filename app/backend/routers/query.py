from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from models.query import QueryRequest
from services import ai_observability
from services.anthropic_client import resolve_api_key, resolve_taxon_filter
from services.gbif_client import GbifUnavailableError, fetch_top_species
from services.logging_client import log_query_outcome
from services.query_budget import try_consume_daily_budget
from services.rate_limiter import limiter
from services.taxon_resolution import resolve_taxon_key

router = APIRouter()

UNRESOLVED_MESSAGE = "Sorry, we couldn't match that to a category we support yet — try something like 'birds' or 'plants'."
NO_RESULTS_MESSAGE = "We understood your request, but didn't find anything for it here right now."
GBIF_UNAVAILABLE_MESSAGE = "We're having trouble reaching nature data right now — try again shortly."
DAILY_LIMIT_MESSAGE = "We've reached today's limit for this feature — please try again tomorrow."
RESOLVED_MESSAGE = "This is an early preview of a much bigger nature-walk experience to come."


def _resolve_taxon_filter(query: str, distinct_id: str, consent: bool) -> tuple[dict | None, dict]:
    client = ai_observability.build_client(
        consent=consent, distinct_id=distinct_id, api_key=resolve_api_key()
    )
    extra_kwargs = {"posthog_distinct_id": distinct_id} if consent else {}
    usage: dict = {}

    def _capture_usage(response) -> None:
        usage["input_tokens"] = response.usage.input_tokens
        usage["output_tokens"] = response.usage.output_tokens

    taxon_filter = resolve_taxon_filter(
        query, client, on_response=_capture_usage, **extra_kwargs
    )
    return taxon_filter, usage


@router.post("/api/query")
@limiter.limit("10/minute")
def submit_query(request: Request, body: QueryRequest):
    if not try_consume_daily_budget(date.today()):
        log_query_outcome(body.query, "daily_limit_reached", guardrail="daily_limit")
        return JSONResponse(
            status_code=429,
            content={"error": "daily_limit_reached", "message": DAILY_LIMIT_MESSAGE},
        )

    taxon_filter, usage = _resolve_taxon_filter(body.query, body.distinctId, body.consent)
    if taxon_filter is None:
        log_query_outcome(body.query, "unresolved", **usage)
        return {"status": "unresolved", "message": UNRESOLVED_MESSAGE}

    taxon_key = resolve_taxon_key(taxon_filter["taxonRank"], taxon_filter["taxonValue"])
    if taxon_key is None:
        log_query_outcome(body.query, "unresolved", **usage)
        return {"status": "unresolved", "message": UNRESOLVED_MESSAGE}

    try:
        species = fetch_top_species(taxon_filter["taxonRank"], taxon_key)
    except GbifUnavailableError:
        log_query_outcome(body.query, "gbif_unavailable", **usage)
        return JSONResponse(
            status_code=502,
            content={"status": "gbif_unavailable", "message": GBIF_UNAVAILABLE_MESSAGE},
        )

    if not species:
        log_query_outcome(body.query, "no_results", gbif_result_count=0, **usage)
        return {
            "status": "no_results",
            "taxonRank": taxon_filter["taxonRank"],
            "taxonValue": taxon_filter["taxonValue"],
            "message": NO_RESULTS_MESSAGE,
        }

    log_query_outcome(body.query, "resolved", gbif_result_count=len(species), **usage)
    return {
        "status": "resolved",
        "taxonRank": taxon_filter["taxonRank"],
        "taxonValue": taxon_filter["taxonValue"],
        "species": species,
        "message": RESOLVED_MESSAGE,
    }
