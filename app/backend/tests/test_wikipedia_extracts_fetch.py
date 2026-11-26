import itertools
import time

from jobs.wikipedia_extracts_fetch import (
    RateLimiter,
    append_results,
    read_fetched_specieskeys,
    records_to_fetch,
    resolve_batch_response,
)


def test_resolve_batch_response_maps_a_directly_matched_title_to_its_extract():
    data = {
        "query": {
            "pages": {
                "123": {"title": "Turdus merula", "extract": "A common garden bird."},
            }
        }
    }

    result = resolve_batch_response(data, ["Turdus merula"])

    assert result == {
        "Turdus merula": {"status": "ok", "extract": "A common garden bird.", "final_title": "Turdus merula"},
    }


def test_resolve_batch_response_follows_a_redirect_to_its_final_article():
    data = {
        "query": {
            "redirects": [{"from": "Blue tit", "to": "Eurasian blue tit"}],
            "pages": {
                "456": {"title": "Eurasian blue tit", "extract": "A small, colourful passerine."},
            },
        }
    }

    result = resolve_batch_response(data, ["Blue tit"])

    assert result == {
        "Blue tit": {
            "status": "ok",
            "extract": "A small, colourful passerine.",
            "final_title": "Eurasian blue tit",
        },
    }


def test_resolve_batch_response_follows_normalization_then_a_redirect():
    # A title can be renamed twice before landing on a pages entry: first
    # MediaWiki's own title normalization (underscores -> spaces, case),
    # then a real redirect.
    data = {
        "query": {
            "normalized": [{"from": "blue_tit", "to": "Blue tit"}],
            "redirects": [{"from": "Blue tit", "to": "Eurasian blue tit"}],
            "pages": {
                "456": {"title": "Eurasian blue tit", "extract": "A small, colourful passerine."},
            },
        }
    }

    result = resolve_batch_response(data, ["blue_tit"])

    assert result["blue_tit"]["status"] == "ok"
    assert result["blue_tit"]["final_title"] == "Eurasian blue tit"


def test_resolve_batch_response_marks_a_missing_page_as_missing():
    data = {
        "query": {
            "pages": {
                "-1": {"title": "Nonexistent Species Zzz", "missing": ""},
            }
        }
    }

    result = resolve_batch_response(data, ["Nonexistent Species Zzz"])

    assert result == {"Nonexistent Species Zzz": {"status": "missing", "extract": None}}


def test_resolve_batch_response_marks_a_page_with_no_extract_as_empty_extract():
    data = {
        "query": {
            "pages": {
                "789": {"title": "Stub Article", "extract": ""},
            }
        }
    }

    result = resolve_batch_response(data, ["Stub Article"])

    assert result == {"Stub Article": {"status": "empty_extract", "extract": None}}


def test_resolve_batch_response_marks_a_title_absent_from_the_response_as_not_found():
    # Defensive case: a requested title that doesn't appear anywhere in the
    # normalized/redirects/pages chain at all.
    data = {"query": {"pages": {}}}

    result = resolve_batch_response(data, ["Completely Unknown Title"])

    assert result == {"Completely Unknown Title": {"status": "not_found", "extract": None}}


def test_resolve_batch_response_resolves_every_title_in_a_mixed_batch_independently():
    data = {
        "query": {
            "redirects": [{"from": "Blue tit", "to": "Eurasian blue tit"}],
            "pages": {
                "1": {"title": "Turdus merula", "extract": "A common garden bird."},
                "2": {"title": "Eurasian blue tit", "extract": "A small, colourful passerine."},
                "-1": {"title": "Nonexistent Species Zzz", "missing": ""},
            },
        }
    }

    result = resolve_batch_response(data, ["Turdus merula", "Blue tit", "Nonexistent Species Zzz"])

    assert result["Turdus merula"]["status"] == "ok"
    assert result["Blue tit"]["status"] == "ok"
    assert result["Blue tit"]["final_title"] == "Eurasian blue tit"
    assert result["Nonexistent Species Zzz"]["status"] == "missing"


# --- Checkpointing: restart from where a crashed run left off --------------


def test_records_to_fetch_excludes_specieskeys_already_in_the_output_file():
    records = [{"specieskey": "SP1"}, {"specieskey": "SP2"}, {"specieskey": "SP3"}]
    already_fetched = {"SP1", "SP3"}

    result = records_to_fetch(records, already_fetched)

    assert result == [{"specieskey": "SP2"}]


def test_records_to_fetch_returns_every_record_when_nothing_fetched_yet():
    records = [{"specieskey": "SP1"}, {"specieskey": "SP2"}]

    result = records_to_fetch(records, already_fetched=set())

    assert result == records


def test_read_fetched_specieskeys_returns_empty_set_when_no_output_file_exists_yet(tmp_path):
    output_path = str(tmp_path / "does_not_exist.jsonl")

    result = read_fetched_specieskeys(output_path)

    assert result == set()


def test_append_results_then_read_fetched_specieskeys_round_trips(tmp_path):
    output_path = str(tmp_path / "extracts.jsonl")

    append_results(output_path, [{"specieskey": "SP1", "status": "ok", "extract": "text"}])
    append_results(output_path, [{"specieskey": "SP2", "status": "missing", "extract": None}])

    assert read_fetched_specieskeys(output_path) == {"SP1", "SP2"}


def test_append_results_is_flushed_immediately_so_a_crash_mid_batch_keeps_earlier_rows(tmp_path):
    # The whole point of this checkpoint: each append_results call (one per
    # batch) must be durable on its own, not buffered until the process
    # exits cleanly - simulated here by reading back with a fresh open()
    # rather than relying on the writer's own file handle/buffers.
    output_path = str(tmp_path / "extracts.jsonl")

    append_results(output_path, [{"specieskey": "SP1", "status": "ok", "extract": "text"}])

    with open(output_path) as f:
        lines = f.readlines()
    assert len(lines) == 1


# --- Rate limiting: stay under Wikimedia's documented requests/min ceiling --


def test_rate_limiter_enforces_the_minimum_interval_between_consecutive_waits():
    # A high rate (6000/min = one slot every 10ms) keeps this test fast while
    # still exercising real pacing behaviour, not a mocked clock.
    limiter = RateLimiter(rate_per_min=6000)

    limiter.wait()
    start = time.perf_counter()
    limiter.wait()
    elapsed = time.perf_counter() - start

    assert elapsed >= 0.01 * 0.9  # small tolerance for scheduling jitter


def test_rate_limiter_gives_every_concurrent_caller_a_distinct_non_overlapping_slot():
    # Correctness here is "every thread gets its own slot, none shared or
    # skipped" (the lock around _next_slot) - checked via each thread's own
    # reserved delay, not real wall-clock gaps (which is inherently flaky
    # under OS thread scheduling jitter).
    import concurrent.futures

    limiter = RateLimiter(rate_per_min=6000)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        slots = list(executor.map(lambda _: limiter._reserve_slot(), range(10)))

    slots.sort()
    gaps = [round(b - a, 6) for a, b in itertools.pairwise(slots)]
    assert all(gap >= round(limiter.min_interval, 6) for gap in gaps)
