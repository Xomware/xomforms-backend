"""
POST /invites/send -- Creator emails form invites via SES (authed).

Sends to each recipient individually rather than one message with everyone on
it: an invite is personalised, and more importantly a single To: list would
disclose the whole invitee list to every recipient.

Per-recipient failures are recorded, not raised. One bad address must not
abort the rest of the send, and the creator needs to see WHICH ones failed --
so the response reports per-recipient status and the same statuses are
persisted onto the poll for /invites/list to read back later.
"""

import re

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError
from lambdas.common.utility_helpers import (
    success_response,
    parse_body,
    get_caller_email,
    get_iso_timestamp,
)
from lambdas.common.polls_dynamo import get_poll_for_creator, update_poll_attributes
from lambdas.common.email_helpers import send_invite

log = get_logger(__file__)

HANDLER = "invites_send"

MAX_RECIPIENTS_PER_SEND = 50
# Deliberately permissive: real-world addresses defeat clever patterns, and SES
# is the actual authority on deliverability. This only catches obvious typos.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_recipients(raw) -> list[dict]:
    """Accepts ["a@b.com"] or [{"email": ..., "name": ...}]; dedupes on email."""
    if not isinstance(raw, list) or not raw:
        raise ValidationError(
            message="recipients must be a non-empty list", function="handler", field="recipients"
        )
    if len(raw) > MAX_RECIPIENTS_PER_SEND:
        raise ValidationError(
            message=f"at most {MAX_RECIPIENTS_PER_SEND} recipients per send",
            function="handler",
            field="recipients",
        )

    seen: set[str] = set()
    out: list[dict] = []
    for entry in raw:
        if isinstance(entry, str):
            email, name = entry, None
        elif isinstance(entry, dict):
            email, name = entry.get("email"), entry.get("name")
        else:
            raise ValidationError(
                message="each recipient must be an email string or an object",
                function="handler",
                field="recipients",
            )

        email = (email or "").strip().lower()
        if not _EMAIL_RE.match(email):
            raise ValidationError(
                message=f"'{email}' is not a valid email address",
                function="handler",
                field="recipients",
            )
        if email in seen:
            continue
        seen.add(email)
        out.append({"email": email, "name": (name or "").strip() or None})
    return out


def _merge_invites(existing: list, results: list[dict]) -> list[dict]:
    """Latest status per address wins, so re-sending updates rather than piles up."""
    by_email = {i.get("email"): dict(i) for i in (existing or []) if isinstance(i, dict)}
    for result in results:
        by_email[result["email"]] = result
    return sorted(by_email.values(), key=lambda i: i.get("email") or "")


@handle_errors(HANDLER)
def handler(event, context):
    email = get_caller_email(event)

    body = parse_body(event)
    poll_id = body.get("pollId")
    if not poll_id:
        raise ValidationError(message="pollId is required", function="handler", field="pollId")

    recipients = _normalize_recipients(body.get("recipients"))

    # Raises 404 if it doesn't exist, 403 if the caller isn't the creator.
    poll = get_poll_for_creator(poll_id, email, function="handler")

    sender_name = (body.get("senderName") or "").strip() or email.split("@")[0]
    form_title = poll.get("title") or "a form"
    sent_at = get_iso_timestamp()

    results: list[dict] = []
    for recipient in recipients:
        record = {"email": recipient["email"], "name": recipient["name"], "sentAt": sent_at}
        try:
            send_invite(
                to_email=recipient["email"],
                recipient_name=recipient["name"],
                sender_name=sender_name,
                form_title=form_title,
                poll_id=poll_id,
                poll=poll,
            )
            record["status"] = "sent"
        except Exception as err:
            # Recorded, not raised -- one bad address must not abort the batch.
            record["status"] = "failed"
            record["error"] = str(err)[:300]
            log.error(f"Invite failed for {recipient['email']} on poll {poll_id}: {err}")
        results.append(record)

    merged = _merge_invites(poll.get("invites"), results)
    update_poll_attributes(poll_id, {"invites": merged})

    sent = sum(1 for r in results if r["status"] == "sent")
    failed = len(results) - sent
    log.info(f"Invites for {poll_id}: {sent} sent, {failed} failed")

    return success_response(
        {"pollId": poll_id, "sent": sent, "failed": failed, "results": results}
    )
