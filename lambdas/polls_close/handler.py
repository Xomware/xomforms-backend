"""
POST /polls/close -- Creator closes (or reopens) one of their forms (authed).

Non-destructive counterpart to polls_delete: closing stops new responses while
keeping every existing one, so the results view stays intact. Pass
{"reopen": true} to clear closeAt and accept responses again.

Closing sets closeAt to NOW rather than to a future time -- the scheduled-close
case is already covered by the closeAt field on create.
"""

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError
from lambdas.common.utility_helpers import (
    success_response,
    parse_body,
    get_caller_email,
    get_iso_timestamp,
)
from lambdas.common.polls_dynamo import get_poll_for_creator, set_poll_close_at

log = get_logger(__file__)

HANDLER = "polls_close"


@handle_errors(HANDLER)
def handler(event, context):
    # Identity first -- fail fast with 401 before touching any data.
    email = get_caller_email(event)

    body = parse_body(event)
    poll_id = body.get("pollId")
    if not poll_id:
        raise ValidationError(message="pollId is required", function="handler", field="pollId")

    reopen = bool(body.get("reopen", False))

    # Raises 404 if it doesn't exist, 403 if the caller isn't the creator.
    poll = get_poll_for_creator(poll_id, email, function="handler")

    close_at = None if reopen else get_iso_timestamp()
    set_poll_close_at(poll_id, close_at)

    log.info(f"Poll {'reopened' if reopen else 'closed'}: {poll_id} by {email}")

    # Echo the updated poll so the dashboard can re-derive status without a
    # follow-up read.
    poll = {**poll}
    if reopen:
        poll.pop("closeAt", None)
    else:
        poll["closeAt"] = close_at

    return success_response(poll)
