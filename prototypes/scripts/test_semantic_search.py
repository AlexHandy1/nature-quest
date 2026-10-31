"""Unit tests for the deterministic (non-network, non-model) logic behind the
semantic species search spike: taxon-group classification, prevalence
tiering, balanced selection, embedding-text construction, the DuckDB vector
store round-trip, and the experiment's scoring helpers.

Run: venv/bin/python -m pytest prototypes/scripts/test_semantic_search.py
"""

import math

import pytest

from semantic_search_corpus import (
    assign_tier,
    classify_group,
    derive_common_name,
    select_balanced,
)
from semantic_search_index import build_embedding_text, build_store, search_store
from semantic_search_experiment import hit_at_k, precision_at_k


def make_record(**overrides):
    record = {
        "specieskey": "1",
        "scientific_name": "Passer domesticus",
        "common_name": "House sparrow",
        "group": "bird",
        "tier": "common",
        "occurrence_count": 1_000_000,
        "extract": "A small bird.",
        "image_url": None,
        "wiki_url": None,
        "tags": [],
    }
    record.update(overrides)
    return record


# ── classify_group ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "taxonomy, expected",
    [
        ({"kingdom": "Animalia", "class": "Aves"}, "bird"),
        ({"kingdom": "Animalia", "class": "Mammalia"}, "mammal"),
        ({"kingdom": "Animalia", "class": "Actinopterygii"}, "fish"),
        ({"kingdom": "Animalia", "class": "Elasmobranchii"}, "fish"),
        ({"kingdom": "Animalia", "class": "Insecta", "order": "Lepidoptera", "family": "Nymphalidae"}, "butterfly"),
        ({"kingdom": "Animalia", "class": "Insecta", "order": "Lepidoptera", "family": "Sphingidae"}, "moth"),
        ({"kingdom": "Animalia", "class": "Insecta", "order": "Hymenoptera", "family": "Apidae"}, "bee"),
        ({"kingdom": "Animalia", "class": "Insecta", "order": "Hymenoptera", "family": "Formicidae"}, "ant_wasp"),
        ({"kingdom": "Animalia", "class": "Insecta", "order": "Coleoptera"}, "beetle"),
        ({"kingdom": "Animalia", "class": "Insecta", "order": "Odonata"}, "dragonfly"),
        ({"kingdom": "Animalia", "class": "Insecta", "order": "Orthoptera"}, "other_insect"),
        ({"kingdom": "Animalia", "class": "Arachnida"}, "arachnid"),
        ({"kingdom": "Animalia", "class": "Squamata"}, "reptile"),
        ({"kingdom": "Animalia", "class": "Testudines"}, "reptile"),
        ({"kingdom": "Animalia", "class": "Amphibia"}, "amphibian"),
        ({"kingdom": "Animalia", "phylum": "Mollusca", "class": "Gastropoda"}, "mollusc"),
        ({"kingdom": "Animalia", "phylum": "Arthropoda", "class": "Malacostraca"}, "crustacean"),
        ({"kingdom": "Plantae", "class": "Pinopsida"}, "plant"),
        ({"kingdom": "Fungi", "class": "Agaricomycetes"}, "fungus"),
        ({"kingdom": "Animalia", "phylum": "Cnidaria", "class": "Anthozoa"}, "other_animal"),
    ],
)
def test_classify_group(taxonomy, expected):
    assert classify_group(taxonomy) == expected


# ── assign_tier ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "count, expected",
    [(5_000_000, "common"), (1_000_000, "common"), (999_999, "mid"),
     (10_000, "mid"), (9_999, "rare"), (0, "rare")],
)
def test_assign_tier(count, expected):
    assert assign_tier(count) == expected


# ── derive_common_name ──────────────────────────────────────────────────

def test_common_name_prefers_wikipedia_title_when_it_differs_from_scientific_name():
    name, source = derive_common_name("Passer domesticus", "House sparrow", ["sparrow"])
    assert (name, source) == ("House sparrow", "wikipedia_title")


