"""
Tests for the Zap onboarding pipeline.

Run with:
    pytest tests/test_pipeline.py -v
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# Ensure project root is on sys.path when running from tests/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.schemas import ClientCard, CRMRecord, ServiceCategory
from src import crm as crm_module
from src.scraper import scrape_urls, _extract_text
from src.extractor import extract_client_card, ExtractionError, _build_user_message
from src.call_script import generate_call_script, CallScriptError
from src.notifier import send_call_script
from src.url_discovery import discover_urls, DiscoveryError

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head><title>מיזוג שרות - חברת קור</title></head>
<body>
  <nav>ניווט | ראשי | צור קשר</nav>
  <header>Header boilerplate</header>
  <main>
    <h1>שרות מיזוג אוויר מקצועי</h1>
    <p>אנו מספקים שירותי התקנה ותיקון מזגנים בכל הקריות.</p>
    <p>טלפון: 04-8765432 | דוא&quot;ל: korcool@example.com</p>
    <p>כתובת: רחוב הנמל 5, קריית ביאליק</p>
  </main>
  <footer>Footer boilerplate</footer>
  <script>console.log("should be removed");</script>
</body>
</html>
"""

SAMPLE_EXTRACTION_JSON = {
    "business_name": "חברת קור",
    "owner_name": "דני כהן",
    "phone": "04-8765432",
    "email": "korcool@example.com",
    "address": "רחוב הנמל 5, קריית ביאליק",
    "area": "קריות",
    "services": [
        {"name": "התקנת מזגנים", "description": "התקנה מקצועית של כל סוגי המזגנים"},
        {"name": "תיקון מזגנים", "description": "תיקון תקלות בכל המותגים"},
    ],
}

SAMPLE_CALL_SCRIPT = (
    "**פתיחה**\n"
    "שלום, אני מנציג זאפ. האם נוח לך לדבר?\n\n"
    "**מטרת השיחה**\n"
    "אני מתקשר להשלים את הצטרפות חברת קור לפלטפורמה שלנו.\n\n"
    "**סיום**\n"
    "תודה רבה, שיחה טובה!\n"
)


def _make_client_card(**kwargs: Any) -> ClientCard:
    defaults = dict(
        business_name="חברת קור",
        owner_name="דני כהן",
        phone="04-8765432",
        email="korcool@example.com",
        address="רחוב הנמל 5, קריית ביאליק",
        area="קריות",
        services=[
            ServiceCategory(name="התקנת מזגנים", description="התקנה מקצועית"),
        ],
        source_urls=["https://example.com"],
        extracted_at=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return ClientCard(**defaults)


# ---------------------------------------------------------------------------
# 1. Scraper tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scraper_returns_text() -> None:
    """Patch httpx.AsyncClient.get to return mock HTML; verify BS4 extracts visible text."""

    mock_response = MagicMock()
    mock_response.text = SAMPLE_HTML
    mock_response.raise_for_status = MagicMock()

    with patch("src.scraper.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await scrape_urls(["https://example.com"])

    assert "https://example.com" in result
    text = result["https://example.com"]
    assert "שרות מיזוג אוויר מקצועי" in text
    assert "04-8765432" in text
    # Boilerplate should be stripped
    assert "console.log" not in text
    assert "Footer boilerplate" not in text


def test_extract_text_removes_script_and_nav() -> None:
    """Unit test _extract_text helper directly."""
    text = _extract_text(SAMPLE_HTML)
    assert "שרות מיזוג אוויר מקצועי" in text
    assert "console.log" not in text
    assert "ניווט" not in text  # nav removed


# ---------------------------------------------------------------------------
# 2. Extractor tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extractor_builds_client_card() -> None:
    """Patch OpenAI to return mock JSON; verify a valid ClientCard is returned."""

    mock_message = MagicMock()
    mock_message.content = json.dumps(SAMPLE_EXTRACTION_JSON)

    mock_choice = MagicMock()
    mock_choice.message = mock_message

    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]

    mock_chat = AsyncMock()
    mock_chat.completions.create = AsyncMock(return_value=mock_completion)

    mock_openai = MagicMock()
    mock_openai.chat = mock_chat

    texts = {"https://example.com": "some scraped text about the business"}

    with patch("src.extractor.AsyncOpenAI", return_value=mock_openai):
        card = await extract_client_card(texts, ["https://example.com"])

    assert isinstance(card, ClientCard)
    assert card.business_name == "חברת קור"
    assert card.phone == "04-8765432"
    assert card.area == "קריות"
    assert len(card.services) == 2
    assert card.services[0].name == "התקנת מזגנים"
    assert card.source_urls == ["https://example.com"]


@pytest.mark.asyncio
async def test_extractor_raises_on_empty_texts() -> None:
    """ExtractionError raised when no scraped text is available."""
    with pytest.raises(ExtractionError, match="No scraped text"):
        await extract_client_card({}, [])


# ---------------------------------------------------------------------------
# 3. Call script tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_script_returns_string() -> None:
    """Patch OpenAI; verify a non-empty string containing Hebrew is returned."""

    mock_message = MagicMock()
    mock_message.content = SAMPLE_CALL_SCRIPT

    mock_choice = MagicMock()
    mock_choice.message = mock_message

    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]

    mock_chat = AsyncMock()
    mock_chat.completions.create = AsyncMock(return_value=mock_completion)

    mock_openai = MagicMock()
    mock_openai.chat = mock_chat

    card = _make_client_card()

    with patch("src.call_script.AsyncOpenAI", return_value=mock_openai):
        script = await generate_call_script(card)

    assert isinstance(script, str)
    assert len(script) > 50
    # Must contain some Hebrew characters
    assert any("\u0590" <= ch <= "\u05ff" for ch in script), "Script must contain Hebrew text"
    assert "שלום" in script or "פתיחה" in script


