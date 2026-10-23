from jobs.monthly_gbif_update_common_names_cache import (
    bootstrap_cache_from_dimension,
    merge_into_cache,
    new_entrants,
)


def test_new_entrants_returns_specieskeys_not_already_in_the_cache():
    top_n = ["SP1", "SP2", "SP3"]
    existing_cache = {"SP1": "Pica pica"}

    result = new_entrants(top_n, existing_cache)

    assert result == ["SP2", "SP3"]


def test_new_entrants_is_empty_when_every_top_n_specieskey_is_already_cached():
    top_n = ["SP1", "SP2"]
    existing_cache = {"SP1": "Pica pica", "SP2": "House Sparrow"}

    result = new_entrants(top_n, existing_cache)

    assert result == []


def test_merge_into_cache_adds_newly_fetched_entries():
    existing_cache = {"SP1": "Pica pica"}
    newly_fetched = {"SP2": "House Sparrow"}

    result = merge_into_cache(existing_cache, newly_fetched)

    assert result == {"SP1": "Pica pica", "SP2": "House Sparrow"}


def test_merge_into_cache_never_overwrites_an_existing_entry():
    existing_cache = {"SP1": "Pica pica"}
    newly_fetched = {"SP1": "Eurasian Magpie"}

    result = merge_into_cache(existing_cache, newly_fetched)

    assert result == {"SP1": "Pica pica"}


def test_bootstrap_cache_from_dimension_keeps_rows_with_a_common_name():
    dimension_rows = [
        {"specieskey": "SP1", "common_name": "Pica pica"},
        {"specieskey": "SP2", "common_name": "House Sparrow"},
    ]

    result = bootstrap_cache_from_dimension(dimension_rows)

    assert result == {"SP1": "Pica pica", "SP2": "House Sparrow"}


def test_bootstrap_cache_from_dimension_skips_rows_with_no_common_name():
    dimension_rows = [
        {"specieskey": "SP1", "common_name": "Pica pica"},
        {"specieskey": "SP2", "common_name": None},
    ]

    result = bootstrap_cache_from_dimension(dimension_rows)

    assert result == {"SP1": "Pica pica"}
