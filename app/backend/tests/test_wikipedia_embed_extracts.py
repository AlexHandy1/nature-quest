import duckdb
import pytest

from jobs.wikipedia_embed_extracts import (
    build_embedding_text,
    embed_records,
    read_fetch_results,
    rows_to_embed,
    run,
    write_embeddings_parquet,
)


def test_build_embedding_text_combines_common_name_scientific_name_and_extract():
    record = {"specieskey": "SP1", "species": "Turdus merula", "common_name": "Common Blackbird",
              "extract": "A common garden bird."}

    result = build_embedding_text(record)

    assert result == "Common Blackbird (Turdus merula): A common garden bird."


def test_build_embedding_text_falls_back_to_scientific_name_when_no_common_name():
    record = {"specieskey": "SP2", "species": "Parus caeruleus", "common_name": None,
              "extract": "A small, colourful passerine."}

    result = build_embedding_text(record)

    assert result == "Parus caeruleus: A small, colourful passerine."


# --- Filtering fetch results down to embeddable species --------------------


def test_rows_to_embed_keeps_only_species_with_a_successful_extract():
    fetch_results = [
        {"specieskey": "SP1", "status": "ok", "extract": "A common garden bird."},
        {"specieskey": "SP2", "status": "missing", "extract": None},
        {"specieskey": "SP3", "status": "empty_extract", "extract": None},
    ]

    result = rows_to_embed(fetch_results)

    assert result == [{"specieskey": "SP1", "status": "ok", "extract": "A common garden bird."}]


# --- Reading the fetch step's JSONL output ----------------------------------


def test_read_fetch_results_reads_back_every_row_written_by_the_fetch_step(tmp_path):
    path = str(tmp_path / "extracts.jsonl")
    with open(path, "w") as f:
        f.write('{"specieskey": "SP1", "status": "ok", "extract": "A common garden bird."}\n')
        f.write('{"specieskey": "SP2", "status": "missing", "extract": null}\n')

    result = read_fetch_results(path)

    assert result == [
        {"specieskey": "SP1", "status": "ok", "extract": "A common garden bird."},
        {"specieskey": "SP2", "status": "missing", "extract": None},
    ]


# --- Embedding ---------------------------------------------------------------


def test_embed_records_attaches_an_embedding_vector_to_each_record():
    records = [
        {"specieskey": "SP1", "species": "Turdus merula", "common_name": "Common Blackbird",
         "extract": "A common garden bird."},
        {"specieskey": "SP2", "species": "Parus caeruleus", "common_name": None,
         "extract": "A small, colourful passerine."},
    ]

    def fake_embed_fn(texts):
        return [[float(len(t)), 0.0] for t in texts]

    result = embed_records(records, embed_fn=fake_embed_fn)

    assert result == [
        {"specieskey": "SP1", "embedding": [float(len("Common Blackbird (Turdus merula): A common garden bird.")), 0.0]},
        {"specieskey": "SP2", "embedding": [float(len("Parus caeruleus: A small, colourful passerine.")), 0.0]},
    ]


# --- Writing the embeddings parquet -----------------------------------------


def test_write_embeddings_parquet_writes_rows_that_can_be_read_back(tmp_path):
    path = str(tmp_path / "embeddings.parquet")
    rows = [
        {"specieskey": "SP1", "embedding": [0.1, 0.2, 0.3]},
        {"specieskey": "SP2", "embedding": [0.4, 0.5, 0.6]},
    ]

    write_embeddings_parquet(rows, path, dim=3)

    con = duckdb.connect()
    result = con.execute(f"SELECT specieskey, embedding FROM read_parquet('{path}') ORDER BY specieskey").fetchall()
    keys = [row[0] for row in result]
    vectors = [row[1] for row in result]
    assert keys == ["SP1", "SP2"]
    # FLOAT (32-bit) storage - values round-trip approximately, not exactly
    assert vectors[0] == pytest.approx([0.1, 0.2, 0.3])
    assert vectors[1] == pytest.approx([0.4, 0.5, 0.6])


# --- Orchestration -----------------------------------------------------------


def test_run_reads_fetch_output_embeds_ok_rows_and_writes_the_embeddings_parquet(tmp_path):
    fetch_output_path = str(tmp_path / "extracts.jsonl")
    embeddings_output_path = str(tmp_path / "embeddings.parquet")
    with open(fetch_output_path, "w") as f:
        f.write('{"specieskey": "SP1", "species": "Turdus merula", "common_name": "Common Blackbird", '
                '"status": "ok", "extract": "A common garden bird."}\n')
        f.write('{"specieskey": "SP2", "species": "Nonexistent Species", "common_name": null, '
                '"status": "missing", "extract": null}\n')

    def fake_embed_fn(texts):
        return [[1.0, 2.0] for _ in texts]

    summary = run(fetch_output_path, embeddings_output_path, embed_fn=fake_embed_fn, dim=2)

    assert summary == {"total_fetched": 2, "embedded": 1, "skipped": 1}
    con = duckdb.connect()
    result = con.execute(f"SELECT specieskey FROM read_parquet('{embeddings_output_path}')").fetchall()
    assert result == [("SP1",)]