# ---------------------------------------------------------------------------
# 4. CRM tests
# ---------------------------------------------------------------------------


def test_crm_insert_and_list(tmp_path: Path) -> None:
    """Use tmp_path fixture; verify insert then list_records returns the record."""
    db_file = str(tmp_path / "test_crm.db")

    crm_module.init_db(db_file)

    card = _make_client_card()
    record = CRMRecord(
        client_card=card,
        call_script=SAMPLE_CALL_SCRIPT,
        notified=False,
    )

    new_id = crm_module.insert_record(db_file, record)
    assert isinstance(new_id, int)
    assert new_id >= 1
    assert record.id == new_id

    records = crm_module.list_records(db_file)
    assert len(records) == 1

    retrieved = records[0]
    assert retrieved.id == new_id
    assert retrieved.client_card.business_name == "חברת קור"
    assert retrieved.call_script == SAMPLE_CALL_SCRIPT
    assert retrieved.notified is False


def test_crm_list_multiple_records_newest_first(tmp_path: Path) -> None:
    """Multiple inserts are returned newest first."""
    db_file = str(tmp_path / "multi.db")
    crm_module.init_db(db_file)

    for i in range(3):
        card = _make_client_card(business_name=f"עסק {i}")
        record = CRMRecord(client_card=card, call_script=f"script {i}")
        crm_module.insert_record(db_file, record)

    records = crm_module.list_records(db_file)
    assert len(records) == 3
    # IDs should be descending (newest first)
    ids = [r.id for r in records]
    assert ids == sorted(ids, reverse=True)


