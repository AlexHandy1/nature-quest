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


def test_large_polygon_is_accepted_with_no_area_cap():
    # ~55km x 55km bounding box - used to exceed the (now-removed) 25 km^2
    # GBIF-motivated cap. No area cap applies to the local DuckDB index.
    large_polygon = (
        "POLYGON((-4.0 40.0,-3.5 40.0,-3.5 40.5,-4.0 40.5,-4.0 40.0))"
    )

    request = QueryRequest(query="birds", distinctId="anon-1", polygon=large_polygon)

    assert request.polygon == large_polygon