def test_common_name_falls_back_to_gbif_vernacular_when_title_is_scientific():
    name, source = derive_common_name("Bombus terrestris", "Bombus terrestris", ["Buff-tailed bumblebee"])
    assert (name, source) == ("Buff-tailed bumblebee", "gbif_vernacular")


def test_common_name_title_comparison_ignores_case():
    name, source = derive_common_name("Bombus terrestris", "Bombus Terrestris", ["Buff-tailed bumblebee"])
    assert source == "gbif_vernacular"


def test_common_name_is_none_when_no_source_has_one():
    assert derive_common_name("Foo bar", "Foo bar", []) == (None, None)


# ── select_balanced ─────────────────────────────────────────────────────

def test_select_balanced_returns_requested_count():
    records = [make_record(specieskey=str(i), group=f"g{i % 4}") for i in range(20)]
    assert len(select_balanced(records, 10)) == 10


def test_select_balanced_round_robins_across_groups():
    records = (
        [make_record(specieskey=f"b{i}", group="bird") for i in range(10)]
        + [make_record(specieskey=f"f{i}", group="fish") for i in range(2)]
    )
    selected = select_balanced(records, 4)
    groups = [r["group"] for r in selected]
    assert groups.count("fish") == 2
    assert groups.count("bird") == 2


def test_select_balanced_mixes_tiers_within_a_group():
    records = (
        [make_record(specieskey=f"c{i}", group="bird", tier="common") for i in range(3)]
        + [make_record(specieskey="r0", group="bird", tier="rare")]
    )
    selected = select_balanced(records, 2)
    assert {r["tier"] for r in selected} == {"common", "rare"}


def test_select_balanced_returns_everything_when_fewer_than_requested():
    records = [make_record(specieskey=str(i)) for i in range(3)]
    assert len(select_balanced(records, 10)) == 3


# ── build_embedding_text ────────────────────────────────────────────────

def test_embedding_text_includes_common_name_scientific_name_and_extract():
    text = build_embedding_text(make_record())
    assert text == "House sparrow (Passer domesticus): A small bird."


# ── DuckDB store round-trip ─────────────────────────────────────────────

def unit(vec):
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec]


def test_search_store_ranks_by_cosine_similarity(tmp_path):
    records = [
        make_record(specieskey="a", common_name="A"),
        make_record(specieskey="b", common_name="B"),
        make_record(specieskey="c", common_name="C"),
    ]
    vectors = [unit([1, 0, 0]), unit([0.8, 0.6, 0]), unit([0, 0, 1])]
    con = build_store(str(tmp_path / "t.duckdb"), records, vectors, dim=3)

    results = search_store(con, unit([1, 0.1, 0]), k=2, dim=3)

    assert [r["specieskey"] for r in results] == ["a", "b"]
    assert results[0]["score"] > results[1]["score"]


def test_search_store_returns_display_fields(tmp_path):
    con = build_store(str(tmp_path / "t.duckdb"), [make_record()], [unit([1, 0, 0])], dim=3)
    result = search_store(con, unit([1, 0, 0]), k=5, dim=3)[0]
    assert result["common_name"] == "House sparrow"
    assert result["group"] == "bird"
    assert result["score"] == pytest.approx(1.0, abs=1e-5)


# ── scoring ─────────────────────────────────────────────────────────────

def test_hit_at_k_true_when_any_result_relevant():
    results = [make_record(group="mammal"), make_record(group="bird")]
    assert hit_at_k(results, lambda r: r["group"] == "bird") is True


def test_hit_at_k_false_when_none_relevant():
    results = [make_record(group="mammal")]
    assert hit_at_k(results, lambda r: r["group"] == "bird") is False


def test_precision_at_k_is_fraction_of_relevant_results():
    results = [make_record(group="bird")] * 3 + [make_record(group="fish")] * 2
    assert precision_at_k(results, lambda r: r["group"] == "bird") == pytest.approx(0.6)
