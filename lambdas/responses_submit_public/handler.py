"""
POST /responses/submit-guest -- Guest submit, keyed by "guest#<uuid>";
gated by the poll's guestAllowed flag. Public route, no authorizer context.
"""

import uuid
from pydantic import ValidationError as PydanticValidationError

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError, NotFoundError, ForbiddenError
from lambdas.common.utility_helpers import success_response, parse_body
from lambdas.common.models import SubmitAvailabilityRequest, SubmitAnswersRequest
from lambdas.common.polls_dynamo import get_poll
from lambdas.common.responses_dynamo import submit_availability, submit_answers

log = get_logger(__file__)

HANDLER = "responses_submit_public"


def _is_qa(poll: dict) -> bool:
    return poll.get("formType") == "qa" or bool(poll.get("fields"))


@handle_errors(HANDLER)
def handler(event, context):
    body = parse_body(event)
    poll_id = body.get("pollId")
    if not poll_id:
        raise ValidationError(message="pollId is required", function="handler", field="pollId")

    poll = get_poll(poll_id)
    if poll is None:
        raise NotFoundError(message=f"Poll '{poll_id}' not found", function="handler", resource="poll")

    if not poll.get("guestAllowed", False):
        raise ForbiddenError(
            message="This poll does not accept guest submissions",
            function="handler",
            reason="guestAllowed=false",
        )

    # Guest weak-identity: name-only, no Cognito signup (locked MVP decision).
    # guestId is best-effort client-supplied (localStorage guest#<uuid>) so a
    # returning guest on the same browser upserts instead of duplicating;
    # mint a fresh one server-side if the client didn't send one.
    guest_id = body.get("guestId") or str(uuid.uuid4())
    respondent_key = f"guest#{guest_id}"

    if _is_qa(poll):
        try:
            req = SubmitAnswersRequest(**body)
        except PydanticValidationError as err:
            raise ValidationError(message=str(err), function="handler")
        result = submit_answers(
            poll, respondent_key=respondent_key, display_name=req.displayName, answers=req.answers
        )
        log.info(f"Guest answers submitted: poll={poll_id} respondent={respondent_key}")
        return success_response(result)

    # Scheduler poll -- unchanged availability path.
    try:
        req = SubmitAvailabilityRequest(**body)
    except PydanticValidationError as err:
        raise ValidationError(message=str(err), function="handler")

    result = submit_availability(poll, respondent_key=respondent_key, display_name=req.displayName, blocks=req.blocks)
    log.info(f"Guest response submitted: poll={poll_id} respondent={respondent_key}")

    return success_response(result)
