from fastapi import APIRouter, Request

from models.query import QueryRequest
from services.rate_limiter import limiter

router = APIRouter()


@router.post("/api/query")
@limiter.limit("10/minute")
def submit_query(request: Request, body: QueryRequest):
    return {"status": "unresolved", "message": "not yet implemented"}
