import pytest
from pydantic import ValidationError

from models.query import QueryRequest

RETIRO_POLYGON = (
    "POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,"
    "-3.676 40.41148,-3.68002 40.42163,-3.68876 40.4199))"
)


def test_missing_polygon_raises_validation_error():
    with pytest.raises(ValidationError):
        QueryRequest(query="birds", distinctId="anon-1")


def test_valid_polygon_is_accepted():
    request = QueryRequest(query="birds", distinctId="anon-1", polygon=RETIRO_POLYGON)

    assert request.polygon == RETIRO_POLYGON


def test_polygon_with_fewer_than_three_vertices_raises_validation_error():
    two_vertex_polygon = "POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.68876 40.4199))"

    with pytest.raises(ValidationError):
        QueryRequest(query="birds", distinctId="anon-1", polygon=two_vertex_polygon)


def test_polygon_well_above_the_old_gbif_cap_is_now_accepted():
    # ~30km x 23km bounding box (~660 km^2) - well above the old, removed
    # 25 km^2 GBIF-motivated cap, comfortably under the new one.
    polygon = "POLYGON((-3.85 40.0,-3.5 40.0,-3.5 40.2,-3.85 40.2,-3.85 40.0))"

    request = QueryRequest(query="birds", distinctId="anon-1", polygon=polygon)

    assert request.polygon == polygon


def test_polygon_exceeding_the_new_area_cap_raises_validation_error():
    # ~111km x 85km bounding box (~9,400 km^2), well above the 2,000 km^2
    # cap. This cap exists for a different reason than the old one: it
    # bounds services.species_index.candidate_cells' own Python-side
    # grid-sampling cost/memory, not GBIF's rate limits (see security
    # review, build_duckdb_migration branch) - measured empirically to
    # stay well under 1s even at extreme (near-Arctic) latitudes.
    oversized_polygon = "POLYGON((-4.0 40.0,-3.0 40.0,-3.0 41.0,-4.0 41.0,-4.0 40.0))"

    with pytest.raises(ValidationError):
        QueryRequest(query="birds", distinctId="anon-1", polygon=oversized_polygon)
