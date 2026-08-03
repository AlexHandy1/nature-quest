from services.logging_client import log_interest_submission


def test_log_interest_submission_logs_the_query(caplog):
    with caplog.at_level("INFO"):
        log_interest_submission(query="show me some birds near here")

    record = caplog.records[0]
    assert record.message == "interest_submission_received"
    assert record.query == "show me some birds near here"
