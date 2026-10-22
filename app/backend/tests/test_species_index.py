import os
from unittest.mock import patch

import duckdb
import pygeohash as pgh
import pytest

from services.species_index import (
    _select_species_across_groups,
    candidate_cells,
    open_index_connection,
    polygon_centroid,
    query_species_for_filter,
)

RETIRO_POLYGON = (
    "POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,"
    "-3.676 40.41148,-3.68002 40.42163,-3.68876 40.4199))"
)


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
                ('SP1', 'Pica pica', 'Animalia', 'Aves', NULL),
                ('SP2', 'Passer domesticus', 'Animalia', 'Aves', 'House Sparrow')
            ) AS t(specieskey, species, kingdom, class, common_name)
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


def test_returns_the_hotspot_cells_own_count_separately_from_the_area_total(tmp_path):
    fact_path, dim_path = _write_fixture_index(tmp_path)
    con = duckdb.connect()

    species = query_species_for_filter(
        con,
        fact_path,
        dim_path,
        {"taxonRank": "class", "taxonValue": "Aves"},
        cells=["u4pruy", "u4pruz"],
    )

    # Pica pica: 10 occurrences in its hotspot cell (u4pruy) vs 15 total
    # across both candidate cells (10 + 5 in u4pruz).
    pica = next(s for s in species if s["species"] == "Pica pica")
    assert pica["hotspot_count"] == 10
    assert pica["count"] == 15


def test_returns_the_dimension_tables_common_name_falling_back_to_null(tmp_path):
    fact_path, dim_path = _write_fixture_index(tmp_path)
    con = duckdb.connect()

    species = query_species_for_filter(
        con,
        fact_path,
        dim_path,
        {"taxonRank": "class", "taxonValue": "Aves"},
        cells=["u4pruy", "u4pruz"],
    )

    sparrow = next(s for s in species if s["species"] == "Passer domesticus")
    pica = next(s for s in species if s["species"] == "Pica pica")
    assert sparrow["common_name"] == "House Sparrow"
    assert pica["common_name"] is None


def test_open_index_connection_returns_a_working_connection_locally():
    with patch.dict(os.environ, {}, clear=True):
        con = open_index_connection()

    assert con.execute("SELECT 1").fetchone() == (1,)


def test_open_index_connection_configures_gcs_auth_on_cloud_run():
    with (
        patch.dict(os.environ, {"K_SERVICE": "nature-quest-backend"}),
        patch(
            "services.species_index.fetch_secret",
            side_effect=["fake-key-id", "fake-secret"],
        ),
    ):
        con = open_index_connection()

    secrets = con.execute("SELECT type FROM duckdb_secrets()").fetchall()
    assert secrets == [("gcs",)]


def test_open_index_connection_logs_only_a_connect_checkpoint_locally(caplog):
    with caplog.at_level("INFO"), patch.dict(os.environ, {}, clear=True):
        open_index_connection()

    checkpoints = [r.checkpoint for r in caplog.records if r.message == "timing_checkpoint"]
    assert checkpoints == ["t_connect"]


def test_open_index_connection_logs_the_gcs_setup_checkpoints_on_cloud_run(caplog):
    with (
        caplog.at_level("INFO"),
        patch.dict(os.environ, {"K_SERVICE": "nature-quest-backend"}),
        patch(
            "services.species_index.fetch_secret",
            side_effect=["fake-key-id", "fake-secret"],
        ),
    ):
        open_index_connection()

    checkpoints = [r.checkpoint for r in caplog.records if r.message == "timing_checkpoint"]
    assert checkpoints == [
        "t_connect",
        "t_install_load_httpfs",
        "t_fetch_secret_gcs_key_id",
        "t_fetch_secret_gcs_secret",
        "t_create_secret",
    ]


def test_query_species_for_filter_logs_a_query_execute_checkpoint(tmp_path, caplog):
    fact_path, dim_path = _write_fixture_index(tmp_path)
    con = duckdb.connect()

    with caplog.at_level("INFO"):
        query_species_for_filter(
            con,
            fact_path,
            dim_path,
            {"taxonRank": "class", "taxonValue": "Aves"},
            cells=["u4pruy", "u4pruz"],
        )

    checkpoints = [r.checkpoint for r in caplog.records if r.message == "timing_checkpoint"]
    assert checkpoints == ["t_query_execute"]


def test_polygon_centroid_averages_the_polygon_vertices():
    # A simple closed square: (0,0),(0,2),(2,2),(2,0),(0,0) in "lon lat" WKT
    # order. Average of the 4 unique vertices (excluding the closing repeat
    # of the first point) is (1, 1).
    square = "POLYGON((0 0,0 2,2 2,2 0,0 0))"

    lat, lon = polygon_centroid(square)

    assert lat == pytest.approx(1.0)
    assert lon == pytest.approx(1.0)


def test_polygon_centroid_of_the_retiro_polygon():
    lat, lon = polygon_centroid(RETIRO_POLYGON)

    assert lat == pytest.approx(40.4137, abs=0.001)
    assert lon == pytest.approx(-3.6826, abs=0.001)


def test_select_species_across_groups_quota_selects_then_sorts_by_count_descending():
    groups = [
        [
            {"species": "Turdus merula", "count": 3},
            {"species": "Passer domesticus", "count": 2},
            {"species": "Corvus monedula", "count": 1},
        ],
        [
            {"species": "Quercus ilex", "count": 2},
            {"species": "Pinus pinea", "count": 1},
        ],
    ]

    selected = _select_species_across_groups(groups, target_total=5)

    # Quota selects 3 from the first group + 2 from the second for fairness,
    # but the final list is sorted by count descending, not by group. Passer
    # and Quercus tie at count 2 - stable sort keeps Passer (first pre-sort)
    # ahead of Quercus.
    assert [s["species"] for s in selected] == [
        "Turdus merula",
        "Passer domesticus",
        "Quercus ilex",
        "Corvus monedula",
        "Pinus pinea",
    ]
    assert [s["count"] for s in selected] == [3, 2, 2, 1, 1]


def test_select_species_across_groups_sorts_the_final_merged_selection_by_count_descending():
    # Reproduces the reported bug: a low-count species from the first-
    # mentioned group (e.g. a lizard, quota-selected for fairness) was
    # appearing ahead of a much-higher-count species from a later group
    # (e.g. a turtle), because groups were concatenated in mention order
    # without a final re-sort by count.
    groups = [
        [{"species": "Podarcis virescens", "count": 1}],
        [
            {"species": "Trachemys scripta", "count": 121},
            {"species": "Graptemys pseudogeographica", "count": 16},
        ],
    ]

    selected = _select_species_across_groups(groups, target_total=5)

    assert [s["species"] for s in selected] == [
        "Trachemys scripta",
        "Graptemys pseudogeographica",
        "Podarcis virescens",
    ]
    assert [s["count"] for s in selected] == [121, 16, 1]

