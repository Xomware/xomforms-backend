"""
GET /responses/get-mine -- A GUEST's own response to one form (public route).

Signed-in respondents use the authed /responses/mine?pollId=<id> instead; this
route exists because a guest has no JWT, and an API Gateway route with
authorization NONE never populates requestContext.authorizer at all -- so a
single route genuinely cannot identify a signed-in caller here.

The caller must present the guestId their browser generated at submit time
(responses.service.ts persists it in localStorage). That id is an unguessable
uuid4 and is the same credential the existing submit-guest route already
trusts, so this returns exactly one row: the one keyed to it.

It deliberately does NOT accept a bare email. Doing so would let anyone read
any respondent's answers by guessing an address.
"""

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError
from lambdas.common.utility_helpers import success_response, get_query_params
from lambdas.common.polls_dynamo import get_poll
from lambdas.common.responses_dynamo import get_response
from lambdas.common.models import resolve_allow_response_edits

log = get_logger(__file__)

HANDLER = "responses_get_mine"


@handle_errors(HANDLER)
def handler(event, context):
    params = get_query_params(event)
    poll_id = params.get("pollId")
    guest_id = params.get("guestId")

    if not poll_id:
        raise ValidationError(message="pollId is required", function="handler", field="pollId")
    if not guest_id:
        raise ValidationError(
            message="guestId is required on this route; signed-in callers use /responses/mine",
            function="handler",
            field="guestId",
        )

    respondent_key = f"guest#{guest_id}"
    response = get_response(poll_id, respondent_key)
    if response is None:
        return success_response({"response": None})

    poll = get_poll(poll_id)
    if poll is None:
        return success_response({"response": None})

    return success_response(
        {
            "response": {
                "pollId": poll_id,
                "displayName": response.get("displayName"),
                "blocks": response.get("blocks"),
                "answers": response.get("answers"),
                "submittedAt": response.get("submittedAt"),
                "allowResponseEdits": resolve_allow_response_edits(poll),
            }
        }
    )
