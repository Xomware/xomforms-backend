"""
GET /invites/list -- Creator reads invite recipients + delivery status (authed).

Invites are stored on the poll item itself rather than in their own table:
the list is small, bounded by the send cap, and only ever read in the context
of its form.
"""

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError
from lambdas.common.utility_helpers import (
    success_response,
    get_caller_email,
    get_query_params,
)
from lambdas.common.polls_dynamo import get_poll_for_creator

log = get_logger(__file__)

HANDLER = "invites_list"


@handle_errors(HANDLER)
def handler(event, context):
    email = get_caller_email(event)

    params = get_query_params(event)
    poll_id = params.get("pollId")
    if not poll_id:
        raise ValidationError(message="pollId is required", function="handler", field="pollId")

    # Raises 404 if it doesn't exist, 403 if the caller isn't the creator --
    # an invite list is a list of someone's contacts and is creator-only.
    poll = get_poll_for_creator(poll_id, email, function="handler")

    invites = poll.get("invites") or []
    return success_response({"pollId": poll_id, "invites": invites})
