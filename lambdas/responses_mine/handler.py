"""
GET /responses/mine -- Forms the caller has responded to (authed).

Backs the "Responded" half of the dashboard. Pass ?pollId=<id> to fetch just
the caller's own response to one form (used to prefill the edit view) instead
of the whole list.

Each row is joined to its poll so the dashboard can show a title and status
without N follow-up requests. A response whose poll has since been deleted is
dropped rather than returned as a husk with no title.
"""

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors
from lambdas.common.utility_helpers import (
    success_response,
    get_caller_email,
    get_query_params,
)
from lambdas.common.polls_dynamo import get_poll
from lambdas.common.responses_dynamo import query_responses_by_respondent, get_response
from lambdas.common.models import resolve_allow_response_edits

log = get_logger(__file__)

HANDLER = "responses_mine"


def _summarize(poll: dict, response: dict) -> dict:
    """The dashboard/edit view's shape: enough poll context to render a row."""
    return {
        "pollId": poll["pollId"],
        "title": poll.get("title"),
        "formType": poll.get("formType", "scheduler"),
        "creatorEmail": poll.get("creatorEmail"),
        "closeAt": poll.get("closeAt"),
        "timezone": poll.get("timezone"),
        "createdAt": poll.get("createdAt"),
        "allowResponseEdits": resolve_allow_response_edits(poll),
        "submittedAt": response.get("submittedAt"),
        "displayName": response.get("displayName"),
        "blocks": response.get("blocks"),
        "answers": response.get("answers"),
    }


@handle_errors(HANDLER)
def handler(event, context):
    email = get_caller_email(event)
    params = get_query_params(event)
    poll_id = params.get("pollId")

    if poll_id:
        response = get_response(poll_id, email)
        if response is None:
            return success_response({"response": None})
        poll = get_poll(poll_id)
        if poll is None:
            return success_response({"response": None})
        return success_response({"response": _summarize(poll, response)})

    responses = query_responses_by_respondent(email)
    items = []
    for response in responses:
        poll = get_poll(response["pollId"])
        # The poll was deleted out from under the response, or the cascade is
        # mid-flight. Either way there's nothing meaningful to show.
        if poll is None:
            continue
        items.append(_summarize(poll, response))

    items.sort(key=lambda r: r.get("submittedAt") or "", reverse=True)
    log.info(f"Responses listed for {email}: {len(items)}")
    return success_response({"responses": items})
