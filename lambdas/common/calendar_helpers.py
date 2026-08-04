"""
XOMFORMS Calendar Helpers
=========================
Turns a finalized poll into an iCalendar file and an "add to Google Calendar"
URL.

Both are LINKS rather than an email attachment: SES's Simple send can't carry
one, and switching to raw MIME just to attach a 20-line text file would mean
hand-rolling multipart boundaries for every invite. A hosted .ics URL opens in
Apple Calendar and Outlook exactly like an attachment would.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from lambdas.common.logger import get_logger
from lambdas.common.constants import WEB_BASE_URL

log = get_logger(__file__)

# RFC 5545 wants CRLF line endings, and lines folded at 75 octets.
_CRLF = "\r\n"


def _fold(line: str) -> str:
    """Fold a long line per RFC 5545 -- continuations start with one space."""
    if len(line) <= 75:
        return line
    out, rest = line[:75], line[75:]
    while rest:
        out += _CRLF + " " + rest[:74]
        rest = rest[74:]
    return out


def _escape(text: str) -> str:
    """Escape the characters iCalendar treats as structure."""
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _utc_stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def block_to_utc(block_id: str, tz_name: str) -> datetime:
    """
    "2026-08-09T18:30" in the poll's timezone -> an aware UTC datetime.

    The blockId is WALL-CLOCK time in the organizer's zone, which is the whole
    reason a poll stores a timezone: 6:30 PM means 6:30 PM there, whatever the
    offset happens to be on that date.
    """
    naive = datetime.strptime(block_id, "%Y-%m-%dT%H:%M")
    try:
        local = naive.replace(tzinfo=ZoneInfo(tz_name))
    except Exception:
        log.warning(f"Unknown timezone '{tz_name}' on finalize; treating as UTC")
        local = naive.replace(tzinfo=timezone.utc)
    return local.astimezone(timezone.utc)


def event_window(poll: dict) -> tuple[datetime, datetime]:
    """Start and end instants for the finalized slot, in UTC."""
    block_id = poll["finalBlockId"]
    tz_name = poll.get("timezone") or "UTC"
    start = block_to_utc(block_id, tz_name)
    duration = int(poll.get("eventDurationMinutes") or poll.get("granularityMinutes") or 60)
    return start, start + timedelta(minutes=duration)


def _location_text(poll: dict) -> str:
    if poll.get("locationType") == "virtual":
        return poll.get("locationUrl") or "Online"
    parts = [poll.get("locationName"), poll.get("locationAddress")]
    return ", ".join(p for p in parts if p)


def build_ics(poll: dict) -> str:
    """
    A single-event VCALENDAR for the finalized poll.

    UID is derived from the pollId so re-downloading updates the same calendar
    entry rather than creating a duplicate, and SEQUENCE bumps with each
    finalize so a corrected time supersedes the earlier one in clients that
    honour it.
    """
    start, end = event_window(poll)
    now = datetime.now(timezone.utc)
    finalized = poll.get("finalizedAt")
    # Any later finalize must outrank the previous entry.
    sequence = 0
    if finalized:
        try:
            sequence = int(
                datetime.fromisoformat(str(finalized).replace("Z", "+00:00")).timestamp()
            )
        except ValueError:
            sequence = 0

    description = poll.get("description") or ""
    if poll.get("instructions"):
        description = f"{description}\n\n{poll['instructions']}".strip()
    description = f"{description}\n\n{WEB_BASE_URL}/f/{poll['pollId']}".strip()

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Xomware//Xomforms//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{poll['pollId']}@xomforms.xomware.com",
        f"SEQUENCE:{sequence}",
        f"DTSTAMP:{_utc_stamp(now)}",
        f"DTSTART:{_utc_stamp(start)}",
        f"DTEND:{_utc_stamp(end)}",
        f"SUMMARY:{_escape(poll.get('title') or 'Xomforms event')}",
        f"DESCRIPTION:{_escape(description)}",
    ]
    location = _location_text(poll)
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    lines += ["STATUS:CONFIRMED", "END:VEVENT", "END:VCALENDAR"]

    return _CRLF.join(_fold(line) for line in lines) + _CRLF


def google_calendar_url(poll: dict) -> str:
    """Google's template URL -- the one-click option for the majority of users."""
    from urllib.parse import quote_plus

    start, end = event_window(poll)
    dates = f"{_utc_stamp(start)}/{_utc_stamp(end)}"
    params = [
        "action=TEMPLATE",
        f"text={quote_plus(poll.get('title') or 'Xomforms event')}",
        f"dates={dates}",
        f"details={quote_plus((poll.get('description') or '') + f'{chr(10)}{WEB_BASE_URL}/f/' + poll['pollId'])}",
    ]
    location = _location_text(poll)
    if location:
        params.append(f"location={quote_plus(location)}")
    return "https://calendar.google.com/calendar/render?" + "&".join(params)


def ics_url(poll_id: str) -> str:
    """Public download link for the .ics, safe to put in an email."""
    from lambdas.common.constants import API_BASE_URL

    return f"{API_BASE_URL}/polls/ics?pollId={poll_id}"
