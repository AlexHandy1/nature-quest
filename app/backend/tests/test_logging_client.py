from services.logging_client import (
    log_interest_submission,
    log_llm_taxon_filters_resolved,
    log_query_outcome,
    log_query_submitted,
    log_species_selected,
    log_waypoints_ordered,
)


def test_log_interest_submission_logs_the_query(caplog):
    with caplog.at_level("INFO"):
        log_interest_submission(query="show me some birds near here")

    record = caplog.records[0]
    assert record.message == "interest_submission_received"
    assert record.query == "show me some birds near here"


def test_log_query_outcome_logs_the_resolved_outcome(caplog):
    with caplog.at_level("INFO"):
        log_query_outcome(
            query="show me some birds",
            outcome="resolved",
            distinct_id="anon-123",
            input_tokens=120,
            output_tokens=15,
            gbif_result_count=5,
        )

    record = caplog.records[0]
    assert record.message == "query_outcome"
    assert record.query == "show me some birds"
    assert record.outcome == "resolved"
    assert record.distinct_id == "anon-123"
    assert record.guardrail is None
    assert record.input_tokens == 120
    assert record.output_tokens == 15
    assert record.gbif_result_count == 5


def test_log_query_outcome_logs_which_guardrail_fired(caplog):
    with caplog.at_level("INFO"):
        log_query_outcome(query=None, outcome="daily_limit_reached", guardrail="daily_limit")

    record = caplog.records[0]
    assert record.outcome == "daily_limit_reached"
    assert record.guardrail == "daily_limit"
    assert record.input_tokens is None
    assert record.output_tokens is None
    assert record.gbif_result_count is None


def test_log_query_submitted_logs_the_query_and_distinct_id(caplog):
    with caplog.at_level("INFO"):
        log_query_submitted(query="show me some birds", distinct_id="anon-123")

    record = caplog.records[0]
    assert record.message == "query_submitted"
    assert record.query == "show me some birds"
    assert record.distinct_id == "anon-123"


def test_log_llm_taxon_filters_resolved_logs_the_filters(caplog):
    taxon_filters = [{"taxonRank": "class", "taxonValue": "Aves"}]
    with caplog.at_level("INFO"):
        log_llm_taxon_filters_resolved(
            query="birds", distinct_id="anon-123", taxon_filters=taxon_filters
        )

    record = caplog.records[0]
    assert record.message == "llm_taxon_filters_resolved"
    assert record.distinct_id == "anon-123"
    assert record.taxon_filters == taxon_filters


def test_log_species_selected_logs_the_species_list(caplog):
    species = [{"species": "Turdus merula", "count": 5, "hotspot_lat": 40.41, "hotspot_lon": -3.68}]
    with caplog.at_level("INFO"):
        log_species_selected(query="birds", distinct_id="anon-123", species=species)

    record = caplog.records[0]
    assert record.message == "species_selected"
    assert record.distinct_id == "anon-123"
    assert record.species == species


def test_log_waypoints_ordered_logs_the_ordered_waypoints(caplog):
    waypoints = [{"species": "Turdus merula", "hotspot_lat": 40.41, "hotspot_lon": -3.68, "distance_m": 120.0}]
    with caplog.at_level("INFO"):
        log_waypoints_ordered(query="birds", distinct_id="anon-123", waypoints=waypoints)

    record = caplog.records[0]
    assert record.message == "waypoints_ordered"
    assert record.distinct_id == "anon-123"
    assert record.waypoints == waypoints
