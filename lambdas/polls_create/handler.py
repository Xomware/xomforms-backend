"""
POST /polls/create -- Creator builds a schedule poll (authed).
"""

import uuid
from pydantic import ValidationError as PydanticValidationError

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError
from lambdas.common.utility_helpers import (
    success_response,
    parse_body,
    get_caller_email,
    get_iso_timestamp,
)
from lambdas.common.models import CreatePollRequest
from lambdas.common.polls_dynamo import put_poll

log = get_logger(__file__)

HANDLER = "polls_create"


@handle_errors(HANDLER)
def handler(event, context):
    # Identity first -- fail fast with 401 before validating the payload.
    email = get_caller_email(event)

    body = parse_body(event)
    try:
        req = CreatePollRequest(**body)
    except PydanticValidationError as err:
        raise ValidationError(message=str(err), function="handler")

    poll_id = str(uuid.uuid4())
    poll = {
        "pollId": poll_id,
        "creatorEmail": email,
        "title": req.title,
        "description": req.description,
        "startDate": req.startDate.isoformat(),
        "endDate": req.endDate.isoformat(),
        "dayStartMinute": req.dayStartMinute,
        "dayEndMinute": req.dayEndMinute,
        "granularityMinutes": req.granularityMinutes,
        "timezone": req.timezone,
        "guestAllowed": req.guestAllowed,
        "showResultsToRespondents": req.showResultsToRespondents,
        "createdAt": get_iso_timestamp(),
    }
    if req.closeAt is not None:
        poll["closeAt"] = req.closeAt.isoformat()

    put_poll(poll)
    log.info(f"Poll created: {poll_id} by {email}")

    return success_response(poll, status_code=201)
