import math

EARTH_RADIUS_M = 6371000


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi, d_lambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def order_waypoints(species: list[dict], center_lat: float, center_lon: float) -> list[dict]:
    remaining = list(range(len(species)))
    cur_lat, cur_lon = center_lat, center_lon

    result = []
    while remaining:
        nearest = min(
            remaining,
            key=lambda i: _haversine_m(cur_lat, cur_lon, species[i]["hotspot_lat"], species[i]["hotspot_lon"]),
        )
        nearest_lat, nearest_lon = species[nearest]["hotspot_lat"], species[nearest]["hotspot_lon"]
        distance_m = _haversine_m(cur_lat, cur_lon, nearest_lat, nearest_lon)
        result.append({**species[nearest], "distance_m": distance_m})
        remaining.remove(nearest)
        cur_lat, cur_lon = nearest_lat, nearest_lon

    return result
