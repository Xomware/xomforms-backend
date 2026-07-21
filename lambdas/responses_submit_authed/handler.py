"""
POST /responses/submit -- Authed respondent upsert, keyed by email.
"""

from pydantic import ValidationError as PydanticValidationError

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError, NotFoundError
from lambdas.common.utility_helpers import success_response, parse_body, get_caller_email
from lambdas.common.models import SubmitAvailabilityRequest
from lambdas.common.polls_dynamo import get_poll
from lambdas.common.responses_dynamo import submit_availability

log = get_logger(__file__)

HANDLER = "responses_submit_authed"


@handle_errors(HANDLER)
def handler(event, context):
    # Identity first -- fail fast with 401 before touching the poll/payload.
    email = get_caller_email(event)

    body = parse_body(event)
    poll_id = body.get("pollId")
    if not poll_id:
        raise ValidationError(message="pollId is required", function="handler", field="pollId")

    try:
        req = SubmitAvailabilityRequest(**body)
    except PydanticValidationError as err:
        raise ValidationError(message=str(err), function="handler")

    poll = get_poll(poll_id)
    if poll is None:
        raise NotFoundError(message=f"Poll '{poll_id}' not found", function="handler", resource="poll")

    result = submit_availability(poll, respondent_key=email, display_name=req.displayName, blocks=req.blocks)
    log.info(f"Response submitted: poll={poll_id} respondent={email}")

    return success_response(result)
