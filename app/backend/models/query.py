from pydantic import BaseModel, Field, field_validator

MAX_QUERY_LENGTH = 300


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    distinctId: str

    @field_validator("query")
    @classmethod
    def reject_whitespace_only(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be empty or whitespace-only")
        return value
