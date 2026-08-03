"""
POST /responses/claim -- Link this browser's guest responses to the account
that just signed in (authed).

A guest submits under "guest#<uuid>", where the uuid lives only in that
browser's localStorage. On sign-in the client posts that id and the rows are
re-keyed onto the caller's email, so the forms they filled out as a guest show
up in My Forms.

Bounded by GUEST_CLAIM_WINDOW_HOURS. A guestId identifies a BROWSER, not a
person: on a shared laptop an unbounded claim would quietly absorb whoever
used it before you. The handler reports exactly what it linked and what it
skipped so the UI can tell the user rather than doing this invisibly.
"""

from datetime import datetime, timedelta, timezone

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError
from lambdas.common.utility_helpers import success_response, parse_body, get_caller_email
from lambdas.common.constants import GUEST_CLAIM_WINDOW_HOURS
from lambdas.common.responses_dynamo import claim_guest_responses

log = get_logger(__file__)

HANDLER = "responses_claim"


@handle_errors(HANDLER)
def handler(event, context):
    email = get_caller_email(event)

    body = parse_body(event)
    guest_id = (body.get("guestId") or "").strip()
    if not guest_id:
        raise ValidationError(message="guestId is required", function="handler", field="guestId")

    # Reject the sentinel prefix outright: a caller passing "guest#..." as the
    # id would otherwise produce "guest#guest#..." and silently claim nothing.
    if guest_id.startswith("guest#"):
        guest_id = guest_id[len("guest#") :]

    since = datetime.now(timezone.utc) - timedelta(hours=GUEST_CLAIM_WINDOW_HOURS)
    since_iso = since.isoformat().replace("+00:00", "Z")

    result = claim_guest_responses(f"guest#{guest_id}", email, since_iso)

    log.info(
        f"Claim by {email}: claimed={len(result['claimed'])} "
        f"existing={len(result['skippedExisting'])} stale={len(result['skippedStale'])}"
    )

    return success_response(
        {
            "claimed": result["claimed"],
            "skippedExisting": result["skippedExisting"],
            "skippedStale": result["skippedStale"],
            "windowHours": GUEST_CLAIM_WINDOW_HOURS,
        }
    )
