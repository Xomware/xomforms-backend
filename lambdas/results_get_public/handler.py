"""
GET /results/get-public -- Respondent-facing results view (guests included).
Public route, no authorizer context available at all.

Gated by the poll's resultsVisibility:
    hidden          -- 403 for everyone; creator-only via /results/get
    after_response  -- 403 until the caller has actually answered
    always          -- open to anyone with the link

THIS is where the gate lives, not in the UI. Blurring the results client-side
is a CSS trick and the JSON is one devtools tab away -- on an availability poll
that leaks exactly who is free when. The caller identifies themselves the same
way they do everywhere else on a public route: an authed respondent passes
?email (their own, already known to them), a guest passes ?guestId, which is
the unguessable uuid4 their browser generated at submit time.

Note the after_response check is deliberately an EXISTENCE check on a response
row, not a trust decision about the identifier. The worst a forged identifier
achieves is proving that SOMEBODY answered -- which the response count on the
results payload already tells you once you're through the gate.
"""

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError, NotFoundError, ForbiddenError
from lambdas.common.utility_helpers import success_response, get_query_params
from lambdas.common.constants import (
    RESULTS_VISIBILITY_AFTER_RESPONSE,
    RESULTS_VISIBILITY_HIDDEN,
)
from lambdas.common.models import resolve_results_visibility
from lambdas.common.polls_dynamo import get_poll
from lambdas.common.responses_dynamo import has_responded
from lambdas.common.overlap import compute_overlap, compute_form_results

log = get_logger(__file__)

HANDLER = "results_get_public"


def _is_qa(poll: dict) -> bool:
    return poll.get("formType") == "qa" or bool(poll.get("fields"))


def _respondent_key(params: dict) -> str | None:
    """
    How the caller claims to have answered. Guests key on their browser's
    guestId; signed-in respondents pass the email they submitted under (this
    route has no authorizer to read it from).
    """
    guest_id = params.get("guestId")
    if guest_id:
        return f"guest#{guest_id}"
    email = params.get("email")
    return email or None


@handle_errors(HANDLER)
def handler(event, context):
    params = get_query_params(event)
    poll_id = params.get("pollId")
    if not poll_id:
        raise ValidationError(message="pollId is required", function="handler", field="pollId")

    poll = get_poll(poll_id)
    if poll is None:
        raise NotFoundError(message=f"Poll '{poll_id}' not found", function="handler", resource="poll")

    visibility = resolve_results_visibility(poll)

    if visibility == RESULTS_VISIBILITY_HIDDEN:
        raise ForbiddenError(
            message="Results for this form are visible to its creator only",
            function="handler",
            reason="resultsVisibility=hidden",
        )

    if visibility == RESULTS_VISIBILITY_AFTER_RESPONSE:
        respondent_key = _respondent_key(params)
        if respondent_key is None or not has_responded(poll_id, respondent_key):
            raise ForbiddenError(
                message="Fill out this form to see everyone's results",
                function="handler",
                reason="resultsVisibility=after_response",
            )

    # Q&A forms return per-field tallies; scheduler polls return the unchanged
    # OverlapResult (grid + best contiguous window).
    if _is_qa(poll):
        return success_response(compute_form_results(poll_id))
    return success_response(compute_overlap(poll_id))
