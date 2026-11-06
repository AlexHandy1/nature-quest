"""
PROTOTYPE EVAL — test_routing_cascade_eval.py

Question this answers: does the LLM-first / semantic-fallback routing
cascade (routing_cascade.py) preserve all of production's existing
taxon-resolution behaviour unmodified, while correctly handing off to
semantic search for the class of query the LLM resolver can't answer?

Written in the exact shape of the production eval tier
(app/backend/tests/evals/test_taxon_resolution_eval.py and
test_full_pipeline_eval.py) specifically so it can be lifted into
app/backend/tests/evals/ with minimal changes once this design is
confirmed — see docs/status_docs/ for the session that designed this
cascade. Real network calls (OpenRouter LLM + live GBIF verification), no
mocking, same as the production eval tier.

Relationship to the two existing production files:
  - test_taxon_resolution_eval.py tests resolve_taxon_filters() in
    isolation. This prototype does not modify or call that function
    differently, so every one of those ~25 cases is expected to keep
    passing completely unchanged against production code — nothing to add
    there.
  - test_full_pipeline_eval.py is the one actually extended here: its
    chain (query -> taxon_filters -> candidate_cells -> species lookup) is
    now one branch of a larger cascade. The "regression" tests below re-run
    a representative subset of its own scenarios through the FULL cascade
    (resolve_species_for_query, not just the production pipeline function)
    to confirm the LLM-resolved branch is byte-for-byte the same behaviour,
    then new tests cover the semantic-fallback branch this session added.

Run (from repo root, real API calls, costs money):
  venv/bin/pytest prototypes/scripts/test_routing_cascade_eval.py -m eval -v \
    | tee prototypes/logs/test_routing_cascade_eval_$(date +%Y%m%d_%H%M%S).log
"""

import httpx
import pytest

from routing_cascade import EMBED_DB_PATH, build_llm_client, resolve_species_for_query, species_in_cells
from semantic_search_index_fastembed import embed_query, load_model
from services.species_index import candidate_cells

import duckdb

RETIRO_POLYGON = (
    "POLYGON((-3.68876 40.4199,-3.689 40.40777,-3.67912 40.4076,"
    "-3.676 40.41148,-3.68002 40.42163,-3.68876 40.4199))"
)

# Same independent-oracle rationale as test_full_pipeline_eval.py: verifies
# against live GBIF taxonomy, not production's own local table, so a bug in
# the local join can't hide itself from this eval.
GBIF_SPECIES_MATCH_URL = "https://api.gbif.org/v1/species/match"


@pytest.fixture(scope="module")
def llm_client():
    return build_llm_client()


@pytest.fixture(scope="module")
def embed_model():
    return load_model()


@pytest.fixture(scope="module")
def fact_con():
    return duckdb.connect()


@pytest.fixture(scope="module")
def embed_con():
    return duckdb.connect(str(EMBED_DB_PATH), read_only=True)


def _run(query: str, *, llm_client, embed_model, fact_con, embed_con) -> dict:
    result = resolve_species_for_query(
        query,
        RETIRO_POLYGON,
        llm_client=llm_client,
        embed_model=embed_model,
        embed_query_fn=embed_query,
        fact_con=fact_con.cursor(),
        embed_con=embed_con.cursor(),
    )
    print(f"[eval] resolve_species_for_query input={query!r} -> {result!r}")
    return result


def _species_belongs_to_class(scientific_name: str, expected_class: str) -> bool:
    response = httpx.get(
        GBIF_SPECIES_MATCH_URL, params={"name": scientific_name, "rank": "SPECIES"}, timeout=5.0
    )
    response.raise_for_status()
    return response.json().get("class") == expected_class


def _species_belongs_to_kingdom(scientific_name: str, expected_kingdom: str) -> bool:
    response = httpx.get(
        GBIF_SPECIES_MATCH_URL, params={"name": scientific_name, "rank": "SPECIES"}, timeout=5.0
    )
    response.raise_for_status()
    return response.json().get("kingdom") == expected_kingdom


