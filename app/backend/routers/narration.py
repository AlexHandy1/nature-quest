import base64
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from models.narration import NarrateRequest
from services.anthropic_client import build_client
from services.logging_client import log_narration_outcome
from services.narration import generate_narrative
from services.narration_budget import try_consume_daily_budget
from services.rate_limiter import limiter
from services.tts import resolve_api_key, synthesize_speech

router = APIRouter()

TTS_UNAVAILABLE_MESSAGE = "We couldn't generate audio for this walk right now — try again shortly."
DAILY_LIMIT_MESSAGE = "We've reached today's limit for this feature — please try again tomorrow."


def _resolve_openrouter_api_key() -> str | None:
    return resolve_api_key() or os.environ.get("OPENROUTER_API_KEY")


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
    narrative = generate_narrative(species_list, build_client())

    try:
        audio_bytes = synthesize_speech(narrative, api_key=_resolve_openrouter_api_key())
    except RuntimeError:
        log_narration_outcome("tts_unavailable", distinct_id=body.distinctId)
        return JSONResponse(
            status_code=502,
            content={"status": "tts_unavailable", "message": TTS_UNAVAILABLE_MESSAGE},
        )

    log_narration_outcome("resolved", distinct_id=body.distinctId)
    return {
        "narrative": narrative,
        "audio": base64.b64encode(audio_bytes).decode("ascii"),
    }
