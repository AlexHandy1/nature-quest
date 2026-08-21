import re
from types import SimpleNamespace
from unittest.mock import MagicMock

from services.narration import REFUSAL_SENTINEL, generate_narrative, sanitize_dash_pauses


def _species(common_name="Eurasian Magpie", species="Pica pica", hotspot_lat=40.4148, hotspot_lon=-3.6846, extract="A common corvid."):
    return {"common_name": common_name, "species": species, "hotspot_lat": hotspot_lat, "hotspot_lon": hotspot_lon, "extract": extract}


def _mock_client(narrative_text: str) -> MagicMock:
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=narrative_text)]
    )
    return client


def test_generate_narrative_returns_the_models_text_response():
    client = _mock_client("A walk through the park.")

    narrative = generate_narrative([_species()], client)

    assert narrative == "A walk through the park."


def test_generate_narrative_includes_each_species_extract_in_the_prompt():
    client = _mock_client("A walk through the park.")

    generate_narrative([_species(extract="A widespread thrush found across Europe.")], client)

    sent_prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "A widespread thrush found across Europe." in sent_prompt


def test_generate_narrative_sanitizes_dash_pauses_in_the_models_response():
    client = _mock_client("The magpie glides—then lands.")

    narrative = generate_narrative([_species()], client)

    assert narrative == "The magpie glides, then lands."


def test_generate_narrative_notes_a_missing_wikipedia_article_in_the_prompt():
    client = _mock_client("A walk through the park.")

    generate_narrative([_species(extract=None)], client)

    sent_prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "no Wikipedia article found" in sent_prompt


def test_generate_narrative_instructs_the_model_to_mention_every_species():
    client = _mock_client("A walk through the park.")

    generate_narrative([_species(), _species(common_name="Mallard", species="Anas platyrhynchos")], client)

    sent_prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    normalized = re.sub(r"\s+", " ", sent_prompt.lower())
    assert "mention every one of the 2 species" in normalized


def test_generate_narrative_uses_full_temperature_for_creative_variety():
    client = _mock_client("A walk through the park.")

    generate_narrative([_species()], client)

    assert client.messages.create.call_args.kwargs["temperature"] == 1


def test_generate_narrative_invokes_on_response_with_the_raw_response():
    client = _mock_client("A walk through the park.")
    seen = []

    generate_narrative([_species()], client, on_response=seen.append)

    assert seen == [client.messages.create.return_value]


def test_generate_narrative_passes_extra_kwargs_through_to_the_create_call():
    client = _mock_client("A walk through the park.")

    generate_narrative([_species()], client, posthog_distinct_id="anon-1")

    assert client.messages.create.call_args.kwargs["posthog_distinct_id"] == "anon-1"


def test_generate_narrative_returns_none_when_the_model_refuses():
    client = _mock_client(REFUSAL_SENTINEL)

    narrative = generate_narrative([_species()], client)

    assert narrative is None


def test_generate_narrative_instructs_the_model_to_refuse_non_organism_or_inappropriate_content():
    client = _mock_client("A walk through the park.")

    generate_narrative([_species()], client)

    sent_prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert REFUSAL_SENTINEL in sent_prompt


def test_sanitize_dash_pauses_replaces_em_dash_with_comma():
    assert sanitize_dash_pauses("The magpie glides—then lands.") == "The magpie glides, then lands."


def test_sanitize_dash_pauses_replaces_spaced_hyphen_with_comma():
    assert sanitize_dash_pauses("It rests here - then moves on.") == "It rests here, then moves on."


def test_sanitize_dash_pauses_leaves_hyphenated_compound_words_untouched():
    assert sanitize_dash_pauses("A well-known visitor.") == "A well-known visitor."