def _species_belongs_to_one_of_classes(scientific_name: str, expected_classes: set[str]) -> bool:
    response = httpx.get(
        GBIF_SPECIES_MATCH_URL, params={"name": scientific_name, "rank": "SPECIES"}, timeout=5.0
    )
    response.raise_for_status()
    return response.json().get("class") in expected_classes


# ── Regression: LLM-resolved branch must be unchanged from today's production behaviour ──


@pytest.mark.eval
def test_birds_query_still_resolves_via_llm_branch_with_genuine_aves_species(
    llm_client, embed_model, fact_con, embed_con
):
    result = _run("I want to see birds", llm_client=llm_client, embed_model=embed_model,
                   fact_con=fact_con, embed_con=embed_con)

    assert result["branch"] == "llm_resolved"
    assert result["taxon_filters"] == [{"taxonRank": "class", "taxonValue": "Aves"}]
    assert result["species"]
    for species in result["species"]:
        assert _species_belongs_to_class(species["species"], "Aves")


@pytest.mark.eval
def test_birds_and_plants_query_still_returns_a_genuine_mix_via_llm_branch(
    llm_client, embed_model, fact_con, embed_con
):
    result = _run("I want to see birds and plants", llm_client=llm_client, embed_model=embed_model,
                   fact_con=fact_con, embed_con=embed_con)

    assert result["branch"] == "llm_resolved"
    assert result["species"]
    for species in result["species"]:
        is_bird = _species_belongs_to_class(species["species"], "Aves")
        is_plant = _species_belongs_to_kingdom(species["species"], "Plantae")
        assert is_bird or is_plant


@pytest.mark.eval
def test_turtles_query_still_resolves_via_llm_branch_unchanged(
    llm_client, embed_model, fact_con, embed_con
):
    result = _run("I want to see Turtles", llm_client=llm_client, embed_model=embed_model,
                   fact_con=fact_con, embed_con=embed_con)

    assert result["branch"] == "llm_resolved"
    assert result["taxon_filters"] == [{"taxonRank": "order", "taxonValue": "Testudines"}]
    assert result["species"]


# ── New: semantic-fallback branch ──


@pytest.mark.eval
def test_descriptive_query_the_llm_cannot_resolve_triggers_semantic_fallback(
    llm_client, embed_model, fact_con, embed_con
):
    result = _run("something that flies", llm_client=llm_client, embed_model=embed_model,
                   fact_con=fact_con, embed_con=embed_con)

    assert result["branch"] == "semantic_fallback"
    assert result["taxon_filters"] == []


@pytest.mark.eval
def test_pollinators_thematic_query_triggers_semantic_fallback(
    llm_client, embed_model, fact_con, embed_con
):
    result = _run("I want to see pollinators", llm_client=llm_client, embed_model=embed_model,
                   fact_con=fact_con, embed_con=embed_con)

    assert result["branch"] == "semantic_fallback"


@pytest.mark.eval
def test_semantic_fallback_never_returns_a_species_absent_from_the_polygon(
    llm_client, embed_model, fact_con, embed_con
):
    """The core grounding guarantee from PLANNING_EMBEDDINGS_FEATURES_291026.md
    Track A: semantic candidates must be restricted to species actually
    present in the drawn polygon, never a global top-k filtered down
    afterward. Verified directly against species_in_cells(), independent of
    whatever semantic_rank_in_cells() itself computed."""
    result = _run("something that flies", llm_client=llm_client, embed_model=embed_model,
                   fact_con=fact_con, embed_con=embed_con)
    assert result["branch"] == "semantic_fallback"
    assert result["species"], "expected at least one semantic match in Retiro for this query"

    cells = candidate_cells(RETIRO_POLYGON)
    present = set(species_in_cells(fact_con.cursor(), cells))
    for species in result["species"]:
        assert species["specieskey"] in present, (
            f"{species['common_name']} ({species['specieskey']}) was returned by semantic "
            f"search but is not recorded as present in the polygon's candidate cells"
        )


