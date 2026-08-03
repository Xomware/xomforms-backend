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
from lambdas.common.constants import DEFAULT_RESULTS_VISIBILITY
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
    # Attributes common to every form type.
    poll = {
        "pollId": poll_id,
        "creatorEmail": email,
        "title": req.title,
        "description": req.description,
        "formType": req.formType,
        "guestAllowed": req.guestAllowed,
        "showResultsToRespondents": req.showResultsToRespondents,
        # Persist the explicit setting so resolve_results_visibility()'s
        # legacy-boolean shim only ever fires for pre-existing polls. An
        # unspecified value defaults to after_response: on an availability
        # poll, seeing everyone else's answers first biases your own.
        "resultsVisibility": req.resultsVisibility or DEFAULT_RESULTS_VISIBILITY,
        "allowResponseEdits": req.allowResponseEdits,
        "quickFilters": req.quickFilters or [],
        "createdAt": get_iso_timestamp(),
    }

    if req.formType == "qa":
        # A Q&A form persists its typed field list; no scheduler grid config.
        poll["fields"] = [f.model_dump() for f in (req.fields or [])]
    else:
        # Scheduler poll -- byte-for-byte the original item shape (plus the
        # additive formType attribute above).
        # dayStart/dayEnd/granularity are the persisted grid window that
        # generate_grid reads. For the windowed shape these were DERIVED in the
        # model validator (dayStart=earliestStart, dayEnd=latestStart+duration,
        # granularity=15); for legacy requests they were supplied directly.
        poll["startDate"] = req.startDate.isoformat()
        poll["endDate"] = req.endDate.isoformat()
        poll["dayStartMinute"] = req.dayStartMinute
        poll["dayEndMinute"] = req.dayEndMinute
        poll["granularityMinutes"] = req.granularityMinutes
        poll["timezone"] = req.timezone
        # Persist the creator's start-range inputs too (may be None for a legacy
        # request), so the frontend can round-trip and re-render them.
        poll["earliestStartMinute"] = req.earliestStartMinute
        poll["latestStartMinute"] = req.latestStartMinute
        # A single-slot event when unspecified: default to one block so polls
        # created before this field keep behaving identically on results.
        poll["eventDurationMinutes"] = req.eventDurationMinutes or req.granularityMinutes

    if req.closeAt is not None:
        poll["closeAt"] = req.closeAt.isoformat()

    put_poll(poll)
    log.info(f"Poll created: {poll_id} by {email}")

    return success_response(poll, status_code=201)
