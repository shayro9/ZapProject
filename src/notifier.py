"""Email notifier using Resend API."""

import asyncio
import logging
import os
from typing import Optional

import resend

logger = logging.getLogger(__name__)

_FROM_ADDRESS = "Zap Onboarding <onboarding@resend.dev>"


async def send_call_script(
    to_email: str,
    client_name: str,
    call_script: str,
) -> bool:
    """
    Send the generated call script to the producer via Resend email API.

    Email details:
      - from:    "Zap Onboarding <onboarding@resend.dev>"
      - to:      [to_email]
      - subject: "תסריט שיחת לקוח – {client_name}"
      - text:    call_script

    Reads RESEND_API_KEY from the environment.
    Returns True on success, False on failure.
    Never raises — notifier errors must not crash the pipeline.
    """
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        logger.error(
            "RESEND_API_KEY is not set — skipping email notification for '%s'.",
            client_name,
        )
        return False

    resend.api_key = api_key

    params: resend.Emails.SendParams = {
        "from": _FROM_ADDRESS,
        "to": [to_email],
        "subject": f"תסריט שיחת לקוח – {client_name}",
        "text": call_script,
    }

    logger.info(
        "Sending call script email to '%s' for client '%s'",
        to_email,
        client_name,
    )

    try:
        result = await asyncio.to_thread(resend.Emails.send, params)
        email_id = result.get("id") if isinstance(result, dict) else getattr(result, "id", None)
        logger.info(
            "Email sent successfully (id=%s) to '%s'",
            email_id,
            to_email,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Failed to send email to '%s' for client '%s': %s",
            to_email,
            client_name,
            exc,
        )
        return False
