"""
XOMFORMS Email Helpers
======================
Renders the branded form-invite templates and sends them through SES.

Templates live in lambdas/common/email_templates/ and ship inside the shared
lambda layer, so they're read from disk relative to this module rather than
fetched at runtime.

Sending config comes from SSM (written by ses.tf):
    /xomforms/ses/FROM_ADDRESS       -> noreply@xomforms.xomware.com
    /xomforms/ses/CONFIGURATION_SET  -> xomforms-invites
Both are cached per warm container -- they change roughly never, and a
GetParameter per recipient would be pure latency on a bulk send.
"""

import html
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import boto3

from lambdas.common.logger import get_logger
from lambdas.common.errors import XomformsError
from lambdas.common.constants import PRODUCT, WEB_BASE_URL

log = get_logger(__file__)

_TEMPLATE_DIR = Path(__file__).parent / "email_templates"

ses = boto3.client("sesv2", region_name="us-east-1")
ssm = boto3.client("ssm", region_name="us-east-1")


class EmailSendError(XomformsError):
    """Raised when SES rejects a message. 502 -- the failure is downstream."""

    def __init__(self, message: str, handler: str = "email_helpers", function: str = "unknown"):
        super().__init__(message=message, handler=handler, function=function, status=502)


@lru_cache(maxsize=1)
def _load_templates() -> tuple[str, str]:
    html_body = (_TEMPLATE_DIR / "form_invite.html").read_text(encoding="utf-8")
    text_body = (_TEMPLATE_DIR / "form_invite.txt").read_text(encoding="utf-8")
    return html_body, text_body


@lru_cache(maxsize=8)
def _ssm_value(name: str, fallback: str) -> str:
    try:
        res = ssm.get_parameter(Name=name)
        return res["Parameter"]["Value"]
    except Exception as err:
        # A missing parameter must not take the whole send down -- fall back to
        # the documented default and log loudly.
        log.warning(f"SSM lookup failed for {name} ({err}); falling back to {fallback}")
        return fallback


def form_url(poll_id: str) -> str:
    """The public respond URL for a form -- what the invite's CTA points at."""
    return f"{WEB_BASE_URL}/f/{poll_id}"


def _clock(minutes: int) -> str:
    """Minutes since midnight -> "7:00 PM"."""
    total = ((int(minutes) % 1440) + 1440) % 1440
    hour, minute = divmod(total, 60)
    suffix = "AM" if hour < 12 else "PM"
    hour = hour % 12 or 12
    return f"{hour}:{minute:02d} {suffix}"


def _duration_label(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} minutes"
    hours = minutes / 60
    rounded = int(hours) if hours.is_integer() else round(hours, 1)
    return f"{rounded} hour{'' if rounded == 1 else 's'}"


def _detail_rows(poll: dict | None) -> list[tuple[str, str]]:
    """
    The facts a recipient needs before opening the form. Chiefly: they are
    picking a START time, not the hours the event covers -- that is not
    obvious from a grid, and getting it wrong means marking every hour you're
    free instead of the times you could begin.
    """
    if not poll:
        return []

    rows: list[tuple[str, str]] = []
    duration = poll.get("eventDurationMinutes")
    earliest = poll.get("earliestStartMinute")
    latest = poll.get("latestStartMinute")
    start_date = poll.get("startDate")
    end_date = poll.get("endDate")

    if duration:
        rows.append(("Event length", _duration_label(int(duration))))
    if start_date:
        rows.append(("Dates", start_date if start_date == end_date else f"{start_date} to {end_date}"))
    if earliest is not None and latest is not None:
        window = (
            _clock(earliest)
            if earliest == latest
            else f"{_clock(earliest)} - {_clock(latest)}"
        )
        rows.append(("You pick a start time between", window))
    if poll.get("timezone"):
        rows.append(("Organizer's timezone", str(poll["timezone"])))
    return rows


def _details_html(rows: list[tuple[str, str]]) -> str:
    if not rows:
        return ""
    cells = "".join(
        f'<tr>'
        f'<td style="padding:6px 14px 6px 0; font-size:13px; line-height:19px; color:#6a6280; white-space:nowrap;">{html.escape(label)}</td>'
        f'<td style="padding:6px 0; font-size:13px; line-height:19px; font-weight:700; color:#201733;">{html.escape(value)}</td>'
        f'</tr>'
        for label, value in rows
    )
    return (
        '              <table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%; margin:0 0 26px 0; padding:14px 18px; background-color:#f4f0fb; '
        'border:1px solid #e4dcf0; border-radius:10px; font-family:Arial,Helvetica,sans-serif;">'
        f"{cells}</table>\n"
    )


