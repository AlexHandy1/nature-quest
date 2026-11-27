import os

import duckdb
import pytest

from jobs.wikipedia_build_embeddings_table import (
    compute_coverage_stats,
    read_specieskeys,
    run,
    verify_no_duplicate_specieskeys,
    write_table,
)


def test_compute_coverage_stats_reports_how_many_manifest_species_got_an_embedding():
    manifest_specieskeys = {"SP1", "SP2", "SP3", "SP4"}
    embedded_specieskeys = {"SP1", "SP2", "SP3"}

    result = compute_coverage_stats(manifest_specieskeys, embedded_specieskeys)

    assert result == {"manifest_species": 4, "embedded_species": 3, "coverage_pct": 75.0}


def test_compute_coverage_stats_handles_an_empty_manifest_without_dividing_by_zero():
    result = compute_coverage_stats(manifest_specieskeys=set(), embedded_specieskeys=set())

    assert result == {"manifest_species": 0, "embedded_species": 0, "coverage_pct": 0.0}


def _write_embeddings_parquet(path: str, rows: list[tuple]) -> None:
    con = duckdb.connect()
    values = ", ".join(f"('{specieskey}', {embedding})" for specieskey, embedding in rows)
    con.execute(f"""
        COPY (SELECT * FROM (VALUES {values}) AS t(specieskey, embedding))
        TO '{path}' (FORMAT PARQUET)
    """)


# --- Reading specieskeys ----------------------------------------------------


def test_read_specieskeys_returns_the_set_of_keys_in_a_parquet_file(tmp_path):
    path = str(tmp_path / "embeddings.parquet")
    _write_embeddings_parquet(path, [("SP1", [0.1, 0.2]), ("SP2", [0.3, 0.4])])
    con = duckdb.connect()

    result = read_specieskeys(con, path)

    assert result == {"SP1", "SP2"}


# --- Duplicate guardrail -----------------------------------------------------


def test_verify_no_duplicate_specieskeys_raises_when_a_key_appears_twice(tmp_path):
    path = str(tmp_path / "embeddings.parquet")
    _write_embeddings_parquet(path, [("SP1", [0.1, 0.2]), ("SP1", [0.3, 0.4])])
    con = duckdb.connect()

    with pytest.raises(ValueError, match="duplicate"):
        verify_no_duplicate_specieskeys(con, path)


def test_verify_no_duplicate_specieskeys_passes_silently_when_every_key_is_unique(tmp_path):
    path = str(tmp_path / "embeddings.parquet")
    _write_embeddings_parquet(path, [("SP1", [0.1, 0.2]), ("SP2", [0.3, 0.4])])
    con = duckdb.connect()

    verify_no_duplicate_specieskeys(con, path)


# --- Writing the final table -------------------------------------------------


def test_write_table_copies_specieskey_and_embedding_ordered_by_specieskey(tmp_path):
    embeddings_path = str(tmp_path / "embeddings.parquet")
    output_path = str(tmp_path / "wikipedia_species_embeddings.parquet")
    _write_embeddings_parquet(embeddings_path, [("SP2", [0.3, 0.4]), ("SP1", [0.1, 0.2])])
    con = duckdb.connect()

    write_table(con, embeddings_path, output_path)

    result = con.execute(f"SELECT specieskey FROM read_parquet('{output_path}')").fetchall()
    assert result == [("SP1",), ("SP2",)]


# --- Orchestration -----------------------------------------------------------


def _write_manifest(path: str, specieskeys: list[str]) -> None:
    con = duckdb.connect()
    values = ", ".join(f"('{key}')" for key in specieskeys)
    con.execute(f"""
        COPY (SELECT * FROM (VALUES {values}) AS t(specieskey)) TO '{path}' (FORMAT PARQUET)
    """)


def test_run_writes_the_final_table_and_returns_coverage_stats(tmp_path):
    manifest_path = str(tmp_path / "manifest.parquet")
    embeddings_path = str(tmp_path / "embeddings.parquet")
    output_path = str(tmp_path / "wikipedia_species_embeddings.parquet")
    _write_manifest(manifest_path, ["SP1", "SP2", "SP3"])
    _write_embeddings_parquet(embeddings_path, [("SP1", [0.1, 0.2]), ("SP2", [0.3, 0.4])])

    summary = run(manifest_path, embeddings_path, output_path)

    assert summary == {"manifest_species": 3, "embedded_species": 2, "coverage_pct": pytest.approx(66.666, abs=0.01)}
    con = duckdb.connect()
    result = con.execute(f"SELECT specieskey FROM read_parquet('{output_path}')").fetchall()
    assert result == [("SP1",), ("SP2",)]


def test_run_raises_before_writing_anything_when_embeddings_have_duplicate_specieskeys(tmp_path):
    manifest_path = str(tmp_path / "manifest.parquet")
    embeddings_path = str(tmp_path / "embeddings.parquet")
    output_path = str(tmp_path / "wikipedia_species_embeddings.parquet")
    _write_manifest(manifest_path, ["SP1"])
    _write_embeddings_parquet(embeddings_path, [("SP1", [0.1, 0.2]), ("SP1", [0.3, 0.4])])

    with pytest.raises(ValueError, match="duplicate"):
        run(manifest_path, embeddings_path, output_path)

    assert not os.path.exists(output_path)
