# ZapProject

 **AI-powered onboarding automation for Zap producers** 

 from a client URL to a personalised Hebrew call script in under 60 seconds.
 
 In this README you'll find:
 - An overview of the system design and technology choices
 - A more technical part with quick start, usage examples and project structure


## How It Works

```
┌─────────────────────────────────────────────────────────────────────┐
│                     ZapProject Pipeline                             │
└─────────────────────────────────────────────────────────────────────┘

  Input
  ─────
  --url https://...           ──┐
  --url https://d.co.il/...   ──┤  (Mode 1: explicit URLs)
                                │
  --business "מזגנים כרמי"   ──┤  (Mode 2: Tavily discovery)
  --phone "050-..."           ──┘
           │
           ▼
  ┌─────────────────┐
  │  url_discovery  │  Tavily search API — finds website + d.co.il listing
  └────────┬────────┘  (skipped when --url is provided directly)
           |
           │  list[str]  (up to 5 URLs)
           ▼
  ┌─────────────────┐
  │    scraper      │  httpx (async, 15 s timeout) + BeautifulSoup4
  └────────┬────────┘  strips nav/footer/script; returns visible text
           |
           │  dict[url → cleaned_text]
           ▼
  ┌─────────────────┐
  │   extractor     │  GPT-4o (JSON mode, temp=0)
  └────────┬────────┘  extraction.txt system prompt → ClientCard (Pydantic v2)
           |
           │  ClientCard
           ├──────────────────────────┐
           ▼                          ▼
  ┌─────────────────┐       ┌──────────────────┐
  │  call_script    │       │      crm         │
  └────────┬────────┘       └────────┬─────────┘
  GPT-4o (temp=0.7)         SQLite INSERT
  call_script.txt prompt    onboarding_records
  → Hebrew call script               │
           │                         │
           └──────────┬──────────────┘
                      ▼
             ┌─────────────────┐
             │    notifier     │  Resend REST API
             └─────────────────┘  emails call script to producer
```

## Design Choices

| Component | Choice | Why                                                                                                                           |
|-----------|--------|-------------------------------------------------------------------------------------------------------------------------------|
| **AI extraction** | GPT-4o, JSON mode, `temp=0` | Structured output without regex; deterministic results; Pydantic catches any malformed response immediately                   |
| **URL discovery** | Tavily API | Built for AI pipelines, returns clean URLs, not raw HTML; two searches run concurrently via `asyncio.gather` to cut latency   |
| **Web scraping** | `httpx` + BeautifulSoup4 | Fully async, handles redirects and malformed HTML; junk tags stripped before GPT-4o sees the text; no headless browser needed |
| **Data validation** | Pydantic v2 | Validates GPT-4o output at the AI/app boundary — bad data raises immediately, never reaches the CRM or email                  |
| **CRM storage** | SQLite | Zero infrastructure; a single `.db` file is sufficient for the expected volume; swap to Postgres later with minimal changes   |
| **Email delivery** | Resend SDK | Simple REST API, no SMTP config; email failure is non-fatal — the pipeline returns a boolean and continues                    |
| **CLI** | Typer + Rich | Typed argument parsing from annotations; coloured terminal output with no impact on pipeline logic                            |
| **Prompts** | External `.txt` files | Prompt changes are their own reviewed diff; non-engineers can tune extraction or script style without touching Python         |

---

## Quick Start

### Prerequisites

| Tool | Minimum version |
|------|----------------|
| Python | 3.11 |
| pip | 23+ |

### 1. Clone the repository

```bash
git clone https://github.com/shayro9/ZapProject.git
cd ZapProject
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in your keys:

```env
OPENAI_API_KEY=sk-...
TAVILY_API_KEY=tvly-...
RESEND_API_KEY=re_...
CRM_DB_PATH=crm.db
```

### 4. Run the pipeline

```bash
python main.py run \
  --url https://example-ac.co.il \
  --url https://www.d.co.il/business/example \
  --email producer@zap.co.il
```

---

## Usage

```bash
python main.py run \
  --url https://karmi-ac.co.il \
  --url https://www.d.co.il/business/karmi-mazganim \
  --email karmi@karmi-ac.co.il
