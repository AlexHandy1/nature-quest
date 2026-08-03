from fastapi import APIRouter, status

from models.interest import InterestSubmission
from services.logging_client import log_interest_submission

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/api/interest", status_code=status.HTTP_201_CREATED)
def submit_interest(submission: InterestSubmission):
    log_interest_submission(query=submission.query)
    return {"status": "received"}