# ---------------------------------------------------------------------------
# 5. Full pipeline dry-run integration test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline_dry_run(tmp_path: Path) -> None:
    """
    Patch httpx + OpenAI + resend.
    Call _run_pipeline with dry_run=True.
    Verify no exceptions are raised and no CRM file is written.
    """
    from main import _run_pipeline

    db_file = str(tmp_path / "pipeline_test.db")

    # Mock httpx response
    mock_response = MagicMock()
    mock_response.text = SAMPLE_HTML
    mock_response.raise_for_status = MagicMock()

    # Mock OpenAI for extractor
    mock_message_extract = MagicMock()
    mock_message_extract.content = json.dumps(SAMPLE_EXTRACTION_JSON)
    mock_choice_extract = MagicMock()
    mock_choice_extract.message = mock_message_extract
    mock_completion_extract = MagicMock()
    mock_completion_extract.choices = [mock_choice_extract]

    # Mock OpenAI for call script
    mock_message_script = MagicMock()
    mock_message_script.content = SAMPLE_CALL_SCRIPT
    mock_choice_script = MagicMock()
    mock_choice_script.message = mock_message_script
    mock_completion_script = MagicMock()
    mock_completion_script.choices = [mock_choice_script]

    call_count = {"n": 0}

    async def _mock_create(**kwargs: Any) -> Any:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return mock_completion_extract
        return mock_completion_script

    mock_chat = AsyncMock()
    mock_chat.completions.create = _mock_create

    mock_openai_instance = MagicMock()
    mock_openai_instance.chat = mock_chat

    with (
        patch("src.scraper.httpx.AsyncClient") as mock_client_cls,
        patch("src.extractor.AsyncOpenAI", return_value=mock_openai_instance),
        patch("src.call_script.AsyncOpenAI", return_value=mock_openai_instance),
    ):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        # Should not raise
        await _run_pipeline(
            urls=["https://example.com"],
            business=None,
            phone=None,
            email="test@example.com",
            db_path=db_file,
            dry_run=True,
        )

    # dry_run=True means no CRM file should be created
    assert not Path(db_file).exists(), "CRM DB should NOT be created in dry-run mode"


