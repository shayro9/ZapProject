"""Call script generator using OpenAI GPT-4o."""

import logging
import os
from pathlib import Path

from openai import AsyncOpenAI

from models.schemas import ClientCard

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


class CallScriptError(Exception):
    """Raised when call script generation fails."""
    pass


async def generate_call_script(client_card: ClientCard) -> str:
    """
    Generate a personalised Hebrew onboarding call script for the given client.

    Serialises the ClientCard to a pretty-printed JSON summary and sends it to
    GPT-4o with the call_script system prompt. Returns the generated script text.

    Raises CallScriptError on API failure or empty response.
    """
    prompt_path = PROMPTS_DIR / "call_script.txt"
    try:
        system_prompt = prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CallScriptError(f"Cannot read call script prompt: {exc}") from exc

    # Serialise the card as a readable JSON summary for the model
    user_message = client_card.model_dump_json(indent=2)

    logger.info(
        "Generating call script for '%s' (area: %s, services: %d)",
        client_card.business_name,
        client_card.area,
        len(client_card.services),
    )

    client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    try:
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.7,
            max_tokens=2048,
        )
    except Exception as exc:
        raise CallScriptError(f"OpenAI API call failed: {exc}") from exc

    script = (response.choices[0].message.content or "").strip()

    if not script:
        raise CallScriptError("GPT-4o returned an empty call script.")

    logger.info(
        "Generated call script (%d chars) for '%s'",
        len(script),
        client_card.business_name,
    )
    return script
