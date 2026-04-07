"""Pydantic v2 schemas for the Zap onboarding pipeline."""

from datetime import datetime, timezone
from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


class ServiceCategory(BaseModel):
    """A single service offered by the business."""

    name: str
    description: str


class ClientCard(BaseModel):
    """Structured representation of a business client."""

    business_name: str
    owner_name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    area: str = ""
    services: list[ServiceCategory] = []
    source_urls: list[str] = []
    extracted_at: datetime = Field(default_factory=_utcnow)


class CRMRecord(BaseModel):
    """A full onboarding record stored in the CRM database."""

    id: int | None = None
    client_card: ClientCard
    call_script: str
    notified: bool = False
    created_at: datetime = Field(default_factory=_utcnow)
