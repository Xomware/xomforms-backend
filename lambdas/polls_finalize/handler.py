"""
POST /polls/finalize -- Creator picks the winning time (authed).

Does three things as one action, because they're one decision: records the
chosen slot, closes the form to further responses, and tells everyone who
answered what was picked.

Notification is best-effort per recipient. A respondent whose address bounces
must not prevent the form from being finalized -- the decision is the
important part, and it's already recorded by the time any mail is attempted.
"""

from pydantic import ValidationError as PydanticValidationError

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError
from lambdas.common.utility_helpers import (
    success_response,
    parse_body,
    get_caller_email,
    get_iso_timestamp,
)
from lambdas.common.models import FinalizePollRequest
from lambdas.common.polls_dynamo import get_poll_for_creator, update_poll_attributes
from lambdas.common.responses_dynamo import get_responses_for_poll
from lambdas.common.calendar_helpers import block_to_utc, event_window
from lambdas.common.email_helpers import send_confirmation, timezone_label

log = get_logger(__file__)

HANDLER = "polls_finalize"


def _when_label(poll: dict) -> str:
    """
    "Sunday, Aug 9 at 6:30 PM (New York (EDT))" -- the organizer's wall clock.

    Deliberately the ORGANIZER's timezone, not each recipient's: the email is
    one rendering sent to everyone, and a per-recipient local time would need
    a timezone we never asked them for.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    block_id = poll["finalBlockId"]
    tz_name = poll.get("timezone") or "UTC"
    naive = datetime.strptime(block_id, "%Y-%m-%dT%H:%M")
    try:
        local = naive.replace(tzinfo=ZoneInfo(tz_name))
    except Exception:
        local = naive

    day = local.strftime("%A, %b %-d")
    clock = local.strftime("%-I:%M %p")
    return f"{day} at {clock} ({timezone_label(tz_name, local.date())})"


def _recipients(poll_id: str) -> list[dict]:
    """
    Everyone who answered and left an address, deduped.

    A guest who answered twice from one browser is one person; mailing them
    twice for one decision would look broken.
    """
    seen: set[str] = set()
    out: list[dict] = []
    for item in get_responses_for_poll(poll_id):
        key = item.get("respondentKey", "")
        email = item.get("email") or (None if key.startswith("guest#") else key)
        if not email:
            continue
        email = email.strip().lower()
        if email in seen:
            continue
        seen.add(email)
        out.append({"email": email, "name": item.get("displayName")})
    return out


@handle_errors(HANDLER)
def handler(event, context):
    email = get_caller_email(event)

    body = parse_body(event)
    try:
        req = FinalizePollRequest(**body)
    except PydanticValidationError as err:
        raise ValidationError(message=str(err), function="handler")

    # 404 if it doesn't exist, 403 if the caller isn't the creator.
    poll = get_poll_for_creator(req.pollId, email, function="handler")

    if not poll.get("timezone"):
        raise ValidationError(
            message="only a scheduler form can be finalized", function="handler", field="pollId"
        )

    # Resolve it now so a malformed slot fails before anything is written.
    start_utc = block_to_utc(req.blockId, poll.get("timezone") or "UTC")
    now = get_iso_timestamp()

    updated = update_poll_attributes(
        req.pollId,
        {
            "finalBlockId": req.blockId,
            "finalStartUtc": start_utc.isoformat().replace("+00:00", "Z"),
            "finalizedAt": now,
            # Finalizing closes the form: the decision is made, so further
            # availability can't change anything.
            "closeAt": now,
        },
    )

    sent, failed = 0, 0
    if req.notify:
        when = _when_label(updated)
        sender = (poll.get("creatorEmail") or email).split("@")[0]
        for person in _recipients(req.pollId):
            try:
                send_confirmation(
                    to_email=person["email"],
                    recipient_name=person["name"],
                    sender_name=sender,
                    poll=updated,
                    when_label=when,
                )
                sent += 1
            except Exception as err:
                # Recorded, not raised: the form is already finalized, and one
                # bad address must not undo that or block the rest.
                failed += 1
                log.error(f"Confirmation failed for {person['email']} on {req.pollId}: {err}")

    start, end = event_window(updated)
    log.info(f"Poll finalized: {req.pollId} at {req.blockId} ({sent} notified, {failed} failed)")

    return success_response(
        {
            "pollId": req.pollId,
            "finalBlockId": req.blockId,
            "startUtc": start.isoformat().replace("+00:00", "Z"),
            "endUtc": end.isoformat().replace("+00:00", "Z"),
            "notified": sent,
            "failed": failed,
        }
    )
