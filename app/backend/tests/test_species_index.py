import duckdb
import pygeohash as pgh

from services.species_index import candidate_cells, query_species_for_filter


def test_candidate_cells_includes_the_geohash_cell_at_the_polygon_center():
    # A small square polygon, well within a single geohash cell at
    # precision 6 (~0.6km x 1.2km at this latitude).
    polygon = "POLYGON((-3.6846 40.4148,-3.6846 40.4149,-3.6845 40.4149,-3.6845 40.4148,-3.6846 40.4148))"

    cells = candidate_cells(polygon, precision=6)

    assert pgh.encode(40.41485, -3.68455, precision=6) in cells


def _write_fixture_index(tmp_path):
    fact_path = str(tmp_path / "fact.parquet")
    dim_path = str(tmp_path / "dimension.parquet")
    con = duckdb.connect()
    con.execute(f"""
        COPY (
            SELECT * FROM (VALUES
                ('u4pruy', 'SP1', 10),
                ('u4pruy', 'SP2', 25),
                ('u4pruz', 'SP1', 5)
            ) AS t(cell, specieskey, occurrence_count)
        ) TO '{fact_path}' (FORMAT PARQUET)
    """)
    con.execute(f"""
        COPY (
            SELECT * FROM (VALUES
                ('SP1', 'Pica pica', 'Animalia', 'Aves'),
                ('SP2', 'Passer domesticus', 'Animalia', 'Aves')
            ) AS t(specieskey, species, kingdom, class)
        ) TO '{dim_path}' (FORMAT PARQUET)
    """)
    return fact_path, dim_path


def test_ranks_species_by_total_occurrence_count_within_the_candidate_cells(tmp_path):
    fact_path, dim_path = _write_fixture_index(tmp_path)
    con = duckdb.connect()

    species = query_species_for_filter(
        con,
        fact_path,
        dim_path,
        {"taxonRank": "class", "taxonValue": "Aves"},
        cells=["u4pruy", "u4pruz"],
    )

    assert [s["species"] for s in species] == ["Passer domesticus", "Pica pica"]
    assert [s["count"] for s in species] == [25, 15]


def test_returns_each_species_hotspot_as_its_highest_occurrence_cell(tmp_path):
    fact_path, dim_path = _write_fixture_index(tmp_path)
    con = duckdb.connect()

    species = query_species_for_filter(
        con,
        fact_path,
        dim_path,
        {"taxonRank": "class", "taxonValue": "Aves"},
        cells=["u4pruy", "u4pruz"],
    )

    # Pica pica (SP1) has 10 occurrences in u4pruy vs 5 in u4pruz - u4pruy wins.
    pica = next(s for s in species if s["species"] == "Pica pica")
    assert pica["cell"] == "u4pruy"
    lat, lon, lat_err, lon_err = pgh.decode_exactly("u4pruy")
    assert pica["hotspot_lat"] == lat
    assert pica["hotspot_lon"] == lon
    assert pica["cell_lat_err"] == lat_err
    assert pica["cell_lon_err"] == lon_err

