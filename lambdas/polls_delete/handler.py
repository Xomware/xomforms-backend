"""
POST /polls/delete -- Creator permanently deletes one of their forms (authed).

Destructive and irreversible: the poll row AND every response row for it are
removed. Responses go first so a mid-way failure leaves the poll intact and the
operation safely retryable -- deleting the poll first would strand its responses
with no way to find them again.
"""

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError
from lambdas.common.utility_helpers import success_response, parse_body, get_caller_email
from lambdas.common.polls_dynamo import get_poll_for_creator, delete_poll
from lambdas.common.responses_dynamo import delete_responses_for_poll

log = get_logger(__file__)

HANDLER = "polls_delete"


@handle_errors(HANDLER)
def handler(event, context):
    # Identity first -- fail fast with 401 before touching any data.
    email = get_caller_email(event)

    body = parse_body(event)
    poll_id = body.get("pollId")
    if not poll_id:
        raise ValidationError(message="pollId is required", function="handler", field="pollId")

    # Raises 404 if it doesn't exist, 403 if the caller isn't the creator.
    get_poll_for_creator(poll_id, email, function="handler")

    deleted_responses = delete_responses_for_poll(poll_id)
    delete_poll(poll_id)

    log.info(f"Poll deleted: {poll_id} by {email} ({deleted_responses} response(s) removed)")

    return success_response({"pollId": poll_id, "deletedResponses": deleted_responses})
