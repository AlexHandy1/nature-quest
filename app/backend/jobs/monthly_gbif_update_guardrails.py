from dataclasses import dataclass, field
from typing import Literal

EXPECTED_FACT_COLUMNS = frozenset({"cell", "specieskey", "occurrence_count"})
NEAR_ZERO_CHANGE_THRESHOLD_PCT = 0.1
LARGE_SWING_THRESHOLD_PCT = 20.0

EXPECTED_DIMENSION_COLUMNS = frozenset({
    "specieskey", "species", "kingdom", "phylum", "class", "order", "family", "genus", "common_name"
})


@dataclass(frozen=True)
class RunStats:
    fact_columns: frozenset[str]
    dimension_columns: frozenset[str]
    total_occurrence_count: int
    distinct_cell_count: int
    distinct_species_count: int


@dataclass(frozen=True)
class GuardrailOutcome:
    verdict: Literal["pass", "warn", "block"]
    block_reasons: list[str] = field(default_factory=list)
    warn_reasons: list[str] = field(default_factory=list)


def compute_stats_from_parquet(con, fact_path: str, dimension_path: str) -> RunStats:
    """Derives RunStats via DuckDB aggregate queries against fact_path/
    dimension_path (local or gs://) - used for both the freshly-extracted
    release files and the current production files (the guardrail
    baseline), so neither the ~221M-row fact table nor the dimension
    table ever passes through Python as row data."""
    fact_columns = frozenset(
        row[0] for row in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{fact_path}')").fetchall()
    )
    dimension_columns = frozenset(
        row[0]
        for row in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{dimension_path}')").fetchall()
    )
    total_occurrence_count, distinct_cell_count = con.execute(f"""
        SELECT SUM(occurrence_count), COUNT(DISTINCT cell) FROM read_parquet('{fact_path}')
    """).fetchone()
    (distinct_species_count,) = con.execute(f"""
        SELECT COUNT(DISTINCT specieskey) FROM read_parquet('{dimension_path}')
    """).fetchone()
    return RunStats(
        fact_columns=fact_columns,
        dimension_columns=dimension_columns,
        total_occurrence_count=total_occurrence_count,
        distinct_cell_count=distinct_cell_count,
        distinct_species_count=distinct_species_count,
    )


def evaluate(current: RunStats, previous: RunStats) -> GuardrailOutcome:
    block_reasons: list[str] = []

    missing_fact_columns = EXPECTED_FACT_COLUMNS - current.fact_columns
    if missing_fact_columns:
        block_reasons.append(f"fact table missing expected columns: {sorted(missing_fact_columns)}")

    missing_dimension_columns = EXPECTED_DIMENSION_COLUMNS - current.dimension_columns
    if missing_dimension_columns:
        block_reasons.append(
            f"dimension table missing expected columns: {sorted(missing_dimension_columns)}"
        )

    count_metrics = [
        ("total occurrence count", previous.total_occurrence_count, current.total_occurrence_count),
        ("distinct cell count", previous.distinct_cell_count, current.distinct_cell_count),
        ("distinct species count", previous.distinct_species_count, current.distinct_species_count),
    ]

    for label, previous_value, current_value in count_metrics:
        if current_value < previous_value:
            block_reasons.append(f"{label} decreased: {previous_value} -> {current_value}")

    warn_reasons: list[str] = []
    if not block_reasons:
        for label, previous_value, current_value in count_metrics:
            change = _percent_change(previous_value, current_value)
            if change < NEAR_ZERO_CHANGE_THRESHOLD_PCT:
                warn_reasons.append(f"{label} essentially unchanged: {previous_value} -> {current_value}")
            elif change > LARGE_SWING_THRESHOLD_PCT:
                warn_reasons.append(
                    f"{label} swung by more than {LARGE_SWING_THRESHOLD_PCT}%: {previous_value} -> {current_value}"
                )

    if block_reasons:
        verdict: Literal["pass", "warn", "block"] = "block"
    elif warn_reasons:
        verdict = "warn"
    else:
        verdict = "pass"
    return GuardrailOutcome(verdict=verdict, block_reasons=block_reasons, warn_reasons=warn_reasons)


def _percent_change(previous_value: int, current_value: int) -> float:
    if previous_value == 0:
        return 0.0
    return abs(current_value - previous_value) / previous_value * 100
