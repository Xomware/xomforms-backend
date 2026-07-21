"""
XOMFORMS Timezone / Grid Helpers
=================================
Generates the canonical availability grid for a poll and converts between
blockIds (stable, timezone-naive wall-clock identifiers) and UTC instants
(the canonical storage format for availability, per docs/features/xomforms/PLAN.md).

Correctness invariant (the #1 risk called out in the plan): every block's
wall-clock local datetime is localized INDEPENDENTLY against the poll's IANA
timezone -- never derived by adding a timedelta to a running UTC value. This
is what keeps blocks correct across a DST transition inside the poll's date
range; naive "add N minutes in UTC" arithmetic would silently drift by an
hour on the far side of a spring-forward/fall-back boundary.

DST edge-case handling (documented, accepted approximation for MVP):
- Spring-forward "gap" hour (a local wall-clock time that never happens,
  e.g. 2026-03-08 02:30 in America/New_York): Python's zoneinfo resolves
  this deterministically via PEP 495 `fold=0` (pre-transition UTC offset).
  The resulting instant is well-defined and self-consistent, even though
  the wall-clock label technically didn't happen.
- Fall-back "ambiguous" hour (a local wall-clock time that happens twice,
  e.g. 2026-11-01 01:30 in America/New_York): we always resolve to the
  FIRST occurrence (`fold=0`). Real users are very unlikely to be
  scheduling availability at 1-3 AM; this is an intentional MVP
  simplification, not a general-purpose calendar library.
"""

from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from lambdas.common.errors import ValidationError

BLOCK_ID_FORMAT = "%Y-%m-%dT%H:%M"


def _parse_date(value: str, field: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ValidationError(message=f"Invalid date '{value}'", field=field)


def _get_zoneinfo(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValidationError(message=f"Unknown timezone '{tz_name}'", field="timezone")


def _localize(naive_dt: datetime, tz: ZoneInfo) -> str:
    """
    Attach the poll's timezone to a naive wall-clock datetime (fold=0 by
    default -- see module docstring for DST edge-case handling) and return
    the canonical UTC instant as an ISO 8601 string.
    """
    localized = naive_dt.replace(tzinfo=tz)
    return localized.isoformat()


def generate_grid(poll_config: dict) -> list[dict]:
    """
    Enumerate every candidate availability block for a poll.

    Args:
        poll_config: dict with startDate, endDate (YYYY-MM-DD, inclusive),
            dayStartMinute, dayEndMinute (minutes since local midnight,
            dayEndMinute exclusive), granularityMinutes, timezone (IANA name).

    Returns:
        List of {"blockId": str, "utcInstant": str} dicts, in chronological
        (UTC) order. blockId is a stable "YYYY-MM-DDTHH:MM" wall-clock label
        in the poll's own timezone -- used as the canonical key for
        submissions and overlap tally.
    """
    start = _parse_date(poll_config["startDate"], "startDate")
    end = _parse_date(poll_config["endDate"], "endDate")
    if end < start:
        raise ValidationError(message="endDate must be on or after startDate", field="endDate")

    # int(...) normalizes decimal.Decimal -- boto3's DynamoDB *resource* API
    # returns numeric attributes as Decimal, not int (same reason
    # XomformsJSONEncoder exists), and timedelta(minutes=...) below rejects
    # Decimal outright. A poll fetched via get_poll() would otherwise crash
    # generate_grid() -- caught via live end-to-end verification 2026-07-21
    # (see tests/test_timezone.py::TestGenerateGridAcceptsDecimalFromDynamoDB).
    day_start = int(poll_config["dayStartMinute"])
    day_end = int(poll_config["dayEndMinute"])
    if day_end <= day_start:
        raise ValidationError(message="dayEndMinute must be after dayStartMinute", field="dayEndMinute")

    granularity = int(poll_config["granularityMinutes"])
    if granularity <= 0:
        raise ValidationError(message="granularityMinutes must be positive", field="granularityMinutes")

    tz = _get_zoneinfo(poll_config["timezone"])

    blocks: list[dict] = []
    current_date = start
    while current_date <= end:
        minute = day_start
        while minute < day_end:
            naive_dt = datetime(
                current_date.year,
                current_date.month,
                current_date.day,
            ) + timedelta(minutes=minute)
            block_id = naive_dt.strftime(BLOCK_ID_FORMAT)
            utc_instant = _localize(naive_dt, tz)
            blocks.append({"blockId": block_id, "utcInstant": utc_instant})
            minute += granularity
        current_date += timedelta(days=1)

    # Grid is constructed in local wall-clock order, which is monotonic
    # per-day; sort defensively by UTC instant so callers can always rely
    # on chronological ordering even across a DST boundary day.
    blocks.sort(key=lambda b: b["utcInstant"])
    return blocks


def block_id_to_utc(poll_config: dict, block_id: str) -> str:
    """
    Recompute the canonical UTC instant for a given blockId, using the
    poll's timezone. Used to validate that a submitted blockId belongs to
    the poll's grid and to avoid trusting a client-supplied UTC instant.
    """
    try:
        naive_dt = datetime.strptime(block_id, BLOCK_ID_FORMAT)
    except (ValueError, TypeError):
        raise ValidationError(message=f"Malformed blockId '{block_id}'", field="blockId")

    tz = _get_zoneinfo(poll_config["timezone"])
    return _localize(naive_dt, tz)
