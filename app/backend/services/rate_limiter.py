from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from services.logging_client import log_query_outcome

limiter = Limiter(key_func=get_remote_address)


async def handle_rate_limit_exceeded(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    body = await request.json()
    log_query_outcome(
        body.get("query"), "rate_limited", distinct_id=body.get("distinctId"), guardrail="rate_limit"
    )
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limited",
            "message": "You're sending requests too quickly — please wait a moment and try again.",
        },
    )
