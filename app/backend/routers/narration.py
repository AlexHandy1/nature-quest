import base64
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from models.narration import NarrateRequest
from services import ai_observability
from services.anthropic_client import resolve_api_key as resolve_anthropic_api_key
from services.logging_client import log_narration_outcome
from services.narration import generate_narrative
from services.narration_budget import try_consume_daily_budget
from services.rate_limiter import limiter
from services.tts import resolve_api_key as resolve_openrouter_api_key
from services.tts import synthesize_speech

router = APIRouter()

TTS_UNAVAILABLE_MESSAGE = "We couldn't generate audio for this walk right now — try again shortly."
DAILY_LIMIT_MESSAGE = "We've reached today's limit for this feature — please try again tomorrow."
NARRATION_DECLINED_MESSAGE = "We couldn't generate a narrative for this walk."


def _resolve_openrouter_api_key() -> str | None:
    return resolve_openrouter_api_key() or os.environ.get("OPENROUTER_API_KEY")


def _generate_narrative(species_list: list[dict], distinct_id: str, consent: bool) -> tuple[str, dict]:
    client = ai_observability.build_client(
        consent=consent, distinct_id=distinct_id, api_key=resolve_anthropic_api_key()
    )
    extra_kwargs = {"posthog_distinct_id": distinct_id} if consent else {}
    usage: dict = {}

    def _capture_usage(response) -> None:
        usage["input_tokens"] = response.usage.input_tokens
        usage["output_tokens"] = response.usage.output_tokens

    narrative = generate_narrative(species_list, client, on_response=_capture_usage, **extra_kwargs)
    return narrative, usage


@router.post("/api/narrate")
@limiter.limit("10/minute")
def narrate(request: Request, body: NarrateRequest):
    if not try_consume_daily_budget(datetime.now(tz=timezone.utc).date()):
        log_narration_outcome("daily_limit_reached", distinct_id=body.distinctId, guardrail="daily_limit")
        return JSONResponse(
            status_code=429,
            content={"error": "daily_limit_reached", "message": DAILY_LIMIT_MESSAGE},
        )

    species_list = [s.model_dump() for s in body.species]
    narrative, usage = _generate_narrative(species_list, body.distinctId, body.consent)

    if narrative is None:
        log_narration_outcome("declined", distinct_id=body.distinctId, **usage)
        return JSONResponse(
            status_code=422,
            content={"status": "narration_declined", "message": NARRATION_DECLINED_MESSAGE},
        )

    try:
        audio_bytes = synthesize_speech(narrative, api_key=_resolve_openrouter_api_key())
    except RuntimeError:
        log_narration_outcome("tts_unavailable", distinct_id=body.distinctId, **usage)
        return JSONResponse(
            status_code=502,
            content={"status": "tts_unavailable", "message": TTS_UNAVAILABLE_MESSAGE},
        )

    log_narration_outcome("resolved", distinct_id=body.distinctId, **usage)
    return {
        "narrative": narrative,
        "audio": base64.b64encode(audio_bytes).decode("ascii"),
    }
