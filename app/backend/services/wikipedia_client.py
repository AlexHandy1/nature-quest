from urllib.parse import quote, urlparse

import httpx

WIKIPEDIA_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary"
TRUSTED_IMAGE_HOST = "upload.wikimedia.org"
USER_AGENT = "nature-quest/0.1"
REQUEST_TIMEOUT = 30.0


def fetch_species_summary(common_name: str | None, scientific_name: str) -> dict:
    """Looks up a species' Wikipedia article image and extract, trying the
    common name first (more likely to match the article title humans would
    search for) and falling back to the scientific name if that article
    doesn't exist or is a disambiguation page."""
    summary = _fetch_summary(common_name) if common_name else None
    if summary is None:
        summary = _fetch_summary(scientific_name)
    if summary is None:
        return {"image_url": None, "extract": None}
    thumbnail = summary.get("thumbnail") or {}
    original = summary.get("originalimage") or {}
    image_url = thumbnail.get("source") or original.get("source")
    return {
        "image_url": image_url if _is_trusted_image_url(image_url) else None,
        "extract": summary.get("extract"),
    }


def _is_trusted_image_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == TRUSTED_IMAGE_HOST


def _fetch_summary(title: str) -> dict | None:
    try:
        response = httpx.get(
            f"{WIKIPEDIA_SUMMARY_URL}/{quote(title)}",
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None

    data = response.json()
    if data.get("type") == "disambiguation" or not data.get("extract"):
        return None
    return data
