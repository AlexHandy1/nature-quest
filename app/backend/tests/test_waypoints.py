import pytest

from services.waypoints import order_waypoints


def _species(name, lat, lon):
    return {"species": name, "hotspot_lat": lat, "hotspot_lon": lon}


def test_orders_species_nearest_neighbour_from_the_center_point():
    species = [
        _species("Far", 0.0, 2.0),
        _species("Near", 0.0, 0.5),
        _species("Mid", 0.0, 1.0),
    ]

    ordered = order_waypoints(species, center_lat=0.0, center_lon=0.0)

    assert [s["species"] for s in ordered] == ["Near", "Mid", "Far"]


def test_adds_distance_m_from_the_previous_waypoint_or_center_for_the_first():
    species = [
        _species("Near", 0.0, 0.5),
        _species("Mid", 0.0, 1.0),
    ]

    ordered = order_waypoints(species, center_lat=0.0, center_lon=0.0)

    # 1 degree of longitude at the equator is ~111,320m, so 0.5deg ~ 55,660m
    assert ordered[0]["distance_m"] == pytest.approx(55660, rel=0.01)
    assert ordered[1]["distance_m"] == pytest.approx(55660, rel=0.01)


def test_returns_empty_list_for_no_species():
    assert order_waypoints([], center_lat=0.0, center_lon=0.0) == []
