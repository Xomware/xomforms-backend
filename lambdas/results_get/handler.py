"""
GET /results/get -- Creator's own results dashboard (authed, CUSTOM
authorizer). Always allowed for that poll's creatorEmail; 403 for any
other authed caller. The respondent-facing (guests included) view lives
in the separate lambdas/results_get_public/handler.py -- a single route
can't serve both because a NONE-authorization API Gateway route never
populates requestContext.authorizer at all, so there'd be no way to tell
"the creator" apart from anyone else on that route. See the comment block
at the top of xomforms-infrastructure/terraform/lambda.tf.
"""

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError, NotFoundError, ForbiddenError
from lambdas.common.utility_helpers import success_response, get_query_params, get_caller_email
from lambdas.common.polls_dynamo import get_poll
from lambdas.common.overlap import compute_overlap

log = get_logger(__file__)

HANDLER = "results_get"


@handle_errors(HANDLER)
def handler(event, context):
    # Identity first -- fail fast with 401 before touching the poll.
    email = get_caller_email(event)

    params = get_query_params(event)
    poll_id = params.get("pollId")
    if not poll_id:
        raise ValidationError(message="pollId is required", function="handler", field="pollId")

    poll = get_poll(poll_id)
    if poll is None:
        raise NotFoundError(message=f"Poll '{poll_id}' not found", function="handler", resource="poll")

    if email != poll.get("creatorEmail"):
        raise ForbiddenError(
            message="Only the poll's creator can view results on this route",
            function="handler",
            reason="not_creator",
        )

    return success_response(compute_overlap(poll_id))
