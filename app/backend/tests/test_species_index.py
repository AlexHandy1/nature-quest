import pygeohash as pgh

from services.species_index import candidate_cells


def test_candidate_cells_includes_the_geohash_cell_at_the_polygon_center():
    # A small square polygon, well within a single geohash cell at
    # precision 6 (~0.6km x 1.2km at this latitude).
    polygon = "POLYGON((-3.6846 40.4148,-3.6846 40.4149,-3.6845 40.4149,-3.6845 40.4148,-3.6846 40.4148))"

    cells = candidate_cells(polygon, precision=6)

    assert pgh.encode(40.41485, -3.68455, precision=6) in cells
