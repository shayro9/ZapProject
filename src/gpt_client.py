"""Shared AsyncOpenAI singleton used across the application."""

import os

from openai import AsyncOpenAI

_openai_client: AsyncOpenAI | None = None


def get_openai_client() -> AsyncOpenAI:
    """Return the process-wide AsyncOpenAI instance, creating it on first call.

    Deferred instantiation ensures load_dotenv() has been called before the
    API key is read from the environment.
    """
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _openai_client
