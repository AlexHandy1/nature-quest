from unittest.mock import patch

import httpx

from services.wikipedia_client import fetch_species_image


def _summary(extract="A common garden bird.", thumbnail=None, originalimage=None, page_type=None):
    body = {"extract": extract}
    if thumbnail:
        body["thumbnail"] = {"source": thumbnail}
    if originalimage:
        body["originalimage"] = {"source": originalimage}
    if page_type:
        body["type"] = page_type
    return body


def test_fetch_species_image_uses_the_common_name_article_first():
    with patch("services.wikipedia_client.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = _summary(thumbnail="https://example.com/blackbird.jpg")

        image_url = fetch_species_image("Common Blackbird", "Turdus merula")

    assert image_url == "https://example.com/blackbird.jpg"
    called_url = mock_get.call_args[0][0]
    assert "Common%20Blackbird" in called_url or "Common_Blackbird" in called_url


def test_fetch_species_image_falls_back_to_scientific_name_on_disambiguation():
    responses = [
        _summary(page_type="disambiguation"),
        _summary(thumbnail="https://example.com/turdus.jpg"),
    ]
    with patch("services.wikipedia_client.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.side_effect = responses

        image_url = fetch_species_image("Blackbird", "Turdus merula")

    assert image_url == "https://example.com/turdus.jpg"
    assert mock_get.call_count == 2


def test_fetch_species_image_falls_back_to_scientific_name_when_common_name_article_missing():
    with patch("services.wikipedia_client.httpx.get") as mock_get:
        mock_get.side_effect = [
            type("Resp", (), {"status_code": 404, "json": lambda self: {}})(),
            type("Resp", (), {"status_code": 200, "json": lambda self: _summary(thumbnail="https://example.com/x.jpg")})(),
        ]

        image_url = fetch_species_image("Nonexistent Name", "Turdus merula")

    assert image_url == "https://example.com/x.jpg"


def test_fetch_species_image_uses_scientific_name_directly_when_no_common_name_given():
    with patch("services.wikipedia_client.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = _summary(thumbnail="https://example.com/y.jpg")

        image_url = fetch_species_image(None, "Turdus merula")

    assert image_url == "https://example.com/y.jpg"
    assert mock_get.call_count == 1
    called_url = mock_get.call_args[0][0]
    assert "Turdus" in called_url


def test_fetch_species_image_falls_back_to_originalimage_when_no_thumbnail():
    with patch("services.wikipedia_client.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = _summary(originalimage="https://example.com/full.jpg")

        image_url = fetch_species_image(None, "Turdus merula")

    assert image_url == "https://example.com/full.jpg"


def test_fetch_species_image_returns_none_when_article_has_no_extract():
    with patch("services.wikipedia_client.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = _summary(extract=None, thumbnail="https://example.com/z.jpg")

        assert fetch_species_image(None, "Turdus merula") is None


def test_fetch_species_image_returns_none_when_neither_lookup_succeeds():
    with patch("services.wikipedia_client.httpx.get") as mock_get:
        mock_get.return_value.status_code = 404
        mock_get.return_value.json.return_value = {}

        assert fetch_species_image("Common Blackbird", "Turdus merula") is None


def test_fetch_species_image_returns_none_on_request_failure():
    with patch("services.wikipedia_client.httpx.get", side_effect=httpx.HTTPError("boom")):
        assert fetch_species_image(None, "Turdus merula") is None