def _details_text(rows: list[tuple[str, str]]) -> str:
    if not rows:
        return ""
    lines = "\n".join(f"  {label}: {value}" for label, value in rows)
    return f"\n{lines}\n"


def _instructions_html(instructions: str | None) -> str:
    """
    The organizer's own note, quoted so it reads as their words rather than
    ours. Escaped like every other creator-supplied value; newlines become
    <br> because the creator typed them into a textarea and expects them kept.
    """
    text = (instructions or "").strip()
    if not text:
        return ""
    body = html.escape(text).replace("\n", "<br />")
    return (
        '              <table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%; margin:0 0 22px 0; font-family:Arial,Helvetica,sans-serif;">'
        '<tr><td style="padding:14px 18px; background-color:#fff6cf; border-left:4px solid #e0a417; '
        'border-radius:10px; font-size:14px; line-height:21px; color:#201733;">'
        f'<strong style="display:block; margin-bottom:4px; font-size:12px; letter-spacing:0.04em; '
        f'text-transform:uppercase; color:#8a6a12;">A note from the organizer</strong>{body}'
        "</td></tr></table>\n"
    )


def _instructions_text(instructions: str | None) -> str:
    text = (instructions or "").strip()
    if not text:
        return ""
    indented = "\n".join(f"  {line}" for line in text.splitlines())
    return f"\nA note from the organizer:\n{indented}\n"


def render_invite(
    recipient_name: str | None,
    sender_name: str,
    form_title: str,
    poll_id: str,
    poll: dict | None = None,
) -> tuple[str, str]:
    """
    Substitute the template placeholders, returning (html, text).

    EVERY user-supplied value is HTML-escaped before it reaches the HTML body.
    A form title is arbitrary creator input and lands inside the markup, so
    without escaping a title containing a tag would inject into the email --
    the template README calls this out explicitly. The text body is not escaped
    (there's no markup to break) but is still substituted from the same values.

    detailsBlock is the one placeholder holding real markup, so it is
    substituted AFTER the escaping pass and is built here from escaped values
    rather than from anything a caller supplies verbatim.
    """
    html_template, text_template = _load_templates()

    safe_recipient = recipient_name.strip() if recipient_name and recipient_name.strip() else "there"
    year = str(datetime.now(timezone.utc).year)
    url = form_url(poll_id)

    raw = {
        "recipientName": safe_recipient,
        "senderName": sender_name,
        "formTitle": form_title,
        "formUrl": url,
        "year": year,
        "logoUrl": f"{WEB_BASE_URL}/assets/xomforms-banner.png",
        "iconUrl": f"{WEB_BASE_URL}/assets/xomforms-icon-256.png",
    }

    html_out = html_template
    text_out = text_template
    for token, value in raw.items():
        html_out = html_out.replace("{{" + token + "}}", html.escape(str(value), quote=True))
        text_out = text_out.replace("{{" + token + "}}", str(value))

    instructions = (poll or {}).get("instructions")
    html_out = html_out.replace("{{instructionsBlock}}", _instructions_html(instructions))
    text_out = text_out.replace("{{instructionsText}}", _instructions_text(instructions))

    rows = _detail_rows(poll)
    html_out = html_out.replace("{{detailsBlock}}", _details_html(rows))
    text_out = text_out.replace("{{detailsText}}", _details_text(rows))

    return html_out, text_out


def send_invite(
    to_email: str,
    recipient_name: str | None,
    sender_name: str,
    form_title: str,
    poll_id: str,
    poll: dict | None = None,
) -> None:
    """Send one invite. Raises EmailSendError so the caller can record status."""
    html_body, text_body = render_invite(recipient_name, sender_name, form_title, poll_id, poll)
    from_address = _ssm_value(f"/{PRODUCT}/ses/FROM_ADDRESS", f"noreply@{PRODUCT}.xomware.com")
    config_set = _ssm_value(f"/{PRODUCT}/ses/CONFIGURATION_SET", f"{PRODUCT}-invites")

    try:
        ses.send_email(
            FromEmailAddress=from_address,
            Destination={"ToAddresses": [to_email]},
            ConfigurationSetName=config_set,
            Content={
                "Simple": {
                    "Subject": {
                        "Data": f"{sender_name} invited you to fill out “{form_title}”",
                        "Charset": "UTF-8",
                    },
                    "Body": {
                        "Html": {"Data": html_body, "Charset": "UTF-8"},
                        "Text": {"Data": text_body, "Charset": "UTF-8"},
                    },
                }
            },
        )
        log.info(f"Invite sent for poll={poll_id}")
    except Exception as err:
        log.error(f"SES send failed for poll={poll_id}: {err}")
        raise EmailSendError(message=str(err), function="send_invite")
