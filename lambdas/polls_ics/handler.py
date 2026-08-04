"""
GET /polls/ics -- Calendar file for a finalized form (public route).

Public because it's the "add to calendar" link inside a notification email,
which has to open straight from a mail client with no session. It discloses
only the title, time, and location the recipient was already told in that
email, and only ever for a form that has actually been decided.
"""

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError, NotFoundError
from lambdas.common.utility_helpers import get_query_params
from lambdas.common.constants import RESPONSE_HEADERS
from lambdas.common.polls_dynamo import get_poll
from lambdas.common.calendar_helpers import build_ics

log = get_logger(__file__)

HANDLER = "polls_ics"


@handle_errors(HANDLER)
def handler(event, context):
    params = get_query_params(event)
    poll_id = params.get("pollId")
    if not poll_id:
        raise ValidationError(message="pollId is required", function="handler", field="pollId")

    poll = get_poll(poll_id)
    if poll is None:
        raise NotFoundError(
            message=f"Poll '{poll_id}' not found", function="handler", resource="poll"
        )
    if not poll.get("finalBlockId"):
        # Nothing to put in a calendar yet -- 404 rather than an empty file,
        # which a client would silently import as a broken event.
        raise NotFoundError(
            message="This form doesn't have a confirmed time yet",
            function="handler",
            resource="finalized_poll",
        )

    body = build_ics(poll)
    filename = f"{(poll.get('title') or 'event').replace(' ', '-')[:40]}.ics"

    log.info(f"ICS served for poll={poll_id}")
    return {
        "statusCode": 200,
        "headers": {
            **RESPONSE_HEADERS,
            "Content-Type": "text/calendar; charset=utf-8",
            # Prompts a download/open rather than rendering as text in-browser.
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
        "body": body,
        "isBase64Encoded": False,
    }