# ---------------------------------------------------------------------------
# 6. Additional scraper tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scraper_http_error_returns_empty_string() -> None:
    """If a URL returns an HTTP error, that URL maps to an empty string (no crash)."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock(side_effect=Exception("404 Not Found"))

    with patch("src.scraper.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await scrape_urls(["https://broken.example.com"])

    assert "https://broken.example.com" in result
    assert result["https://broken.example.com"] == ""


@pytest.mark.asyncio
async def test_scraper_timeout_returns_empty_string() -> None:
    """If the HTTP request times out, that URL maps to an empty string (no crash)."""
    import httpx

    with patch("src.scraper.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client_cls.return_value = mock_client

        result = await scrape_urls(["https://slow.example.com"])

    assert result["https://slow.example.com"] == ""


@pytest.mark.asyncio
async def test_scraper_multiple_urls() -> None:
    """scrape_urls handles multiple URLs and returns a result for each."""
    mock_response = MagicMock()
    mock_response.text = SAMPLE_HTML
    mock_response.raise_for_status = MagicMock()

    with patch("src.scraper.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await scrape_urls(["https://a.com", "https://b.com"])

    assert set(result.keys()) == {"https://a.com", "https://b.com"}
    assert "שרות מיזוג אוויר מקצועי" in result["https://a.com"]
    assert "שרות מיזוג אוויר מקצועי" in result["https://b.com"]


def test_extract_text_empty_html() -> None:
    """_extract_text on empty string returns empty string without crashing."""
    assert _extract_text("") == ""


# ---------------------------------------------------------------------------
# 7. Additional extractor tests
# ---------------------------------------------------------------------------


def test_build_user_message_truncates_per_page() -> None:
    """Each page contributes at most _MAX_PER_PAGE_CHARS characters."""
    from src.extractor import _MAX_PER_PAGE_CHARS

    long_text = "א" * (_MAX_PER_PAGE_CHARS * 2)
    msg = _build_user_message({"https://example.com": long_text})
    # The chunk from this URL must not exceed the per-page cap
    assert len(msg) <= _MAX_PER_PAGE_CHARS + 100  # +100 for the URL header


def test_build_user_message_skips_blank_pages() -> None:
    """Pages with only whitespace are excluded from the user message."""
    msg = _build_user_message({"https://blank.com": "   \n\t  ", "https://ok.com": "real text"})
    assert "https://blank.com" not in msg
    assert "real text" in msg


@pytest.mark.asyncio
async def test_extractor_raises_on_openai_error() -> None:
    """ExtractionError is raised when the OpenAI call itself throws."""
    mock_chat = AsyncMock()
    mock_chat.completions.create = AsyncMock(side_effect=Exception("API unavailable"))

    mock_openai = MagicMock()
    mock_openai.chat = mock_chat

    texts = {"https://example.com": "some text about the business"}

    with patch("src.extractor.AsyncOpenAI", return_value=mock_openai):
        with pytest.raises(ExtractionError, match="OpenAI API call failed"):
            await extract_client_card(texts, ["https://example.com"])


@pytest.mark.asyncio
async def test_extractor_raises_on_invalid_json() -> None:
    """ExtractionError is raised when GPT-4o returns non-JSON content."""
    mock_message = MagicMock()
    mock_message.content = "this is not json at all"

    mock_choice = MagicMock()
    mock_choice.message = mock_message

    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]

    mock_chat = AsyncMock()
    mock_chat.completions.create = AsyncMock(return_value=mock_completion)

    mock_openai = MagicMock()
    mock_openai.chat = mock_chat

    texts = {"https://example.com": "some text"}

    with patch("src.extractor.AsyncOpenAI", return_value=mock_openai):
        with pytest.raises(ExtractionError, match="invalid JSON"):
            await extract_client_card(texts, ["https://example.com"])


@pytest.mark.asyncio
async def test_extractor_raises_on_all_whitespace_texts() -> None:
    """ExtractionError is raised when all scraped values are blank."""
    with pytest.raises(ExtractionError, match="No scraped text"):
        await extract_client_card(
            {"https://a.com": "   ", "https://b.com": "\n\t"},
            ["https://a.com", "https://b.com"],
        )


@pytest.mark.asyncio
async def test_extractor_fallback_business_name() -> None:
    """ClientCard falls back to 'לא ידוע' when business_name is missing from JSON."""
    data = {**SAMPLE_EXTRACTION_JSON, "business_name": None}

    mock_message = MagicMock()
    mock_message.content = json.dumps(data)
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    mock_chat = AsyncMock()
    mock_chat.completions.create = AsyncMock(return_value=mock_completion)
    mock_openai = MagicMock()
    mock_openai.chat = mock_chat

    with patch("src.extractor.AsyncOpenAI", return_value=mock_openai):
        card = await extract_client_card(
            {"https://example.com": "text"}, ["https://example.com"]
        )

    assert card.business_name == "לא ידוע"


# ---------------------------------------------------------------------------
# 8. Additional call script tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_script_raises_on_openai_error() -> None:
    """CallScriptError is raised when the OpenAI call throws."""
    mock_chat = AsyncMock()
    mock_chat.completions.create = AsyncMock(side_effect=Exception("quota exceeded"))
    mock_openai = MagicMock()
    mock_openai.chat = mock_chat

    with patch("src.call_script.AsyncOpenAI", return_value=mock_openai):
        with pytest.raises(CallScriptError, match="OpenAI API call failed"):
            await generate_call_script(_make_client_card())


@pytest.mark.asyncio
async def test_call_script_raises_on_empty_response() -> None:
    """CallScriptError is raised when GPT-4o returns an empty string."""
    mock_message = MagicMock()
    mock_message.content = ""
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]
    mock_chat = AsyncMock()
    mock_chat.completions.create = AsyncMock(return_value=mock_completion)
    mock_openai = MagicMock()
    mock_openai.chat = mock_chat

    with patch("src.call_script.AsyncOpenAI", return_value=mock_openai):
        with pytest.raises(CallScriptError, match="empty call script"):
            await generate_call_script(_make_client_card())


# ---------------------------------------------------------------------------
# 9. URL discovery tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_urls_raises_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """DiscoveryError is raised when TAVILY_API_KEY is not set."""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(DiscoveryError, match="TAVILY_API_KEY"):
        await discover_urls("מזגנים כרמי")


@pytest.mark.asyncio
async def test_discover_urls_deduplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    """URLs returned by both searches appear only once in the result."""
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    duplicate_url = "https://karmi-ac.co.il"
    search_results = {"results": [{"url": duplicate_url}]}

    with patch("src.url_discovery.TavilyClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.search = MagicMock(return_value=search_results)
        mock_cls.return_value = mock_client

        urls = await discover_urls("מזגנים כרמי")

    assert urls.count(duplicate_url) == 1


@pytest.mark.asyncio
async def test_discover_urls_capped_at_five(monkeypatch: pytest.MonkeyPatch) -> None:
    """At most 5 URLs are returned even if both searches return many results."""
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    many_results = {"results": [{"url": f"https://example{i}.com"} for i in range(8)]}

    with patch("src.url_discovery.TavilyClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.search = MagicMock(return_value=many_results)
        mock_cls.return_value = mock_client

        urls = await discover_urls("עסק בדיקה")

    assert len(urls) <= 5


@pytest.mark.asyncio
async def test_discover_urls_returns_empty_when_no_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty list is returned when Tavily finds nothing (no exception)."""
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    with patch("src.url_discovery.TavilyClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.search = MagicMock(return_value={"results": []})
        mock_cls.return_value = mock_client

        urls = await discover_urls("עסק לא קיים")

    assert urls == []


# ---------------------------------------------------------------------------
# 10. Additional notifier tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notifier_returns_false_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """send_call_script returns False (without raising) when RESEND_API_KEY is absent."""
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    result = await send_call_script("test@example.com", "חברת קור", SAMPLE_CALL_SCRIPT)
    assert result is False


@pytest.mark.asyncio
async def test_notifier_returns_false_on_send_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """send_call_script returns False (without raising) when the Resend API throws."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")

    with patch("src.notifier.resend.Emails.send", side_effect=Exception("network error")):
        result = await send_call_script("test@example.com", "חברת קור", SAMPLE_CALL_SCRIPT)

    assert result is False


@pytest.mark.asyncio
async def test_notifier_returns_true_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """send_call_script returns True when Resend succeeds."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")

    with patch("src.notifier.resend.Emails.send", return_value={"id": "email-123"}):
        result = await send_call_script("test@example.com", "חברת קור", SAMPLE_CALL_SCRIPT)

    assert result is True


