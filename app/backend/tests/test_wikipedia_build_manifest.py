import os

import duckdb

from jobs.wikipedia_build_manifest import (
    build_manifest,
    ensure_titles_dump,
    load_candidate_species,
    load_titles,
    run,
    to_title_case,
)


def test_to_title_case_replaces_spaces_with_underscores_and_capitalizes_first_letter():
    assert to_title_case("house sparrow") == "House_sparrow"


def test_to_title_case_leaves_an_already_capitalized_name_unchanged():
    assert to_title_case("Turdus merula") == "Turdus_merula"


def test_to_title_case_handles_a_single_word_name():
    assert to_title_case("sparrow") == "Sparrow"


# --- Downloading the titles dump, cached on disk ----------------------------


def test_ensure_titles_dump_downloads_when_the_file_does_not_exist_yet(tmp_path):
    local_path = str(tmp_path / "dumps" / "titles.gz")
    download_calls = []

    def fake_download(url, path):
        download_calls.append((url, path))
        with open(path, "w") as f:
            f.write("page_title\nHouse_sparrow\n")

    ensure_titles_dump(local_path, download_fn=fake_download)

    assert len(download_calls) == 1
    assert download_calls[0][1] == local_path
    assert os.path.exists(local_path)


def test_ensure_titles_dump_skips_downloading_when_the_file_already_exists(tmp_path):
    local_path = str(tmp_path / "titles.gz")
    with open(local_path, "w") as f:
        f.write("page_title\nHouse_sparrow\n")
    download_calls = []

    def tracking_download(url, path):
        download_calls.append((url, path))

    ensure_titles_dump(local_path, download_fn=tracking_download)

    assert download_calls == []


# --- Loading candidate species from production fact/dimension tables -------


def _write_fact_and_dimension(tmp_path, fact_rows: list[tuple], dimension_rows: list[tuple]) -> tuple[str, str]:
    con = duckdb.connect()
    fact_path = str(tmp_path / "fact.parquet")
    dimension_path = str(tmp_path / "dimension.parquet")
    fact_values = ", ".join(f"('{cell}', '{key}', {count})" for cell, key, count in fact_rows)
    con.execute(f"""
        COPY (SELECT * FROM (VALUES {fact_values}) AS t(cell, specieskey, occurrence_count))
        TO '{fact_path}' (FORMAT PARQUET)
    """)
    dim_values = ", ".join(
        "('{}', '{}', {})".format(key, species, "NULL::VARCHAR" if common is None else f"'{common}'")
        for key, species, common in dimension_rows
    )
    con.execute(f"""
        COPY (SELECT * FROM (VALUES {dim_values}) AS t(specieskey, species, common_name))
        TO '{dimension_path}' (FORMAT PARQUET)
    """)
    return fact_path, dimension_path


def test_load_candidate_species_sums_occurrence_across_cells_and_excludes_at_or_below_the_threshold(tmp_path):
    fact_path, dimension_path = _write_fact_and_dimension(
        tmp_path,
        fact_rows=[("u4pruy", "SP1", 5), ("u4pruz", "SP1", 5), ("u4pruy", "SP2", 1)],
        dimension_rows=[("SP1", "Turdus merula", "Common Blackbird"), ("SP2", "Rare Species", None)],
    )
    con = duckdb.connect()

    result = load_candidate_species(con, fact_path, dimension_path, min_occurrence_count=1)

    assert sorted(result, key=lambda r: r["specieskey"]) == [
        {"specieskey": "SP1", "species": "Turdus merula", "common_name": "Common Blackbird", "total_occurrence": 10},
    ]


# --- Loading the titles dump ------------------------------------------------


def test_load_titles_reads_the_page_title_column_from_the_dump(tmp_path):
    dump_path = str(tmp_path / "titles.csv")
    with open(dump_path, "w") as f:
        f.write("page_title\nTurdus_merula\nHouse_sparrow\n")
    con = duckdb.connect()

    load_titles(con, dump_path)

    result = con.execute("SELECT page_title FROM titles ORDER BY page_title").fetchall()
    assert result == [("House_sparrow",), ("Turdus_merula",)]


# --- Building the final manifest --------------------------------------------


