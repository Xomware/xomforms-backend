"""
GET /results/get-public -- Respondent-facing results view (guests
included). Public route, no authorizer context available at all. Allowed
only when the poll's showResultsToRespondents flag is true -- same
behavior for authed non-creator respondents and guests alike, since this
route has no way to distinguish between them.
"""

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError, NotFoundError, ForbiddenError
from lambdas.common.utility_helpers import success_response, get_query_params
from lambdas.common.polls_dynamo import get_poll
from lambdas.common.overlap import compute_overlap

log = get_logger(__file__)

HANDLER = "results_get_public"


@handle_errors(HANDLER)
def handler(event, context):
    params = get_query_params(event)
    poll_id = params.get("pollId")
    if not poll_id:
        raise ValidationError(message="pollId is required", function="handler", field="pollId")

    poll = get_poll(poll_id)
    if poll is None:
        raise NotFoundError(message=f"Poll '{poll_id}' not found", function="handler", resource="poll")

    if not poll.get("showResultsToRespondents", False):
        raise ForbiddenError(
            message="Results are not visible to respondents for this poll",
            function="handler",
            reason="showResultsToRespondents=false",
        )

    return success_response(compute_overlap(poll_id))
