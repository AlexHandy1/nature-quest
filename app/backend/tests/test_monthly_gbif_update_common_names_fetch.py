from jobs.monthly_gbif_update_common_names_fetch import (
    build_common_name_map,
    fetch_common_names_for_taxa,
    pick_best_name,
)


def test_pick_best_name_rejects_a_banding_code_in_favor_of_a_real_name():
    names = ["COST", "American Goldfinch"]

    result = pick_best_name(names)

    assert result == "American Goldfinch"


def test_pick_best_name_rejects_an_informal_lowercase_entry_even_if_longer():
    names = ["pigeon", "Dove"]

    result = pick_best_name(names)

    assert result == "Dove"


def test_pick_best_name_rejects_a_non_ascii_mistagged_name_even_if_longer():
    names = ["Niimíipuu Péekstamit Name", "Red Fox"]

    result = pick_best_name(names)

    assert result == "Red Fox"


def test_pick_best_name_falls_back_to_the_original_list_when_every_candidate_is_junk():
    names = ["COST", "GRTI"]

    result = pick_best_name(names)

    assert result in names


def test_build_common_name_map_dedups_multiple_names_per_taxon():
    records = [
        {"taxonID": "SP1", "name": "COST"},
        {"taxonID": "SP1", "name": "American Goldfinch"},
        {"taxonID": "SP2", "name": "House Sparrow"},
    ]

    result = build_common_name_map(records)

    assert result == {"SP1": "American Goldfinch", "SP2": "House Sparrow"}


def test_fetch_common_names_for_taxa_collects_records_from_every_taxon():
    def fake_fetch(dataset_key, taxon_id):
        return [{"name": f"Name for {taxon_id}"}]

    records = fetch_common_names_for_taxa(315557, ["SP1", "SP2"], fetch_fn=fake_fetch)

    assert sorted(records, key=lambda r: r["taxonID"]) == [
        {"taxonID": "SP1", "name": "Name for SP1"},
        {"taxonID": "SP2", "name": "Name for SP2"},
    ]
