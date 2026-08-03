"""
POST /polls/update -- Creator edits a form's settings (authed).

Scoped deliberately to SETTINGS: title, description, guest access, results
visibility, and whether respondents may edit their answers. The date range,
time window, granularity, and field list are NOT editable here -- changing
those under respondents who already answered would invalidate submissions
(painted blocks might no longer exist on the grid). That needs a migration
story, not a toggle.
"""

from pydantic import ValidationError as PydanticValidationError

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError
from lambdas.common.utility_helpers import success_response, parse_body, get_caller_email
from lambdas.common.models import UpdatePollRequest
from lambdas.common.polls_dynamo import get_poll_for_creator, update_poll_attributes

log = get_logger(__file__)

HANDLER = "polls_update"


@handle_errors(HANDLER)
def handler(event, context):
    # Identity first -- fail fast with 401 before validating the payload.
    email = get_caller_email(event)

    body = parse_body(event)
    try:
        req = UpdatePollRequest(**body)
    except PydanticValidationError as err:
        raise ValidationError(message=str(err), function="handler")

    # Raises 404 if it doesn't exist, 403 if the caller isn't the creator.
    get_poll_for_creator(req.pollId, email, function="handler")

    changes = req.changes()
    if not changes:
        raise ValidationError(
            message="no settings supplied to update", function="handler"
        )

    # Keep the legacy boolean consistent with the richer setting, so any client
    # still reading showResultsToRespondents doesn't drift out of sync.
    if "resultsVisibility" in changes:
        changes["showResultsToRespondents"] = changes["resultsVisibility"] == "always"

    updated = update_poll_attributes(req.pollId, changes)
    log.info(f"Poll settings updated: {req.pollId} by {email}")

    return success_response(updated)
