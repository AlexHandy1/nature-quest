from services.logging_client import log_interest_submission, log_query_outcome


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
            input_tokens=120,
            output_tokens=15,
            gbif_result_count=5,
        )

    record = caplog.records[0]
    assert record.message == "query_outcome"
    assert record.query == "show me some birds"
    assert record.outcome == "resolved"
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
