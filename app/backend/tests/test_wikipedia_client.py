from unittest.mock import patch

import httpx

from services.wikipedia_client import fetch_species_summary


def _summary(extract="A common garden bird.", thumbnail=None, originalimage=None, page_type=None):
    body = {"extract": extract}
    if thumbnail:
        body["thumbnail"] = {"source": thumbnail}
    if originalimage:
        body["originalimage"] = {"source": originalimage}
    if page_type:
        body["type"] = page_type
    return body


def test_fetch_species_summary_returns_the_extract_alongside_the_image():
    with patch("services.wikipedia_client.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = _summary(
            extract="A widespread thrush found across Europe.",
            thumbnail="https://upload.wikimedia.org/blackbird.jpg",
        )

        summary = fetch_species_summary("Common Blackbird", "Turdus merula")

    assert summary == {
        "image_url": "https://upload.wikimedia.org/blackbird.jpg",
        "extract": "A widespread thrush found across Europe.",
    }


def test_fetch_species_summary_uses_the_common_name_article_first():
    with patch("services.wikipedia_client.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = _summary(
            thumbnail="https://upload.wikimedia.org/blackbird.jpg"
        )

        summary = fetch_species_summary("Common Blackbird", "Turdus merula")

    assert summary["image_url"] == "https://upload.wikimedia.org/blackbird.jpg"
    called_url = mock_get.call_args[0][0]
    assert "Common%20Blackbird" in called_url or "Common_Blackbird" in called_url


def test_fetch_species_summary_falls_back_to_scientific_name_on_disambiguation():
    responses = [
        _summary(page_type="disambiguation"),
        _summary(thumbnail="https://upload.wikimedia.org/turdus.jpg"),
    ]
    with patch("services.wikipedia_client.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.side_effect = responses

        summary = fetch_species_summary("Blackbird", "Turdus merula")

    assert summary["image_url"] == "https://upload.wikimedia.org/turdus.jpg"
    assert mock_get.call_count == 2


def test_fetch_species_summary_falls_back_to_scientific_name_when_common_name_article_missing():
    with patch("services.wikipedia_client.httpx.get") as mock_get:
        mock_get.side_effect = [
            type("Resp", (), {"status_code": 404, "json": lambda self: {}})(),
            type("Resp", (), {"status_code": 200, "json": lambda self: _summary(thumbnail="https://upload.wikimedia.org/x.jpg")})(),
        ]

        summary = fetch_species_summary("Nonexistent Name", "Turdus merula")

    assert summary["image_url"] == "https://upload.wikimedia.org/x.jpg"


def test_fetch_species_summary_uses_scientific_name_directly_when_no_common_name_given():
    with patch("services.wikipedia_client.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = _summary(thumbnail="https://upload.wikimedia.org/y.jpg")

        summary = fetch_species_summary(None, "Turdus merula")

    assert summary["image_url"] == "https://upload.wikimedia.org/y.jpg"
    assert mock_get.call_count == 1
    called_url = mock_get.call_args[0][0]
    assert "Turdus" in called_url


def test_fetch_species_summary_falls_back_to_originalimage_when_no_thumbnail():
    with patch("services.wikipedia_client.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = _summary(originalimage="https://upload.wikimedia.org/full.jpg")

        summary = fetch_species_summary(None, "Turdus merula")

    assert summary["image_url"] == "https://upload.wikimedia.org/full.jpg"


def test_fetch_species_summary_returns_none_fields_when_article_has_no_extract():
    with patch("services.wikipedia_client.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = _summary(extract=None, thumbnail="https://upload.wikimedia.org/z.jpg")

        assert fetch_species_summary(None, "Turdus merula") == {"image_url": None, "extract": None}


def test_fetch_species_summary_rejects_thumbnail_from_an_untrusted_host():
    with patch("services.wikipedia_client.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = _summary(thumbnail="https://evil.example.com/blackbird.jpg")

        assert fetch_species_summary(None, "Turdus merula")["image_url"] is None


def test_fetch_species_summary_rejects_originalimage_from_an_untrusted_host():
    with patch("services.wikipedia_client.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = _summary(originalimage="https://evil.example.com/blackbird.jpg")

        assert fetch_species_summary(None, "Turdus merula")["image_url"] is None


def test_fetch_species_summary_rejects_a_scheme_other_than_https():
    with patch("services.wikipedia_client.httpx.get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = _summary(thumbnail="http://upload.wikimedia.org/blackbird.jpg")

        assert fetch_species_summary(None, "Turdus merula")["image_url"] is None


def test_fetch_species_summary_returns_none_fields_when_neither_lookup_succeeds():
    with patch("services.wikipedia_client.httpx.get") as mock_get:
        mock_get.return_value.status_code = 404
        mock_get.return_value.json.return_value = {}

        assert fetch_species_summary("Common Blackbird", "Turdus merula") == {"image_url": None, "extract": None}


def test_fetch_species_summary_returns_none_fields_on_request_failure():
    with patch("services.wikipedia_client.httpx.get", side_effect=httpx.HTTPError("boom")):
        assert fetch_species_summary(None, "Turdus merula") == {"image_url": None, "extract": None}
