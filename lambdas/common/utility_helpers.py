"""
XOMFORMS Utility Helpers
========================
Common utilities for Lambda handlers. Ported from xomify-backend's
lambdas/common/utility_helpers.py; trimmed of Spotify/legacy-compat cruft
since this is a fresh repo with no back-compat debt to carry.
"""

import json
import decimal
from datetime import datetime, timezone
from typing import Any, Optional, Set

from lambdas.common.logger import get_logger

log = get_logger(__file__)


# ============================================
# JSON Encoding
# ============================================

class XomformsJSONEncoder(json.JSONEncoder):
    """
    Custom JSON encoder that handles:
    - Decimal (from DynamoDB)
    - datetime objects
    - sets
    """

    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            if obj % 1 == 0:
                return int(obj)
            return float(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, set):
            return list(obj)
        return super().default(obj)


def json_dumps(obj: Any) -> str:
    """Serialize object to JSON string with custom encoder."""
    return json.dumps(obj, cls=XomformsJSONEncoder)


# ============================================
# Request Parsing
# ============================================

def is_api_request(event: dict) -> bool:
    """Check if the event is from API Gateway."""
    return isinstance(event.get('body'), str)


def parse_body(event: dict) -> dict:
    """
    Parse the request body from an event.
    Handles both API Gateway (string) and direct invocation (dict).
    """
    body = event.get('body')

    if body is None:
        return {}

    if isinstance(body, str):
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            log.warning("Failed to parse body as JSON")
            return {}

    return body if isinstance(body, dict) else {}


def get_query_params(event: dict) -> dict:
    """Get query string parameters from event."""
    return event.get('queryStringParameters') or {}


def get_path_params(event: dict) -> dict:
    """Get path parameters from event."""
    return event.get('pathParameters') or {}


# ============================================
# Response Building
# ============================================

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Amz-Date,X-Api-Key,X-Amz-Security-Token",
    "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
    "Content-Type": "application/json",
}


def success_response(body: Any, status_code: int = 200, is_api: bool = True) -> dict:
    """Build a successful Lambda response."""
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json_dumps(body) if is_api else body,
        "isBase64Encoded": False,
    }


def error_response(
    message: str,
    status_code: int = 500,
    is_api: bool = True,
    details: Optional[dict] = None,
) -> dict:
    """Build an error Lambda response."""
    body = {
        "error": {
            "message": message,
            "status": status_code,
            **(details or {}),
        }
    }

    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json_dumps(body) if is_api else body,
        "isBase64Encoded": False,
    }


# ============================================
# Input Validation
# ============================================

def validate_input(
    data: Optional[dict],
    required_fields: Set[str] = None,
    optional_fields: Set[str] = None,
) -> tuple[bool, Optional[str]]:
    """Validate input data has required fields and no extra fields."""
    required_fields = required_fields or set()
    optional_fields = optional_fields or set()

    if data is None:
        if required_fields:
            return False, f"Missing required fields: {required_fields}"
        return True, None

    if not isinstance(data, dict):
        return False, "Input must be a dictionary"

    data_keys = set(data.keys())
    allowed_keys = required_fields | optional_fields

    missing = required_fields - data_keys
    if missing:
        return False, f"Missing required fields: {missing}"

    if optional_fields:
        extra = data_keys - allowed_keys
        if extra:
            return False, f"Unexpected fields: {extra}"

    return True, None


def require_fields(data: dict, *fields: str) -> None:
    """
    Raise ValidationError if any required fields are missing.

    Usage:
        require_fields(body, 'title', 'startDate')
    """
    from lambdas.common.errors import ValidationError

    missing = [f for f in fields if f not in data or data[f] is None]
    if missing:
        raise ValidationError(
            message=f"Missing required fields: {', '.join(missing)}",
            field=missing[0],
        )


# ============================================
# Caller Identity Resolution
# ============================================
# Xomforms' custom authorizer (ported from xomify) populates
# event.requestContext.authorizer.email for every authed route. Unlike
# xomify, there is no legacy query/body fallback here -- this is a fresh
# repo, so we trust the authorizer context only.

def get_caller_email(event: dict) -> str:
    """
    Resolve the caller's email from the authorizer context.

    Raises MissingCallerIdentityError (HTTP 401) if absent -- callers on
    authed routes are always expected to have passed through the custom
    authorizer first.
    """
    from lambdas.common.errors import MissingCallerIdentityError

    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") if isinstance(request_context, dict) else None
    if isinstance(authorizer, dict):
        email = authorizer.get("email")
        if isinstance(email, str) and email:
            return email

    raise MissingCallerIdentityError(field="email")


# ============================================
# Date/Time Utilities
# ============================================

def get_timestamp() -> str:
    """Get current UTC timestamp in standard format."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def get_iso_timestamp() -> str:
    """Get current UTC timestamp in ISO 8601 format (with Z suffix)."""
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
