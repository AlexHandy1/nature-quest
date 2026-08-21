from pydantic import BaseModel, Field

from services.gbif_client import TOP_SPECIES_COUNT


MAX_NAME_LENGTH = 500
MAX_EXTRACT_LENGTH = 2000


class SpeciesInput(BaseModel):
    common_name: str = Field(max_length=MAX_NAME_LENGTH)
    species: str = Field(max_length=MAX_NAME_LENGTH)
    hotspot_lat: float
    hotspot_lon: float
    extract: str | None = Field(default=None, max_length=MAX_EXTRACT_LENGTH)


class NarrateRequest(BaseModel):
    species: list[SpeciesInput] = Field(min_length=TOP_SPECIES_COUNT, max_length=TOP_SPECIES_COUNT)
    distinctId: str
    consent: bool = False
