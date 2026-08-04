"""
GET /invites/resolve -- Who was this invite sent to? (public route)

Lets the form prefill a recipient's name and address from the link they were
emailed, so someone who was personally invited doesn't have to retype details
we already hold.

Public because an invite recipient has no account -- there's no session to
authenticate against. The token is an opaque per-recipient uuid, so this
discloses only the address of whoever already holds that token, which is the
same trust model as the existing guest id: possession of the link IS the
credential.

Notably it does NOT accept an email and confirm whether it was invited -- that
would turn the route into an invitation oracle for any address you care to try.
"""

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError, NotFoundError
from lambdas.common.utility_helpers import success_response, get_query_params
from lambdas.common.polls_dynamo import get_poll

log = get_logger(__file__)

HANDLER = "invites_resolve"


@handle_errors(HANDLER)
def handler(event, context):
    params = get_query_params(event)
    poll_id = params.get("pollId")
    token = (params.get("t") or "").strip()

    if not poll_id:
        raise ValidationError(message="pollId is required", function="handler", field="pollId")
    if not token:
        raise ValidationError(message="t is required", function="handler", field="t")

    poll = get_poll(poll_id)
    if poll is None:
        raise NotFoundError(
            message=f"Poll '{poll_id}' not found", function="handler", resource="poll"
        )

    for invite in poll.get("invites") or []:
        if isinstance(invite, dict) and invite.get("token") == token:
            log.info(f"Invite token resolved for poll={poll_id}")
            return success_response(
                {"email": invite.get("email"), "name": invite.get("name")}
            )

    # A stale or made-up token is not an error worth shouting about -- the form
    # simply falls back to asking. 404 keeps it distinguishable from a bad
    # request while telling an attacker nothing about which tokens exist.
    raise NotFoundError(
        message="Invite not found", function="handler", resource="invite"
    )
