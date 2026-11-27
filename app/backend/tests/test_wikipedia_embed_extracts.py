import duckdb
import pytest

from jobs.wikipedia_embed_extracts import (
    build_embedding_text,
    check_output_consistency,
    embed_records,
    merge_chunks_to_final,
    read_embedded_specieskeys,
    read_fetch_results,
    records_to_embed,
    rows_to_embed,
    run,
    write_chunk_parquet,
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


# --- Chunk checkpointing ------------------------------------------------------


def test_write_chunk_parquet_writes_a_new_file_into_the_chunk_dir(tmp_path):
    chunk_dir = str(tmp_path / "chunks")
    rows = [{"specieskey": "SP1", "embedding": [0.1, 0.2]}]

    write_chunk_parquet(rows, chunk_dir, dim=2)

    con = duckdb.connect()
    result = con.execute(f"SELECT specieskey FROM read_parquet('{chunk_dir}/*.parquet')").fetchall()
    assert result == [("SP1",)]


def test_write_chunk_parquet_does_not_collide_with_an_existing_chunk_file(tmp_path):
    chunk_dir = str(tmp_path / "chunks")

    write_chunk_parquet([{"specieskey": "SP1", "embedding": [0.1, 0.2]}], chunk_dir, dim=2)
    write_chunk_parquet([{"specieskey": "SP2", "embedding": [0.3, 0.4]}], chunk_dir, dim=2)

    con = duckdb.connect()
    result = con.execute(f"SELECT specieskey FROM read_parquet('{chunk_dir}/*.parquet') ORDER BY specieskey").fetchall()
    assert result == [("SP1",), ("SP2",)]


def test_read_embedded_specieskeys_reads_keys_across_all_chunk_files(tmp_path):
    chunk_dir = str(tmp_path / "chunks")
    write_chunk_parquet([{"specieskey": "SP1", "embedding": [0.1, 0.2]}], chunk_dir, dim=2)
    write_chunk_parquet([{"specieskey": "SP2", "embedding": [0.3, 0.4]}], chunk_dir, dim=2)

    result = read_embedded_specieskeys(chunk_dir)

    assert result == {"SP1", "SP2"}


def test_read_embedded_specieskeys_returns_empty_set_when_chunk_dir_does_not_exist(tmp_path):
    chunk_dir = str(tmp_path / "does_not_exist")

    result = read_embedded_specieskeys(chunk_dir)

    assert result == set()


def test_records_to_embed_excludes_already_embedded_specieskeys():
    embeddable = [
        {"specieskey": "SP1", "status": "ok", "extract": "A."},
        {"specieskey": "SP2", "status": "ok", "extract": "B."},
    ]

    result = records_to_embed(embeddable, already_embedded={"SP1"})

    assert result == [{"specieskey": "SP2", "status": "ok", "extract": "B."}]


def test_merge_chunks_to_final_combines_every_chunk_file_into_one_output(tmp_path):
    chunk_dir = str(tmp_path / "chunks")
    final_path = str(tmp_path / "embeddings.parquet")
    write_chunk_parquet([{"specieskey": "SP1", "embedding": [0.1, 0.2]}], chunk_dir, dim=2)
    write_chunk_parquet([{"specieskey": "SP2", "embedding": [0.3, 0.4]}], chunk_dir, dim=2)

    merge_chunks_to_final(chunk_dir, final_path, dim=2)

    con = duckdb.connect()
    result = con.execute(f"SELECT specieskey FROM read_parquet('{final_path}') ORDER BY specieskey").fetchall()
    assert result == [("SP1",), ("SP2",)]


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


# --- Consistency check: actual output file vs. embeddable target -----------


def test_run_resumes_by_skipping_specieskeys_already_checkpointed_in_chunk_dir(tmp_path):
    fetch_output_path = str(tmp_path / "extracts.jsonl")
    embeddings_output_path = str(tmp_path / "embeddings.parquet")
    chunk_dir = str(tmp_path / "embeddings.parquet.chunks")
    with open(fetch_output_path, "w") as f:
        f.write('{"specieskey": "SP1", "species": "Turdus merula", "common_name": null, '
                '"status": "ok", "extract": "A common garden bird."}\n')
        f.write('{"specieskey": "SP2", "species": "Parus caeruleus", "common_name": null, '
                '"status": "ok", "extract": "A small, colourful passerine."}\n')
    write_chunk_parquet([{"specieskey": "SP1", "embedding": [9.0, 9.0]}], chunk_dir, dim=2)

    calls = []

    def fake_embed_fn(texts):
        calls.append(list(texts))
        return [[1.0, 2.0] for _ in texts]

    summary = run(fetch_output_path, embeddings_output_path, embed_fn=fake_embed_fn, dim=2)

    # only SP2 was actually sent to the embed_fn - SP1 was skipped as already checkpointed
    assert calls == [["Parus caeruleus: A small, colourful passerine."]]
    con = duckdb.connect()
    result = con.execute(f"SELECT specieskey, embedding FROM read_parquet('{embeddings_output_path}') ORDER BY specieskey").fetchall()
    assert result == [("SP1", [9.0, 9.0]), ("SP2", [1.0, 2.0])]
    assert summary == {"total_fetched": 2, "embedded": 2, "skipped": 0}


def test_check_output_consistency_confirms_output_matches_the_embeddable_species(tmp_path):
    path = str(tmp_path / "embeddings.parquet")
    write_embeddings_parquet([{"specieskey": "SP1", "embedding": [0.1, 0.2]}], path, dim=2)

    result = check_output_consistency({"SP1"}, path)

    assert result == {
        "embeddable_species": 1,
        "output_row_count": 1,
        "output_distinct_specieskeys": 1,
        "rows_match_distinct_keys": True,
        "output_matches_embeddable": True,
    }


def test_check_output_consistency_flags_when_output_is_missing_an_embeddable_species(tmp_path):
    path = str(tmp_path / "embeddings.parquet")
    write_embeddings_parquet([{"specieskey": "SP1", "embedding": [0.1, 0.2]}], path, dim=2)

    result = check_output_consistency({"SP1", "SP2"}, path)

    assert result["output_matches_embeddable"] is False
    assert result["embeddable_species"] == 2


def test_run_logs_a_consistency_check_against_the_real_output_file(caplog, tmp_path):
    fetch_output_path = str(tmp_path / "extracts.jsonl")
    embeddings_output_path = str(tmp_path / "embeddings.parquet")
    with open(fetch_output_path, "w") as f:
        f.write('{"specieskey": "SP1", "species": "Turdus merula", "common_name": "Common Blackbird", '
                '"status": "ok", "extract": "A common garden bird."}\n')

    def fake_embed_fn(texts):
        return [[1.0, 2.0] for _ in texts]

    with caplog.at_level("INFO"):
        run(fetch_output_path, embeddings_output_path, embed_fn=fake_embed_fn, dim=2)

    consistency_records = [r for r in caplog.records if r.message == "wikipedia_embed_extracts_consistency_check"]
    assert len(consistency_records) == 1
    assert consistency_records[0].output_matches_embeddable is True
    assert consistency_records[0].embeddable_species == 1


def test_run_processes_embeddable_species_in_chunks_logging_progress_per_chunk(caplog, tmp_path):
    fetch_output_path = str(tmp_path / "extracts.jsonl")
    embeddings_output_path = str(tmp_path / "embeddings.parquet")
    with open(fetch_output_path, "w") as f:
        f.writelines(f'{{"specieskey": "SP{i}", "species": "Species {i}", "common_name": null, '
                     f'"status": "ok", "extract": "An extract."}}\n' for i in range(5))

    def fake_embed_fn(texts):
        return [[1.0, 2.0] for _ in texts]

    with caplog.at_level("INFO"):
        run(fetch_output_path, embeddings_output_path, embed_fn=fake_embed_fn, dim=2, chunk_size=2)

    progress_records = [r for r in caplog.records if r.message == "wikipedia_embed_extracts_progress"]
    assert len(progress_records) == 3  # chunks of 2, 2, 1
    assert [r.species_embedded for r in progress_records] == [2, 4, 5]
