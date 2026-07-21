"""
PROTOTYPE — test_intent_query_spike.py
Light TDD per PLANNING_INTENT_QUERY_210726.md §9: tests cover only the
deterministic, non-LLM, non-network logic (local cache lookup, GBIF
species/match validation, quota/round-robin species selection). The LLM
call and live GBIF calls are validated manually via the plan's §8 test
intents, not here.
"""

from intent_query_spike import (
    resolve_from_local_cache,
    select_species_across_groups,
    validate_species_match,
)


def test_resolves_a_known_kingdom_lay_term_to_its_cached_key():
    caches = {"kingdom": {"Plantae": 6}, "class": {}, "order": {}}

    key = resolve_from_local_cache("kingdom", "Plantae", caches)

    assert key == 6


def test_returns_no_match_for_a_taxon_value_not_in_the_local_cache():
    caches = {"kingdom": {"Plantae": 6}, "class": {}, "order": {}}

    key = resolve_from_local_cache("kingdom", "Fungi", caches)

    assert key is None


def test_returns_no_match_for_a_rank_with_no_local_cache_at_all():
    caches = {"kingdom": {"Plantae": 6}, "class": {}, "order": {}}

    key = resolve_from_local_cache("family", "Anatidae", caches)

    assert key is None


def test_accepts_an_exact_species_match_and_returns_its_resolved_key():
    response = {
        "matchType": "EXACT",
        "confidence": 94,
        "rank": "CLASS",
        "classKey": 212,
        "class": "Aves",
    }

    key = validate_species_match(response, requested_rank="class")

    assert key == 212


def test_accepts_a_fuzzy_match_that_meets_the_confidence_threshold():
    response = {
        "matchType": "FUZZY",
        "confidence": 85,
        "rank": "CLASS",
        "classKey": 216,
        "class": "Insecta",
    }

    key = validate_species_match(response, requested_rank="class")

    assert key == 216


def test_rejects_a_fuzzy_match_below_the_confidence_threshold():
    response = {
        "matchType": "FUZZY",
        "confidence": 84,
        "rank": "CLASS",
        "classKey": 216,
        "class": "Insecta",
    }

    key = validate_species_match(response, requested_rank="class")

    assert key is None


def test_rejects_a_response_with_no_match():
    response = {"confidence": 100, "matchType": "NONE", "synonym": False}

    key = validate_species_match(response, requested_rank="class")

    assert key is None


def test_splits_species_evenly_across_multiple_resolved_taxon_groups():
    birds = [{"species": f"bird-{i}"} for i in range(5)]
    plants = [{"species": f"plant-{i}"} for i in range(5)]

    selected = select_species_across_groups([birds, plants], target_total=4)

    assert len(selected) == 4
    assert sum(1 for s in selected if s["species"].startswith("bird")) == 2
    assert sum(1 for s in selected if s["species"].startswith("plant")) == 2


def test_splits_five_species_across_three_groups_as_evenly_as_possible():
    birds = [{"species": f"bird-{i}"} for i in range(5)]
    plants = [{"species": f"plant-{i}"} for i in range(5)]
    mammals = [{"species": f"mammal-{i}"} for i in range(5)]

    selected = select_species_across_groups([birds, plants, mammals], target_total=5)

    assert len(selected) == 5
    counts = {
        prefix: sum(1 for s in selected if s["species"].startswith(prefix))
        for prefix in ("bird", "plant", "mammal")
    }
    # 5 species / 3 groups -> quotas of 2, 2, 1 in group order, every group represented
    assert sorted(counts.values()) == [1, 2, 2]
    assert all(count >= 1 for count in counts.values())


def test_redistributes_unused_slots_when_a_group_has_fewer_species_than_its_quota():
    birds = [{"species": f"bird-{i}"} for i in range(5)]
    mammals = [{"species": "mammal-0"}]  # only 1 available, quota would be 2

    selected = select_species_across_groups([birds, mammals], target_total=4)

    # still returns 4 total species, not fewer, by taking birds' unused-by-mammals slot
    assert len(selected) == 4
    assert sum(1 for s in selected if s["species"].startswith("bird")) == 3
    assert sum(1 for s in selected if s["species"].startswith("mammal")) == 1


def test_redistributes_all_slots_when_a_group_is_completely_empty():
    birds = [{"species": f"bird-{i}"} for i in range(5)]
    mammals = []  # genuinely no matches in this group (the "surfaced to user" case)

    selected = select_species_across_groups([birds, mammals], target_total=4)

    assert len(selected) == 4
    assert all(s["species"].startswith("bird") for s in selected)


def test_returns_fewer_than_target_when_total_supply_across_groups_is_genuinely_insufficient():
    birds = [{"species": "bird-0"}]
    mammals = [{"species": "mammal-0"}]

    selected = select_species_across_groups([birds, mammals], target_total=5)

    # only 2 species exist across both groups combined - can't manufacture 5,
    # but must return everything available rather than hang or crash
    assert len(selected) == 2


def test_rejects_a_higherrank_match_even_with_high_confidence():
    # e.g. "Reptilia" at rank=CLASS resolves to a HIGHERRANK match
    # (falls back to phylum Chordata) rather than a genuine class-level hit —
    # must not be treated as a resolved filter. See PLANNING_INTENT_QUERY_210726.md §2.5.
    response = {
        "matchType": "HIGHERRANK",
        "confidence": 94,
        "rank": "PHYLUM",
        "phylumKey": 44,
        "phylum": "Chordata",
    }

    key = validate_species_match(response, requested_rank="class")

    assert key is None
