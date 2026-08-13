import threading
import time
from unittest.mock import patch

from services.species_enrichment import enrich_species


def _species(species="Turdus merula", species_key=2495414, **extra):
    return {"species": species, "species_key": species_key, "count": 5, **extra}


def test_adds_common_name_and_image_url_to_each_species():
    with (
        patch("services.species_enrichment.fetch_common_name", return_value="Common Blackbird"),
        patch("services.species_enrichment.fetch_species_image", return_value="https://example.com/blackbird.jpg"),
    ):
        enriched = enrich_species([_species()])

    assert enriched[0]["common_name"] == "Common Blackbird"
    assert enriched[0]["image_url"] == "https://example.com/blackbird.jpg"
    # Original fields untouched.
    assert enriched[0]["species"] == "Turdus merula"
    assert enriched[0]["count"] == 5


def test_looks_up_the_image_using_the_resolved_common_name():
    with (
        patch("services.species_enrichment.fetch_common_name", return_value="Common Blackbird"),
        patch("services.species_enrichment.fetch_species_image") as mock_image,
    ):
        enrich_species([_species(species="Turdus merula")])

    mock_image.assert_called_once_with("Common Blackbird", "Turdus merula")


def test_looks_up_the_image_with_no_common_name_when_none_was_found():
    with (
        patch("services.species_enrichment.fetch_common_name", return_value=None),
        patch("services.species_enrichment.fetch_species_image") as mock_image,
    ):
        enrich_species([_species(species="Turdus merula")])

    mock_image.assert_called_once_with(None, "Turdus merula")


def test_preserves_input_order_even_when_the_first_species_finishes_last():
    delay_by_key = {1: 0.15, 2: 0.0}

    def fake_common_name(species_key):
        time.sleep(delay_by_key[species_key])
        return f"Name{species_key}"

    with (
        patch("services.species_enrichment.fetch_common_name", side_effect=fake_common_name),
        patch("services.species_enrichment.fetch_species_image", return_value=None),
    ):
        enriched = enrich_species([_species(species="First", species_key=1), _species(species="Second", species_key=2)])

    assert [s["species"] for s in enriched] == ["First", "Second"]


def test_enriches_species_concurrently_not_sequentially():
    per_species_delay = 0.2

    def fake_common_name(species_key):
        time.sleep(per_species_delay)

    with (
        patch("services.species_enrichment.fetch_common_name", side_effect=fake_common_name),
        patch("services.species_enrichment.fetch_species_image", return_value=None),
    ):
        start = time.monotonic()
        enrich_species([_species(species_key=i) for i in range(3)])
        elapsed = time.monotonic() - start

    assert elapsed < 0.4


def test_caps_concurrent_enrichment_at_three():
    lock = threading.Lock()
    concurrent_count = 0
    max_concurrent_seen = 0

    def fake_common_name(species_key):
        nonlocal concurrent_count, max_concurrent_seen
        with lock:
            concurrent_count += 1
            max_concurrent_seen = max(max_concurrent_seen, concurrent_count)
        time.sleep(0.1)
        with lock:
            concurrent_count -= 1

    with (
        patch("services.species_enrichment.fetch_common_name", side_effect=fake_common_name),
        patch("services.species_enrichment.fetch_species_image", return_value=None),
    ):
        enrich_species([_species(species_key=i) for i in range(6)])

    assert max_concurrent_seen <= 3


def test_returns_empty_list_when_given_no_species():
    assert enrich_species([]) == []
