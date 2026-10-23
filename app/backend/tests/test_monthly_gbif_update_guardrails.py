from jobs.monthly_gbif_update_guardrails import (
    EXPECTED_DIMENSION_COLUMNS,
    EXPECTED_FACT_COLUMNS,
    RunStats,
    evaluate,
)


def _healthy_stats(**overrides) -> RunStats:
    defaults = {
        "fact_columns": EXPECTED_FACT_COLUMNS,
        "dimension_columns": EXPECTED_DIMENSION_COLUMNS,
        "total_occurrence_count": 1_000_000,
        "distinct_cell_count": 10_000,
        "distinct_species_count": 5_000,
    }
    defaults.update(overrides)
    return RunStats(**defaults)


def test_passes_when_schema_matches_and_no_counts_decreased():
    previous = _healthy_stats()
    current = _healthy_stats(
        total_occurrence_count=1_050_000,
        distinct_cell_count=10_500,
        distinct_species_count=5_250,
    )

    outcome = evaluate(current, previous)

    assert outcome.verdict == "pass"
    assert outcome.block_reasons == []
    assert outcome.warn_reasons == []


def test_blocks_when_fact_table_is_missing_a_required_column():
    previous = _healthy_stats()
    current = _healthy_stats(fact_columns=frozenset({"cell", "specieskey"}))

    outcome = evaluate(current, previous)

    assert outcome.verdict == "block"
    assert any("occurrence_count" in reason for reason in outcome.block_reasons)


def test_blocks_when_dimension_table_is_missing_a_required_column():
    previous = _healthy_stats()
    current = _healthy_stats(dimension_columns=frozenset({"specieskey", "species"}))

    outcome = evaluate(current, previous)

    assert outcome.verdict == "block"
    assert any("common_name" in reason for reason in outcome.block_reasons)


def test_blocks_when_total_occurrence_count_decreases():
    previous = _healthy_stats(total_occurrence_count=1_000_000)
    current = _healthy_stats(total_occurrence_count=999_999)

    outcome = evaluate(current, previous)

    assert outcome.verdict == "block"
    assert any("occurrence count" in reason for reason in outcome.block_reasons)


def test_blocks_when_distinct_cell_count_decreases():
    previous = _healthy_stats(distinct_cell_count=10_000)
    current = _healthy_stats(distinct_cell_count=9_999)

    outcome = evaluate(current, previous)

    assert outcome.verdict == "block"
    assert any("cell count" in reason for reason in outcome.block_reasons)


def test_blocks_when_distinct_species_count_decreases():
    previous = _healthy_stats(distinct_species_count=5_000)
    current = _healthy_stats(distinct_species_count=4_999)

    outcome = evaluate(current, previous)

    assert outcome.verdict == "block"
    assert any("species count" in reason for reason in outcome.block_reasons)


def test_warns_when_total_occurrence_count_is_essentially_unchanged():
    previous = _healthy_stats(total_occurrence_count=1_000_000)
    current = _healthy_stats(total_occurrence_count=1_000_000)

    outcome = evaluate(current, previous)

    assert outcome.verdict == "warn"
    assert outcome.block_reasons == []
    assert any("occurrence count" in reason for reason in outcome.warn_reasons)


def test_collects_every_block_reason_when_multiple_checks_fail_at_once():
    previous = _healthy_stats()
    current = _healthy_stats(
        fact_columns=frozenset({"cell", "specieskey"}),
        total_occurrence_count=previous.total_occurrence_count - 1,
        distinct_cell_count=previous.distinct_cell_count - 1,
    )

    outcome = evaluate(current, previous)

    assert outcome.verdict == "block"
    assert len(outcome.block_reasons) == 3
    assert any("occurrence_count" in reason for reason in outcome.block_reasons)
    assert any("occurrence count" in reason for reason in outcome.block_reasons)
    assert any("cell count" in reason for reason in outcome.block_reasons)


def test_warns_when_distinct_cell_count_is_essentially_unchanged():
    previous = _healthy_stats(distinct_cell_count=10_000)
    current = _healthy_stats(distinct_cell_count=10_000)

    outcome = evaluate(current, previous)

    assert outcome.verdict == "warn"
    assert any("cell count" in reason for reason in outcome.warn_reasons)


def test_warns_when_distinct_species_count_is_essentially_unchanged():
    previous = _healthy_stats(distinct_species_count=5_000)
    current = _healthy_stats(distinct_species_count=5_000)

    outcome = evaluate(current, previous)

    assert outcome.verdict == "warn"
    assert any("species count" in reason for reason in outcome.warn_reasons)


def test_warns_when_total_occurrence_count_swings_by_more_than_twenty_percent():
    previous = _healthy_stats(total_occurrence_count=1_000_000)
    current = _healthy_stats(total_occurrence_count=1_300_000)

    outcome = evaluate(current, previous)

    assert outcome.verdict == "warn"
    assert outcome.block_reasons == []
    assert any("occurrence count" in reason for reason in outcome.warn_reasons)