# ---------------------------------------------------------------------------
# 11. Additional CRM tests
# ---------------------------------------------------------------------------


def test_crm_update_notified(tmp_path: Path) -> None:
    """update_notified persists the notified flag to the database."""
    db_file = str(tmp_path / "update_notified.db")
    crm_module.init_db(db_file)

    record = CRMRecord(client_card=_make_client_card(), call_script=SAMPLE_CALL_SCRIPT, notified=False)
    record_id = crm_module.insert_record(db_file, record)

    assert crm_module.list_records(db_file)[0].notified is False

    crm_module.update_notified(db_file, record_id, notified=True)

    assert crm_module.list_records(db_file)[0].notified is True



    """Calling init_db twice on the same path does not raise."""
    db_file = str(tmp_path / "idempotent.db")
    crm_module.init_db(db_file)
    crm_module.init_db(db_file)  # should not raise


def test_crm_list_records_empty_db(tmp_path: Path) -> None:
    """list_records returns an empty list when the table has no rows."""
    db_file = str(tmp_path / "empty.db")
    crm_module.init_db(db_file)
    assert crm_module.list_records(db_file) == []


def test_crm_notified_true_roundtrip(tmp_path: Path) -> None:
    """notified=True is persisted and correctly deserialised."""
    db_file = str(tmp_path / "notified.db")
    crm_module.init_db(db_file)

    record = CRMRecord(
        client_card=_make_client_card(),
        call_script=SAMPLE_CALL_SCRIPT,
        notified=True,
    )
    crm_module.insert_record(db_file, record)

    retrieved = crm_module.list_records(db_file)[0]
    assert retrieved.notified is True


def test_crm_list_records_skips_corrupt_row(tmp_path: Path) -> None:
    """A row with invalid JSON in client_card_json is silently skipped."""
    import sqlite3

    db_file = str(tmp_path / "corrupt.db")
    crm_module.init_db(db_file)

    # Insert a valid record first
    record = CRMRecord(client_card=_make_client_card(), call_script="script")
    crm_module.insert_record(db_file, record)

    # Manually corrupt a second row
    conn = sqlite3.connect(db_file)
    conn.execute(
        "INSERT INTO onboarding_records "
        "(business_name, client_card_json, call_script, notified, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("corrupt", "not valid json {{{{", "script", 0, "2024-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    records = crm_module.list_records(db_file)
    # Only the valid record is returned; corrupt row is skipped without crashing
    assert len(records) == 1
    assert records[0].client_card.business_name == "חברת קור"
