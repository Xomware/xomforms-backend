"""
GET /responses/list -- Who responded, with contact details (authed, creator).

The most sensitive route in the app: it returns respondents' names and email
addresses. Creator-only, enforced by get_poll_for_creator, and never exposed
publicly in any form.
"""

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError
from lambdas.common.utility_helpers import (
    success_response,
    get_caller_email,
    get_query_params,
)
from lambdas.common.polls_dynamo import get_poll_for_creator
from lambdas.common.responses_dynamo import get_responses_for_poll

log = get_logger(__file__)

HANDLER = "responses_list"


@handle_errors(HANDLER)
def handler(event, context):
    email = get_caller_email(event)

    params = get_query_params(event)
    poll_id = params.get("pollId")
    if not poll_id:
        raise ValidationError(message="pollId is required", function="handler", field="pollId")

    # 404 if it doesn't exist, 403 if the caller isn't the creator.
    get_poll_for_creator(poll_id, email, function="handler")

    respondents = []
    for item in get_responses_for_poll(poll_id):
        key = item.get("respondentKey", "")
        respondents.append(
            {
                "displayName": item.get("displayName"),
                # Guests supply one explicitly; an authed respondent's key IS
                # their address.
                "email": item.get("email") or (None if key.startswith("guest#") else key),
                "isGuest": key.startswith("guest#"),
                "submittedAt": item.get("submittedAt"),
                "blockCount": len(item.get("blocks") or []),
            }
        )

    respondents.sort(key=lambda r: r.get("submittedAt") or "", reverse=True)
    log.info(f"Respondents listed for {poll_id}: {len(respondents)}")
    return success_response({"pollId": poll_id, "respondents": respondents})
