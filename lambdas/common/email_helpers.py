"""
XOMFORMS Email Helpers
======================
Renders the branded form-invite templates and sends them through SES.

Templates live in lambdas/common/email_templates/ and ship inside the shared
lambda layer, so they're read from disk relative to this module rather than
fetched at runtime.

Sending config comes from SSM (written by ses.tf):
    /xomforms/ses/FROM_ADDRESS       -> noreply@xomforms.xomware.com
    /xomforms/ses/CONFIGURATION_SET  -> xomforms-invites
Both are cached per warm container -- they change roughly never, and a
GetParameter per recipient would be pure latency on a bulk send.
"""

import html
import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import boto3

from lambdas.common.logger import get_logger
from lambdas.common.errors import XomformsError
from lambdas.common.constants import PRODUCT, WEB_BASE_URL

log = get_logger(__file__)

_TEMPLATE_DIR = Path(__file__).parent / "email_templates"

ses = boto3.client("sesv2", region_name="us-east-1")
ssm = boto3.client("ssm", region_name="us-east-1")


class EmailSendError(XomformsError):
    """Raised when SES rejects a message. 502 -- the failure is downstream."""

    def __init__(self, message: str, handler: str = "email_helpers", function: str = "unknown"):
        super().__init__(message=message, handler=handler, function=function, status=502)


@lru_cache(maxsize=1)
def _load_templates() -> tuple[str, str]:
    html_body = (_TEMPLATE_DIR / "form_invite.html").read_text(encoding="utf-8")
    text_body = (_TEMPLATE_DIR / "form_invite.txt").read_text(encoding="utf-8")
    return html_body, text_body


@lru_cache(maxsize=8)
def _ssm_value(name: str, fallback: str) -> str:
    try:
        res = ssm.get_parameter(Name=name)
        return res["Parameter"]["Value"]
    except Exception as err:
        # A missing parameter must not take the whole send down -- fall back to
        # the documented default and log loudly.
        log.warning(f"SSM lookup failed for {name} ({err}); falling back to {fallback}")
        return fallback


def form_url(poll_id: str) -> str:
    """The public respond URL for a form -- what the invite's CTA points at."""
    return f"{WEB_BASE_URL}/f/{poll_id}"


def render_invite(
    recipient_name: str | None,
    sender_name: str,
    form_title: str,
    poll_id: str,
) -> tuple[str, str]:
    """
    Substitute the template placeholders, returning (html, text).

    EVERY user-supplied value is HTML-escaped before it reaches the HTML body.
    A form title is arbitrary creator input and lands inside the markup, so
    without escaping a title containing a tag would inject into the email --
    the template README calls this out explicitly. The text body is not escaped
    (there's no markup to break) but is still substituted from the same values.
    """
    html_template, text_template = _load_templates()

    safe_recipient = recipient_name.strip() if recipient_name and recipient_name.strip() else "there"
    year = str(datetime.now(timezone.utc).year)
    url = form_url(poll_id)

    raw = {
        "recipientName": safe_recipient,
        "senderName": sender_name,
        "formTitle": form_title,
        "formUrl": url,
        "year": year,
    }

    html_out = html_template
    text_out = text_template
    for token, value in raw.items():
        html_out = html_out.replace("{{" + token + "}}", html.escape(str(value), quote=True))
        text_out = text_out.replace("{{" + token + "}}", str(value))

    return html_out, text_out


def send_invite(
    to_email: str,
    recipient_name: str | None,
    sender_name: str,
    form_title: str,
    poll_id: str,
) -> None:
    """Send one invite. Raises EmailSendError so the caller can record status."""
    html_body, text_body = render_invite(recipient_name, sender_name, form_title, poll_id)
    from_address = _ssm_value(f"/{PRODUCT}/ses/FROM_ADDRESS", f"noreply@{PRODUCT}.xomware.com")
    config_set = _ssm_value(f"/{PRODUCT}/ses/CONFIGURATION_SET", f"{PRODUCT}-invites")

    try:
        ses.send_email(
            FromEmailAddress=from_address,
            Destination={"ToAddresses": [to_email]},
            ConfigurationSetName=config_set,
            Content={
                "Simple": {
                    "Subject": {
                        "Data": f"{sender_name} invited you to fill out “{form_title}”",
                        "Charset": "UTF-8",
                    },
                    "Body": {
                        "Html": {"Data": html_body, "Charset": "UTF-8"},
                        "Text": {"Data": text_body, "Charset": "UTF-8"},
                    },
                }
            },
        )
        log.info(f"Invite sent for poll={poll_id}")
    except Exception as err:
        log.error(f"SES send failed for poll={poll_id}: {err}")
        raise EmailSendError(message=str(err), function="send_invite")
