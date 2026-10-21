from pydantic import BaseModel, Field, field_validator

from services.species_index import parse_polygon_vertices

MAX_QUERY_LENGTH = 300
MIN_POLYGON_VERTICES = 3


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    distinctId: str
    consent: bool = False
    polygon: str

    @field_validator("query")
    @classmethod
    def reject_whitespace_only(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be empty or whitespace-only")
        return value

    @field_validator("polygon")
    @classmethod
    def validate_polygon(cls, value: str) -> str:
        vertices = parse_polygon_vertices(value)
        if len(vertices) < MIN_POLYGON_VERTICES:
            raise ValueError(f"polygon must have at least {MIN_POLYGON_VERTICES} vertices")
        return value