def test_build_manifest_matches_on_scientific_name_when_available(tmp_path):
    fact_path, dimension_path = _write_fact_and_dimension(
        tmp_path, fact_rows=[("u4pruy", "SP1", 10)],
        dimension_rows=[("SP1", "Turdus merula", "Common Blackbird")],
    )
    dump_path = str(tmp_path / "titles.csv")
    with open(dump_path, "w") as f:
        f.write("page_title\nTurdus_merula\n")
    output_path = str(tmp_path / "manifest.parquet")
    con = duckdb.connect()
    con.create_function("to_title_case", to_title_case)
    load_titles(con, dump_path)
    load_candidate_species(con, fact_path, dimension_path, min_occurrence_count=1)

    stats = build_manifest(con, output_path)

    result = con.execute(f"SELECT * FROM read_parquet('{output_path}')").fetchall()
    assert result == [("SP1", "Turdus merula", "Common Blackbird", 10, "Turdus_merula", True)]
    assert stats == {"candidate_species": 1, "scientific_name_match": 1, "common_name_match": 0,
                      "either_match": 1, "hit_rate": 1.0}


def test_build_manifest_falls_back_to_common_name_when_scientific_name_has_no_article(tmp_path):
    fact_path, dimension_path = _write_fact_and_dimension(
        tmp_path, fact_rows=[("u4pruy", "SP1", 10)],
        dimension_rows=[("SP1", "Turdus merula", "Common Blackbird")],
    )
    dump_path = str(tmp_path / "titles.csv")
    with open(dump_path, "w") as f:
        f.write("page_title\nCommon_Blackbird\n")
    output_path = str(tmp_path / "manifest.parquet")
    con = duckdb.connect()
    con.create_function("to_title_case", to_title_case)
    load_titles(con, dump_path)
    load_candidate_species(con, fact_path, dimension_path, min_occurrence_count=1)

    build_manifest(con, output_path)

    result = con.execute(f"SELECT matched_title, matched_on_scientific_name FROM read_parquet('{output_path}')").fetchall()
    assert result == [("Common_Blackbird", False)]


def test_build_manifest_excludes_a_species_matching_neither_title_form(tmp_path):
    fact_path, dimension_path = _write_fact_and_dimension(
        tmp_path, fact_rows=[("u4pruy", "SP1", 10)],
        dimension_rows=[("SP1", "Unmatched species", None)],
    )
    dump_path = str(tmp_path / "titles.csv")
    with open(dump_path, "w") as f:
        f.write("page_title\nSome_other_article\n")
    output_path = str(tmp_path / "manifest.parquet")
    con = duckdb.connect()
    con.create_function("to_title_case", to_title_case)
    load_titles(con, dump_path)
    load_candidate_species(con, fact_path, dimension_path, min_occurrence_count=1)

    stats = build_manifest(con, output_path)

    result = con.execute(f"SELECT * FROM read_parquet('{output_path}')").fetchall()
    assert result == []
    assert stats["either_match"] == 0
    assert stats["hit_rate"] == 0.0


# --- Orchestration -----------------------------------------------------------


def test_run_downloads_titles_if_missing_and_writes_the_manifest(tmp_path):
    fact_path, dimension_path = _write_fact_and_dimension(
        tmp_path, fact_rows=[("u4pruy", "SP1", 10)],
        dimension_rows=[("SP1", "Turdus merula", "Common Blackbird")],
    )
    titles_dump_path = str(tmp_path / "dumps" / "titles.csv")
    output_path = str(tmp_path / "manifest.parquet")

    def fake_download(url, path):
        with open(path, "w") as f:
            f.write("page_title\nTurdus_merula\n")

    stats = run(fact_path, dimension_path, titles_dump_path, output_path,
                min_occurrence_count=1, download_fn=fake_download)

    assert stats["either_match"] == 1
    con = duckdb.connect()
    result = con.execute(f"SELECT specieskey FROM read_parquet('{output_path}')").fetchall()
    assert result == [("SP1",)]


def test_run_reuses_an_already_downloaded_titles_dump(tmp_path):
    fact_path, dimension_path = _write_fact_and_dimension(
        tmp_path, fact_rows=[("u4pruy", "SP1", 10)],
        dimension_rows=[("SP1", "Turdus merula", "Common Blackbird")],
    )
    titles_dump_path = str(tmp_path / "titles.csv")
    with open(titles_dump_path, "w") as f:
        f.write("page_title\nTurdus_merula\n")
    output_path = str(tmp_path / "manifest.parquet")
    download_calls = []

    def tracking_download(url, path):
        download_calls.append(path)

    run(fact_path, dimension_path, titles_dump_path, output_path,
        min_occurrence_count=1, download_fn=tracking_download)

    assert download_calls == []