@pytest.mark.eval
def test_semantic_fallback_results_are_ranked_by_descending_similarity(
    llm_client, embed_model, fact_con, embed_con
):
    result = _run("creatures that come out at night", llm_client=llm_client, embed_model=embed_model,
                   fact_con=fact_con, embed_con=embed_con)
    assert result["branch"] == "semantic_fallback"
    scores = [s["score"] for s in result["species"]]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.eval
def test_taxon_resolved_query_with_no_local_occurrences_does_not_fall_back_to_semantic(
    llm_client, embed_model, fact_con, embed_con
):
    """Penguins is a clean taxon match (order Sphenisciformes) but has zero
    recorded occurrences in Retiro Park. resolve_species_for_query()'s
    if/else branches on taxon_filters emptiness alone — once it enters the
    non-empty branch it returns immediately, whatever query_species_for_filter
    comes back with. So a resolved-but-locally-absent taxon must stay on the
    llm_resolved branch with an empty species list (today's "no_results"
    case), and must NOT trigger semantic search just because the taxon match
    turned out to be locally absent — confirmed manually against the running
    prototype server before writing this assertion."""
    result = _run("I want to see penguins", llm_client=llm_client, embed_model=embed_model,
                   fact_con=fact_con, embed_con=embed_con)

    # Not pinning the exact taxon_filters value: unlike Testudines/fish/
    # reptiles, penguins has no explicit TAXON_GUIDANCE rule, and repeated
    # calls with identical input have been observed to return either
    # order:Sphenisciformes or family:Spheniscidae. The behaviour under
    # test is the branch/empty-result outcome, not which valid taxonomic
    # rank the LLM happens to pick on a given call.
    assert result["branch"] == "llm_resolved"
    assert result["taxon_filters"]
    assert result["species"] == []


# NOTE: a test for the mixed taxon+thematic compound case ("birds and
# pollinators") was deliberately removed here. Repeated identical calls to
# resolve_taxon_filters() with that exact input were observed to return at
# least three different outputs ([{Aves},{Insecta}], [{Aves}] alone, and []
# entirely) - not one stable "known limitation" shape to pin a regression
# test against. This routing-instability finding is flagged separately
# (session write-up / planning doc), not encoded as an assertion here.


# ── New: semantic-fallback results are verified against live GBIF taxonomy,
# same independent-oracle pattern as the llm_resolved regression tests above,
# not just checked for branch/grounding/ranking correctness ──


FLIGHT_CAPABLE_CLASSES = {"Aves", "Insecta"}


@pytest.mark.eval
def test_flies_query_returns_only_flight_capable_classes(
    llm_client, embed_model, fact_con, embed_con
):
    result = _run("something that flies", llm_client=llm_client, embed_model=embed_model,
                   fact_con=fact_con, embed_con=embed_con)
    assert result["branch"] == "semantic_fallback"
    assert result["species"]
    for species in result["species"]:
        assert _species_belongs_to_one_of_classes(species["scientific_name"], FLIGHT_CAPABLE_CLASSES), (
            f"{species['scientific_name']} is not Aves or Insecta"
        )


@pytest.mark.eval
def test_creatures_with_wings_query_returns_only_flight_capable_classes(
    llm_client, embed_model, fact_con, embed_con
):
    """Deliberately avoids the literal word "bird" (unlike an earlier draft
    of this test suite that used "birds of prey" / "colourful birds") -
    those resolve via the LLM taxon path since they contain a literal taxon
    noun, never reaching semantic search at all. Confirmed to decline
    (taxon_filters == []) before writing this assertion."""
    result = _run("creatures with wings", llm_client=llm_client, embed_model=embed_model,
                   fact_con=fact_con, embed_con=embed_con)
    assert result["branch"] == "semantic_fallback"
    assert result["species"]
    for species in result["species"]:
        assert _species_belongs_to_one_of_classes(species["scientific_name"], FLIGHT_CAPABLE_CLASSES), (
            f"{species['scientific_name']} is not Aves or Insecta"
        )
