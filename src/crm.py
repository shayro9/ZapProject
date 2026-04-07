"""SQLite CRM for onboarding records."""

import logging
import sqlite3
from datetime import datetime

from models.schemas import ClientCard, CRMRecord

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS onboarding_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    business_name   TEXT    NOT NULL,
    client_card_json TEXT   NOT NULL,
    call_script     TEXT    NOT NULL,
    notified        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL
);
"""


def _get_connection(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection with row_factory set to Row."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str) -> None:
    """
    Create the onboarding_records table if it does not already exist.

    Columns:
      id               INTEGER PRIMARY KEY AUTOINCREMENT
      business_name    TEXT
      client_card_json TEXT   (serialised ClientCard JSON)
      call_script      TEXT
      notified         INTEGER (0 = False, 1 = True)
      created_at       TEXT   (ISO-8601 UTC timestamp)
    """
    logger.info("Initialising CRM database at '%s'", db_path)
    with _get_connection(db_path) as conn:
        conn.execute(_CREATE_TABLE_SQL)
        conn.commit()
    logger.debug("onboarding_records table ready.")


def insert_record(db_path: str, record: CRMRecord) -> int:
    """
    Insert a CRMRecord into the database.

    The ClientCard is stored as a compact JSON string in client_card_json.
    Returns the new row id (updates record.id in-place).
    """
    client_card_json = record.client_card.model_dump_json()
    created_at = record.created_at.isoformat()
    notified_int = 1 if record.notified else 0

    with _get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO onboarding_records
                (business_name, client_card_json, call_script, notified, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                record.client_card.business_name,
                client_card_json,
                record.call_script,
                notified_int,
                created_at,
            ),
        )
        conn.commit()
        new_id: int = cursor.lastrowid  # type: ignore[assignment]

    logger.info(
        "Inserted CRM record id=%d for '%s'",
        new_id,
        record.client_card.business_name,
    )
    record.id = new_id
    return new_id


def update_notified(db_path: str, record_id: int, notified: bool) -> None:
    """
    Persist the notified status for a given record id.

    Called after a successful (or failed) email delivery to keep the
    CRM in sync with actual notification state.
    """
    notified_int = 1 if notified else 0
    with _get_connection(db_path) as conn:
        conn.execute(
            "UPDATE onboarding_records SET notified = ? WHERE id = ?",
            (notified_int, record_id),
        )
        conn.commit()
    logger.info("Updated notified=%s for record id=%d", notified, record_id)


def list_records(db_path: str, limit: int = 0) -> list[CRMRecord]:
    """
    Return CRM records as CRMRecord objects, ordered newest first.

    Args:
        limit: Maximum number of records to return. 0 means no limit.
    """
    records: list[CRMRecord] = []
    query = "SELECT * FROM onboarding_records ORDER BY id DESC"
    params: tuple = ()
    if limit > 0:
        query += " LIMIT ?"
        params = (limit,)

    with _get_connection(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    for row in rows:
        try:
            card = ClientCard.model_validate_json(row["client_card_json"])
            crm_record = CRMRecord(
                id=row["id"],
                client_card=card,
                call_script=row["call_script"],
                notified=bool(row["notified"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            records.append(crm_record)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to deserialise record id=%s: %s", row["id"], exc)

    return records
