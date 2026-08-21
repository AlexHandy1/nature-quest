from pydantic import BaseModel, Field

from services.gbif_client import TOP_SPECIES_COUNT


class SpeciesInput(BaseModel):
    common_name: str
    species: str
    hotspot_lat: float
    hotspot_lon: float
    extract: str | None = None


class NarrateRequest(BaseModel):
    species: list[SpeciesInput] = Field(min_length=TOP_SPECIES_COUNT, max_length=TOP_SPECIES_COUNT)
    distinctId: str
    consent: bool = False
