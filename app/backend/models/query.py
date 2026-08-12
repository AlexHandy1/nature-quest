import math

from pydantic import BaseModel, Field, field_validator

from services.gbif_client import parse_polygon_vertices

MAX_QUERY_LENGTH = 300
MIN_POLYGON_VERTICES = 3
MAX_POLYGON_AREA_KM2 = 25.0
KM_PER_DEGREE_LAT = 111.0


def _bounding_area_km2(vertices: list[tuple[float, float]]) -> float:
    lons = [lon for lon, _ in vertices]
    lats = [lat for _, lat in vertices]
    mean_lat = sum(lats) / len(lats)
    height_km = (max(lats) - min(lats)) * KM_PER_DEGREE_LAT
    width_km = (max(lons) - min(lons)) * KM_PER_DEGREE_LAT * math.cos(math.radians(mean_lat))
    return height_km * width_km


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
        if _bounding_area_km2(vertices) > MAX_POLYGON_AREA_KM2:
            raise ValueError(f"polygon bounding area must not exceed {MAX_POLYGON_AREA_KM2} km^2")
        return value
