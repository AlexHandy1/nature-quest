"""
PROTOTYPE — test_species_common_names_batch.py
Light TDD per prototypes/README.md's stated convention: tests cover only
the deterministic, non-network logic (occurrence-coverage cutoff ranking,
common-name dedup, per-taxon fetch fan-out, dimension join). The live
ChecklistBank calls are validated manually against the real API, not here.
"""

import os
import tempfile

import pandas as pd

from species_common_names_batch import (
    build_common_name_map,
    compute_top_n_cutoff,
    fetch_common_names_for_taxa,
    join_common_names,
    pick_best_name,
    top_n_specieskeys,
)


def _write_fact_parquet(rows):
    fd, path = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    pd.DataFrame(rows, columns=["cell", "specieskey", "occurrence_count"]).to_parquet(path, index=False)
    return path


def test_pick_best_name_prefers_the_longest_option_among_real_candidates():
    # Real example (Pica pica): all candidates are legitimate Title Case
    # English names, so the longest/most-qualified one wins.
    names = ["Black-billed Magpie", "Common Magpie", "Eurasian Magpie", "European Magpie", "Magpie"]

    assert pick_best_name(names) == "Black-billed Magpie"


def test_pick_best_name_breaks_ties_alphabetically():
    names = ["Zebra Thing", "Aebra Thing"]  # same length, different first letter

    assert pick_best_name(names) == "Aebra Thing"


def test_pick_best_name_rejects_allcaps_short_codes():
    # Real example (Sturnus vulgaris): "COST" is a banding code, not a name.
    names = ["COST", "Starling", "Common Starling"]

    assert pick_best_name(names) == "Common Starling"


def test_pick_best_name_rejects_lowercase_starting_informal_names():
    # Real example (Columba livia): "pigeon" is informal/lowercase.
    names = ["pigeon", "Rock Dove", "Feral Pigeon"]

    assert pick_best_name(names) == "Feral Pigeon"


def test_pick_best_name_rejects_non_ascii_names():
    # Real example (Melanerpes carolinus): an Indigenous-language name was
    # mistagged as English in ChecklistBank's data.
    names = ["Tsiitkaalii bitlaaa halchiigii".replace("a", "ą"), "Red-bellied Woodpecker"]

    assert pick_best_name(names) == "Red-bellied Woodpecker"


def test_pick_best_name_falls_back_to_the_original_list_when_everything_is_junk():
    names = ["TV", "GRTI"]  # both look like banding codes

    assert pick_best_name(names) == "GRTI"


def test_build_common_name_map_dedupes_multiple_names_per_taxon_id():
    records = [
        {"taxonID": "4HPXM", "name": "Black-billed Magpie"},
        {"taxonID": "4HPXM", "name": "Magpie"},
        {"taxonID": "4HPXM", "name": "Eurasian Magpie"},
        {"taxonID": "QXMNH", "name": "Some Diatom"},
    ]

    result = build_common_name_map(records)

    assert result == {"4HPXM": "Black-billed Magpie", "QXMNH": "Some Diatom"}


def test_join_common_names_adds_the_column_and_leaves_missing_rows_null():
    dim_rows = [
        {"specieskey": "4HPXM", "species": "Pica pica"},
        {"specieskey": "QXMNH", "species": "Nitzschia lacuum"},
    ]
    common_name_map = {"4HPXM": "Magpie"}

    result = join_common_names(dim_rows, common_name_map)

    assert result[0]["common_name"] == "Magpie"
    assert result[1]["common_name"] is None


def test_compute_top_n_cutoff_finds_the_smallest_n_covering_the_target_share():
    # species A=70, B=20, C=6, D=4 occurrences; total=100
    # cumulative: A=70% B=90% C=96% D=100% -> 90% coverage needs top 2 (A,B)
    path = _write_fact_parquet([
        ("c1", "A", 70),
        ("c1", "B", 20),
        ("c1", "C", 6),
        ("c1", "D", 4),
    ])

    n = compute_top_n_cutoff(path, coverage=0.90)

    assert n == 2
    os.remove(path)


def test_compute_top_n_cutoff_sums_occurrence_count_across_multiple_cells():
    # species A appears in two cells (30+30=60), species B in one cell (40)
    path = _write_fact_parquet([
        ("c1", "A", 30),
        ("c2", "A", 30),
        ("c1", "B", 40),
    ])

    n = compute_top_n_cutoff(path, coverage=0.55)

    # A's combined total (60) alone already covers 60% >= 55%
    assert n == 1
    os.remove(path)


def test_top_n_specieskeys_returns_the_highest_occurrence_species_first():
    path = _write_fact_parquet([
        ("c1", "A", 70),
        ("c1", "B", 20),
        ("c1", "C", 6),
        ("c1", "D", 4),
    ])

    result = top_n_specieskeys(2, path)

    assert result == ["A", "B"]
    os.remove(path)


def test_fetch_common_names_for_taxa_attaches_taxon_id_to_each_name_row():
    responses = {
        "4HPXM": [{"name": "Magpie"}, {"name": "Common Magpie"}],
        "QXMNH": [],  # no English name found for this taxon
    }

    def fake_fetch(dataset_key, taxon_id):
        return responses[taxon_id]

    records = fetch_common_names_for_taxa(315557, ["4HPXM", "QXMNH"], fetch_fn=fake_fetch, max_workers=2)

    assert sorted((r["taxonID"], r["name"]) for r in records) == [
        ("4HPXM", "Common Magpie"),
        ("4HPXM", "Magpie"),
    ]
