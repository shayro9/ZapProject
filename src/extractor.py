"""AI-powered client data extractor using OpenAI GPT-4o."""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from openai import AsyncOpenAI
from pydantic import ValidationError

from models.schemas import ClientCard, ServiceCategory

logger = logging.getLogger(__name__)

# Module-level singleton — created once on first use so that the env var
# is read after load_dotenv() has been called in the CLI entry-point.
_openai_client: AsyncOpenAI | None = None


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _openai_client

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# Max total characters of scraped text sent to the model (~3 tokens/char → ~4 000 tokens)
_MAX_TOTAL_CHARS = 12_000
# Max chars taken from each individual page
_MAX_PER_PAGE_CHARS = 4_000


class ExtractionError(Exception):
    """Raised when extraction fails (OpenAI error or schema validation failure)."""
    pass


def _build_user_message(texts: dict[str, str]) -> str:
    """
    Concatenate scraped page texts with URL separators.

    Each page contributes at most _MAX_PER_PAGE_CHARS characters.
    Total is capped at _MAX_TOTAL_CHARS.
    """
    parts: list[str] = []
    total = 0

    for url, text in texts.items():
        if not text.strip():
            continue
        chunk = text[:_MAX_PER_PAGE_CHARS]
        header = f"\n=== URL: {url} ===\n"
        segment = header + chunk
        if total + len(segment) > _MAX_TOTAL_CHARS:
            remaining = _MAX_TOTAL_CHARS - total
            if remaining > len(header) + 100:
                parts.append(header + chunk[: remaining - len(header)])
            break
        parts.append(segment)
        total += len(segment)

    return "\n".join(parts).strip()


async def extract_client_card(
    texts: dict[str, str],
    source_urls: list[str],
) -> ClientCard:
    """
    Extract structured client data from scraped texts using GPT-4o.

    Concatenates all scraped text (capped at ~12 000 chars), sends to GPT-4o
    with the extraction system prompt, parses the JSON response into a
    validated ClientCard, and attaches source_urls + extracted_at.

    Raises ExtractionError on API failure or schema validation error.
    """
    # Load system prompt
    prompt_path = PROMPTS_DIR / "extraction.txt"
    try:
        system_prompt = prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ExtractionError(f"Cannot read extraction prompt: {exc}") from exc

    user_message = _build_user_message(texts)
    if not user_message:
        raise ExtractionError(
            "No scraped text available for extraction. "
            "Ensure at least one URL was scraped successfully."
        )

    logger.info(
        "Sending %d chars to GPT-4o for extraction (source_urls=%s)",
        len(user_message),
        source_urls,
    )

    client = _get_openai_client()

    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0,
            max_tokens=2048,
        )
    except Exception as exc:
        raise ExtractionError(f"OpenAI API call failed: {exc}") from exc

    raw_json = response.choices[0].message.content or ""
    logger.debug("GPT-4o extraction response: %s", raw_json[:500])

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ExtractionError(
            f"GPT-4o returned invalid JSON: {exc}\nRaw: {raw_json[:300]}"
        ) from exc

    # Normalise services list
    raw_services = data.get("services") or []
    services = [
        ServiceCategory(
            name=str(s.get("name", "")),
            description=str(s.get("description", "")),
        )
        for s in raw_services
        if isinstance(s, dict)
    ]

    try:
        card = ClientCard(
            business_name=data.get("business_name") or "לא ידוע",
            owner_name=data.get("owner_name"),
            phone=data.get("phone"),
            email=data.get("email"),
            address=data.get("address"),
            area=data.get("area") or "",
            services=services,
            source_urls=source_urls,
            extracted_at=datetime.now(timezone.utc),
        )
    except ValidationError as exc:
        raise ExtractionError(f"ClientCard validation failed: {exc}") from exc

    logger.info(
        "Extracted ClientCard for '%s' with %d service(s)",
        card.business_name,
        len(card.services),
    )
    return card
