"""Async web scraper using httpx + BeautifulSoup4."""

import asyncio
import logging
import re

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Tags whose content should be discarded entirely
_JUNK_TAGS = [
    "script", "style", "noscript", "head",
    "nav", "footer", "header", "aside",
    "form", "button", "iframe", "svg",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; ZapBot/1.0; +https://www.zap.co.il)"
    ),
    "Accept-Language": "he,en;q=0.9",
}


def _extract_text(html: str) -> str:
    """
    Parse HTML with BeautifulSoup4 and return cleaned visible text.

    Removes script/style/nav/footer tags, then extracts visible text
    with collapsed whitespace.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove boilerplate tags — find_all accepts a list of tag names
    for element in soup.find_all(_JUNK_TAGS):
        element.decompose()

    # Get text with space separator to avoid words merging
    raw = soup.get_text(separator=" ", strip=True)

    # Collapse multiple whitespace / newlines
    cleaned = re.sub(r"[ \t]+", " ", raw)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


async def _fetch_one(
    client: httpx.AsyncClient, url: str
) -> tuple[str, str]:
    """
    Fetch a single URL and return (url, cleaned_text).
    Returns (url, "") on any error.
    """
    try:
        response = await client.get(url, follow_redirects=True)
        response.raise_for_status()
        text = _extract_text(response.text)
        logger.info("Scraped '%s' → %d chars", url, len(text))
        return url, text
    except httpx.TimeoutException:
        logger.warning("Timeout fetching '%s'", url)
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "HTTP %d fetching '%s'", exc.response.status_code, url
        )
    except httpx.RequestError as exc:
        logger.warning("Connection error fetching '%s': %s", url, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Unexpected error fetching '%s': %s", url, exc)
    return url, ""


async def scrape_urls(urls: list[str]) -> dict[str, str]:
    """
    Fetch each URL concurrently with httpx (timeout=15s, follow_redirects=True).

    Parses HTML with BeautifulSoup4, removes script/style/nav/footer tags,
    and extracts visible text. Returns a dict mapping url → cleaned text.
    Failed or empty pages map to an empty string — no exceptions are raised.
    """
    if not urls:
        logger.info("No URLs to scrape.")
        return {}

    timeout = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=5.0)
    async with httpx.AsyncClient(
        headers=_HEADERS, timeout=timeout, follow_redirects=True
    ) as client:
        tasks = [_fetch_one(client, url) for url in urls]
        results = await asyncio.gather(*tasks)

    return dict(results)
