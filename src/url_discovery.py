"""URL discovery via Tavily search API."""

import asyncio
import logging
import os
from typing import Optional

from tavily import TavilyClient

logger = logging.getLogger(__name__)


class DiscoveryError(Exception):
    """Raised when URL discovery fails."""
    pass


async def discover_urls(
    business_name: str,
    phone: Optional[str] = None,
) -> list[str]:
    """
    Uses Tavily to find the business website and Golden Pages listing.

    Runs two searches:
      1. '{business_name} {phone or ""} אתר אינטרנט'
      2. '{business_name} דפי זהב d.co.il'

    Returns a deduplicated list of up to 5 URLs, most relevant first.
    Raises DiscoveryError if TAVILY_API_KEY is not set or search fails.
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        raise DiscoveryError(
            "TAVILY_API_KEY environment variable is not set. "
            "Please set it before running URL discovery."
        )

    client = TavilyClient(api_key=api_key)
    phone_part = phone or ""
    query_website = f"{business_name} {phone_part} אתר אינטרנט".strip()
    query_golden = f"{business_name} דפי זהב d.co.il"

    logger.info("Searching for business URLs: '%s'", business_name)

    async def _search(query: str) -> list[str]:
        """Run a single Tavily search in a thread pool and return result URLs."""
        try:
            results = await asyncio.to_thread(
                client.search,
                query=query,
                max_results=5,
                include_raw_content=False,
            )
            urls = [r["url"] for r in results.get("results", []) if "url" in r]
            logger.debug("Query '%s' → %d URLs: %s", query, len(urls), urls)
            return urls
        except Exception as exc:
            logger.warning("Tavily search failed for query '%s': %s", query, exc)
            return []

    # Run both searches concurrently
    results_website, results_golden = await asyncio.gather(
        _search(query_website),
        _search(query_golden),
    )

    # Merge, deduplicate, preserve order (website results first)
    seen: set[str] = set()
    merged: list[str] = []
    for url in results_website + results_golden:
        if url not in seen:
            seen.add(url)
            merged.append(url)

    # Cap at 5 URLs
    final = merged[:5]

    if not final:
        logger.warning("No URLs discovered for business '%s'", business_name)
    else:
        logger.info("Discovered %d URL(s) for '%s': %s", len(final), business_name, final)

    return final
