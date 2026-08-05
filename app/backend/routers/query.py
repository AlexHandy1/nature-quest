from fastapi import APIRouter

from models.query import QueryRequest

router = APIRouter()


@router.post("/api/query")
def submit_query(request: QueryRequest):
    return {"status": "unresolved", "message": "not yet implemented"}
