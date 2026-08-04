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
from datetime import date, datetime, time as dtime, timezone
from zoneinfo import ZoneInfo
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


def form_url(poll_id: str, invite_token: str | None = None) -> str:
    """
    The public respond URL for a form -- what the invite's CTA points at.

    An invite carries an opaque per-recipient token so the form can prefill
    who it was sent to. The token rather than the address itself: an email in
    the query string ends up in browser history, referrer headers, and any
    analytics on the page, for a value the recipient never chose to publish.
    """
    url = f"{WEB_BASE_URL}/f/{poll_id}"
    return f"{url}?i={invite_token}" if invite_token else url


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


def timezone_label(tz_name: str, on: date | None = None) -> str:
    """
    "America/New_York" -> "New York (EDT)".

    IANA identifiers are how the grid and the backend must talk about zones,
    but they read like file paths in an email. Python ships no CLDR data, so
    there's no "Eastern Time" string to look up -- the abbreviation from
    zoneinfo is the closest correct thing that needs no extra dependency.

    Resolved at the event's start date rather than send time, so a form for
    November doesn't get labelled EDT because the invite went out in August.
    """
    city = tz_name.split("/")[-1].replace("_", " ")
    if tz_name in ("UTC", "Etc/UTC"):
        return "UTC"
    try:
        at = datetime.combine(on or datetime.now(timezone.utc).date(), dtime(12, 0))
        abbr = at.replace(tzinfo=ZoneInfo(tz_name)).strftime("%Z")
    except Exception:
        # Unknown zone: the city still beats printing a path.
        return city
    # Some zones report a numeric offset rather than letters ("+04"), which
    # adds nothing next to the city name.
    if not abbr or abbr[0] in "+-":
        return city
    return f"{city} ({abbr})"


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

    # Location first: whether it's worth attending at all often turns on
    # where it is, before any question of when.
    location_type = poll.get("locationType")
    if location_type == "in_person":
        where = poll.get("locationName") or poll.get("locationAddress")
        if where:
            rows.append(("Where", str(where)))
        address = poll.get("locationAddress")
        if address and poll.get("locationName"):
            rows.append(("Address", str(address)))
    elif location_type == "virtual":
        rows.append(("Where", "Online"))

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
        start = poll.get("startDate")
        on = None
        if start:
            try:
                on = date.fromisoformat(str(start))
            except ValueError:
                on = None
        rows.append(("Times shown in", timezone_label(str(poll["timezone"]), on)))
    return rows


def _details_html(rows: list[tuple[str, str]]) -> str:
    if not rows:
        return ""
    # A hairline rule between rows, but not above the first -- a leading
    # divider reads as a broken table edge in Outlook.
    cells = "".join(
        f'<tr>'
        f'<td style="padding:{"11px" if i else "0"} 16px 11px 0; '
        f'border-top:{"1px solid #e9e2f5" if i else "none"}; '
        f'font-size:13px; line-height:19px; color:#6a6280; white-space:nowrap; vertical-align:top;">'
        f'{html.escape(label)}</td>'
        f'<td style="padding:{"11px" if i else "0"} 0 11px 0; '
        f'border-top:{"1px solid #e9e2f5" if i else "none"}; '
        f'font-size:13px; line-height:19px; font-weight:700; color:#201733; text-align:right;">'
        f'{html.escape(value)}</td>'
        f'</tr>'
        for i, (label, value) in enumerate(rows)
    )
    return (
        '              <table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="width:100%; margin:0 0 26px 0; border-collapse:separate; '
        'font-family:Arial,Helvetica,sans-serif;">'
        '<tr><td style="padding:16px 20px; background-color:#faf8fd; border:1px solid #e9e2f5; '
        'border-radius:12px;">'
        '<span style="display:block; margin-bottom:10px; font-size:11px; letter-spacing:0.08em; '
        'text-transform:uppercase; color:#948ca8;">The details</span>'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;">'
        f"{cells}</table>"
        "</td></tr></table>\n"
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
    invite_token: str | None = None,
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
    url = form_url(poll_id, invite_token)

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
    invite_token: str | None = None,
) -> None:
    """Send one invite. Raises EmailSendError so the caller can record status."""
    html_body, text_body = render_invite(
        recipient_name, sender_name, form_title, poll_id, poll, invite_token
    )
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


# ---------------------------------------------------------------------------
# Finalize notification -- "the time is set"
# ---------------------------------------------------------------------------