```

**Example terminal output:**

```
Scraping 2 URL(s)…
Scraped 2/2 pages successfully.
Extracting client data with GPT-4o…
╭─ Client Card — מזגנים כרמי ──────────────────────────────────────────╮
│ {                                                                     │
│   "business_name": "מזגנים כרמי",                                    │
│   "owner_name": "יוסי כרמי",                                         │
│   "phone": "050-1234567",                                             │
│   "email": "karmi@karmi-ac.co.il",                                    │
│   "address": "רחוב הציונות 14, קריית ביאליק",                       │
│   "area": "קריות",                                                    │
│   "services": [                                                       │
│     { "name": "התקנת מזגנים", "description": "התקנת כל סוגי מזגן"}, │
│     { "name": "תיקון מזגנים", "description": "תיקון וטיפול שוטף" },  │
│     { "name": "ניקוי מזגנים", "description": "ניקוי פילטרים ויחידות"}│
│   ],                                                                  │
│   "source_urls": ["https://karmi-ac.co.il", "https://d.co.il/..."],   │
│   "extracted_at": "2024-11-18T09:14:22.031Z"                          │
│ }                                                                     │
╰───────────────────────────────────────────────────────────────────────╯
✍️  Generating Hebrew call script with GPT-4o…
╭─ Call Script (Hebrew) ───────────────────────────────────────────────╮
│ **פתיחה**                                                             │
│ שלום, אני מתקשר מזאפ. האם נוח לך לדבר כמה דקות?                    │
│                                                                       │
│ **הצגה**                                                              │
│ שמי [שם הנציג], ואני מלווה לקוחות חדשים בתחילת הדרך שלהם בפלטפורמה. │
│ קיבלנו את פרטי מזגנים כרמי ואני רוצה לוודא שהכל מוכן עבורך.        │
│                                                                       │
│ **שירותים לאשרור**                                                    │
│ על פי המידע שיש לנו, אתם מציעים: התקנת מזגנים, תיקון מזגנים,        │
│ וניקוי מזגנים — האם זה מדויק?                                        │
│                                                                       │
│ **סיום**                                                              │
│ מצוין! נשלח אליך סיכום בדוא"ל. תודה, יוסי, ושיהיה לך יום טוב!     │
╰───────────────────────────────────────────────────────────────────────╯
✓ CRM record inserted (id=1) in 'crm.db'.
✓ Call script emailed to karmi@karmi-ac.co.il.
╭─ Summary ────────────────────────────────────────────────────────────╮
│ ✅ Onboarding pipeline complete                                        │
│ Business : מזגנים כרמי                                                │
│ Area     : קריות                                                      │
│ Services : 3                                                          │
│ Email    : karmi@karmi-ac.co.il                                       │
│ Dry-run  : False                                                      │
╰───────────────────────────────────────────────────────────────────────╯
```


## Project Structure

```
ZapProject/
│
├── main.py                    # Typer CLI entry point; defines the `run` command
│                              # and the async _run_pipeline() orchestrator
│
├── requirements.txt           # Pinned package versions (pip install -r)
├── pyproject.toml             # pytest configuration (asyncio_mode=auto, testpaths)
├── .env.example               # Template for required environment variables
│
├── src/
│   ├── __init__.py
│   ├── url_discovery.py       # discover_urls(): Tavily dual-search, dedup, cap at 5
│   ├── scraper.py             # scrape_urls(): async httpx + BS4; _extract_text() helper
│   ├── extractor.py           # extract_client_card(): GPT-4o JSON mode → ClientCard
│   ├── call_script.py         # generate_call_script(): GPT-4o → Hebrew call script str
│   ├── crm.py                 # init_db(), insert_record(), list_records() — SQLite
│   └── notifier.py            # send_call_script(): Resend email delivery
│
├── models/
│   ├── __init__.py
│   └── schemas.py             # Pydantic v2: ServiceCategory, ClientCard, CRMRecord
│
├── prompts/
│   ├── extraction.txt         # GPT-4o system prompt: extract JSON client data from HTML
│   └── call_script.txt        # GPT-4o system prompt: generate Hebrew onboarding script
│
└── tests/
    └── test_pipeline.py       # 8 pytest-asyncio tests; all I/O is patched with unittest.mock
```

---

## CRM Schema

The SQLite database contains a single table, `onboarding_records`, created automatically on first run by `crm.init_db()`.

```sql
CREATE TABLE IF NOT EXISTS onboarding_records (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    business_name    TEXT    NOT NULL,
    client_card_json TEXT    NOT NULL,
    call_script      TEXT    NOT NULL,
    notified         INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL
);
```

| Column | Type | Description |
|--------|------|-------------|
| `id` | `INTEGER` | Auto-incrementing primary key. Returned by `insert_record()` and written back to the `CRMRecord.id` field. |
| `business_name` | `TEXT` | Denormalised from the `ClientCard` for fast lookup without parsing JSON. |
| `client_card_json` | `TEXT` | Full `ClientCard` serialised to a compact JSON string via `model_dump_json()`. Deserialised back via `model_validate_json()` in `list_records()`. |
| `call_script` | `TEXT` | The raw Hebrew call script text as returned by GPT-4o. |
| `notified` | `INTEGER` | Boolean stored as `0` (not notified) or `1` (email sent successfully). |
| `created_at` | `TEXT` | ISO 8601 UTC timestamp, e.g. `2024-11-18T09:14:22.031+00:00`. |

---


> **Security note:** Never commit your `.env` file. `.env.example` (no real keys) is safe to commit and acts as documentation for required configuration. `.env` is listed in `.gitignore`.
