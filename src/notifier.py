"""Email notifier using Resend API."""

import asyncio
import logging
import os
import re

import resend

logger = logging.getLogger(__name__)

# NOTE: onboarding@resend.dev is Resend's shared sandbox domain — fine for
# testing and demos.  For production, replace with a verified custom domain,
# e.g. "Zap Onboarding <onboarding@yourdomain.co.il>".
_FROM_ADDRESS = "Zap Onboarding <onboarding@resend.dev>"

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body  {{ font-family: Arial, sans-serif; background: #f4f4f4; margin: 0; padding: 20px; direction: rtl; }}
    .card {{ background: #fff; border-radius: 8px; padding: 32px; max-width: 620px;
             margin: auto; box-shadow: 0 2px 8px rgba(0,0,0,.08); }}
    h1   {{ color: #e63946; font-size: 20px; margin: 0 0 4px; }}
    .sub {{ color: #666; font-size: 13px; margin: 0 0 28px; }}
    h3   {{ color: #1d3557; font-size: 15px; margin: 22px 0 6px;
             border-bottom: 2px solid #e63946; padding-bottom: 4px; }}
    p    {{ color: #333; line-height: 1.8; margin: 4px 0; }}
    .note {{ color: #999; font-style: italic; }}
    .footer {{ margin-top: 36px; font-size: 11px; color: #bbb; text-align: center; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>זאפ — תסריט שיחת לקוח</h1>
    <p class="sub">לקוח: <strong>{client_name}</strong></p>
    {body}
    <div class="footer">נוצר אוטומטית על ידי ZapProject &middot; לשימוש פנימי בלבד</div>
  </div>
</body>
</html>"""


def _script_to_html(script: str) -> str:
    """Convert the markdown-lite call script to styled HTML paragraphs."""
    parts: list[str] = []
    for line in script.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        # Standalone **Header** line → section heading
        if re.match(r"^\*\*.+\*\*$", stripped):
            heading = stripped[2:-2]
            parts.append(f"<h3>{heading}</h3>")
        else:
            # Inline **bold**
            stripped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", stripped)
            # [stage directions] → muted italic
            stripped = re.sub(r"\[(.+?)\]", r'<span class="note">[\1]</span>', stripped)
            parts.append(f"<p>{stripped}</p>")
    return "\n    ".join(parts)


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
      - html:    styled HTML version of call_script
      - text:    plain-text fallback

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

    # Use replace() instead of .format() — the call script body may contain
    # literal curly braces that would cause a KeyError with str.format().
    html_body = (
        _HTML_TEMPLATE
        .replace("{client_name}", client_name)
        .replace("{body}", _script_to_html(call_script))
    )

    params: resend.Emails.SendParams = {
        "from": _FROM_ADDRESS,
        "to": [to_email],
        "subject": f"תסריט שיחת לקוח – {client_name}",
        "html": html_body,
        "text": call_script,   # plain-text fallback for email clients that need it
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