def render_confirmation(
    recipient_name: str | None,
    sender_name: str,
    poll: dict,
    when_label: str,
) -> tuple[str, str]:
    """
    The "it's confirmed" email, reusing the invite shell so both messages look
    like they come from the same product.

    Calendar options are LINKS, not an attachment: SES Simple can't carry one,
    and switching to raw MIME for a 20-line text file would mean hand-rolling
    multipart boundaries. A hosted .ics opens in Apple Mail and Outlook exactly
    as an attachment would, and Google gets its own one-click template URL.
    """
    from lambdas.common.calendar_helpers import google_calendar_url, ics_url

    html_template, text_template = _load_templates()

    safe_recipient = recipient_name.strip() if recipient_name and recipient_name.strip() else "there"
    year = str(datetime.now(timezone.utc).year)
    poll_id = poll["pollId"]
    gcal = google_calendar_url(poll)
    ics = ics_url(poll_id)

    raw = {
        "recipientName": safe_recipient,
        "senderName": sender_name,
        "formTitle": poll.get("title") or "your event",
        "formUrl": f"{WEB_BASE_URL}/f/{poll_id}",
        "year": year,
        "logoUrl": f"{WEB_BASE_URL}/assets/xomforms-banner.png",
        "iconUrl": f"{WEB_BASE_URL}/assets/xomforms-icon-256.png",
    }

    html_out = html_template
    text_out = text_template
    for token, value in raw.items():
        html_out = html_out.replace("{{" + token + "}}", html.escape(str(value), quote=True))
        text_out = text_out.replace("{{" + token + "}}", str(value))

    # Swap the invite's ask for the confirmation.
    html_out = html_out.replace(
        "needs your availability", "confirmed the time"
    ).replace(
        "You&#x27;re invited to", "It&#x27;s happening"
    ).replace(
        "is collecting responses and would love your input. It only takes a minute &mdash; tap the button below to open the form and pick the times that work.",
        f"picked a time for this. It&#x27;s now on the calendar &mdash; add it to yours below.",
    ).replace("Pick your times &rarr;", "Add to calendar &rarr;").replace(
        "Pick your times", "Add to calendar"
    ).replace("Takes about a minute &middot; no account needed", "One tap &middot; adds to your calendar")
    html_out = html_out.replace("{{formUrl}}", html.escape(ics, quote=True))
    # The CTA points at the calendar file rather than back at the form.
    html_out = html_out.replace(html.escape(raw["formUrl"], quote=True), html.escape(ics, quote=True), 1)

    confirmed_rows = [("When", when_label)]
    location_rows = _detail_rows(poll)
    confirmed_rows += [r for r in location_rows if r[0] in ("Where", "Address")]
    html_out = html_out.replace("{{instructionsBlock}}", _instructions_html(poll.get("instructions")))
    html_out = html_out.replace("{{detailsBlock}}", _details_html(confirmed_rows))
    html_out = html_out.replace(
        "</table>\n        <!-- /Card -->",
        "</table>\n        <!-- /Card -->",
    )

    text_out = (
        text_out.replace("you've been invited", "the time is set")
        .replace("invited you to fill out a Xomforms poll:", "confirmed the time for:")
        .replace(
            "They're collecting responses and would love your input. It only takes a\nminute — open the form and pick the times that work:",
            "It's confirmed. Add it to your calendar:",
        )
    )
    text_out = text_out.replace("{{instructionsText}}", _instructions_text(poll.get("instructions")))
    text_out = text_out.replace("{{detailsText}}", _details_text(confirmed_rows))
    text_out = text_out.replace(raw["formUrl"], f"{ics}\n\n  Google Calendar: {gcal}\n\n  Form: {raw['formUrl']}")

    return html_out, text_out


def send_confirmation(
    to_email: str,
    recipient_name: str | None,
    sender_name: str,
    poll: dict,
    when_label: str,
) -> None:
    """Send one confirmation. Raises EmailSendError so the caller records status."""
    html_body, text_body = render_confirmation(recipient_name, sender_name, poll, when_label)
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
                        "Data": f"Confirmed: {poll.get('title') or 'your event'} \u2014 {when_label}",
                        "Charset": "UTF-8",
                    },
                    "Body": {
                        "Html": {"Data": html_body, "Charset": "UTF-8"},
                        "Text": {"Data": text_body, "Charset": "UTF-8"},
                    },
                }
            },
        )
        log.info(f"Confirmation sent for poll={poll.get('pollId')}")
    except Exception as err:
        log.error(f"SES confirmation failed for poll={poll.get('pollId')}: {err}")
        raise EmailSendError(message=str(err), function="send_confirmation")
