from jobs.monthly_gbif_update_logging import log_monthly_gbif_update_outcome


def test_log_monthly_gbif_update_outcome_logs_a_pass_with_no_reasons(caplog):
    with caplog.at_level("INFO"):
        log_monthly_gbif_update_outcome(verdict="pass")

    record = caplog.records[0]
    assert record.message == "monthly_gbif_update_outcome"
    assert record.verdict == "pass"
    assert record.block_reasons == []
    assert record.warn_reasons == []


def test_log_monthly_gbif_update_outcome_logs_block_reasons(caplog):
    with caplog.at_level("INFO"):
        log_monthly_gbif_update_outcome(verdict="block", block_reasons=["total occurrence count decreased"])

    record = caplog.records[0]
    assert record.verdict == "block"
    assert record.block_reasons == ["total occurrence count decreased"]


def test_log_monthly_gbif_update_outcome_logs_warn_reasons(caplog):
    with caplog.at_level("INFO"):
        log_monthly_gbif_update_outcome(verdict="warn", warn_reasons=["occurrence count essentially unchanged"])

    record = caplog.records[0]
    assert record.verdict == "warn"
    assert record.warn_reasons == ["occurrence count essentially unchanged"]
