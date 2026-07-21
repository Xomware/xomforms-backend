"""
GET /polls/get -- Fetch poll config for the respondent grid (public read).
"""

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError, NotFoundError
from lambdas.common.utility_helpers import success_response, get_query_params
from lambdas.common.polls_dynamo import get_poll

log = get_logger(__file__)

HANDLER = "polls_get"


@handle_errors(HANDLER)
def handler(event, context):
    params = get_query_params(event)
    poll_id = params.get("pollId")
    if not poll_id:
        raise ValidationError(message="pollId is required", function="handler", field="pollId")

    poll = get_poll(poll_id)
    if poll is None:
        raise NotFoundError(message=f"Poll '{poll_id}' not found", function="handler", resource="poll")

    return success_response(poll)
