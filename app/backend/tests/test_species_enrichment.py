import threading
import time
from unittest.mock import patch

from services.species_enrichment import enrich_species


def _species(species="Turdus merula", common_name="Common Blackbird", **extra):
    return {"species": species, "common_name": common_name, "count": 5, **extra}


def test_adds_image_url_and_extract_to_each_species():
    with patch(
        "services.species_enrichment.fetch_species_summary",
        return_value={"image_url": "https://example.com/blackbird.jpg", "extract": "A common garden bird."},
    ):
        enriched = enrich_species([_species()])

    assert enriched[0]["image_url"] == "https://example.com/blackbird.jpg"
    assert enriched[0]["extract"] == "A common garden bird."
    # Original fields untouched.
    assert enriched[0]["species"] == "Turdus merula"
    assert enriched[0]["common_name"] == "Common Blackbird"
    assert enriched[0]["count"] == 5


def test_looks_up_the_summary_using_the_already_joined_common_name():
    with patch("services.species_enrichment.fetch_species_summary") as mock_summary:
        mock_summary.return_value = {"image_url": None, "extract": None}
        enrich_species([_species(species="Turdus merula", common_name="Common Blackbird")])

    mock_summary.assert_called_once_with("Common Blackbird", "Turdus merula")


def test_looks_up_the_summary_with_no_common_name_when_none_was_joined():
    with patch("services.species_enrichment.fetch_species_summary") as mock_summary:
        mock_summary.return_value = {"image_url": None, "extract": None}
        enrich_species([_species(species="Turdus merula", common_name=None)])

    mock_summary.assert_called_once_with(None, "Turdus merula")


def test_preserves_input_order_even_when_the_first_species_finishes_last():
    delay_by_species = {"First": 0.15, "Second": 0.0}

    def fake_summary(common_name, species):
        time.sleep(delay_by_species[species])
        return {"image_url": None, "extract": None}

    with patch("services.species_enrichment.fetch_species_summary", side_effect=fake_summary):
        enriched = enrich_species([_species(species="First"), _species(species="Second")])

    assert [s["species"] for s in enriched] == ["First", "Second"]


def test_enriches_species_concurrently_not_sequentially():
    per_species_delay = 0.2

    def fake_summary(common_name, species):
        time.sleep(per_species_delay)
        return {"image_url": None, "extract": None}

    with patch("services.species_enrichment.fetch_species_summary", side_effect=fake_summary):
        start = time.monotonic()
        enrich_species([_species(species=f"Species{i}") for i in range(3)])
        elapsed = time.monotonic() - start

    assert elapsed < 0.4


def test_caps_concurrent_enrichment_at_three():
    lock = threading.Lock()
    concurrent_count = 0
    max_concurrent_seen = 0

    def fake_summary(common_name, species):
        nonlocal concurrent_count, max_concurrent_seen
        with lock:
            concurrent_count += 1
            max_concurrent_seen = max(max_concurrent_seen, concurrent_count)
        time.sleep(0.1)
        with lock:
            concurrent_count -= 1
        return {"image_url": None, "extract": None}

    with patch("services.species_enrichment.fetch_species_summary", side_effect=fake_summary):
        enrich_species([_species(species=f"Species{i}") for i in range(6)])

    assert max_concurrent_seen <= 3


def test_returns_empty_list_when_given_no_species():
    assert enrich_species([]) == []


def test_logs_a_wikipedia_fetch_checkpoint_per_species(caplog):
    with (
        caplog.at_level("INFO"),
        patch(
            "services.species_enrichment.fetch_species_summary",
            return_value={"image_url": None, "extract": None},
        ),
    ):
        enrich_species([_species(species="Turdus merula"), _species(species="Pica pica")])

    checkpoints = [r for r in caplog.records if r.message == "timing_checkpoint"]
    assert {r.checkpoint for r in checkpoints} == {"t_wikipedia_fetch"}
    assert sorted(r.species for r in checkpoints) == ["Pica pica", "Turdus merula"]
