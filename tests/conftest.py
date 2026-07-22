"""
Shared pytest fixtures for xomforms-backend lambda tests.
"""

import pytest
import os
import sys
from unittest.mock import MagicMock

# Add repo root to path so `lambdas.*` imports resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Set required env vars before any lambda modules are imported
_TEST_ENV_VARS = {
    "AWS_DEFAULT_REGION": "us-east-1",
    "DYNAMODB_KMS_ALIAS": "alias/xomforms-kms-test",
    "POLLS_TABLE_NAME": "xomforms-polls-test",
    "RESPONSES_TABLE_NAME": "xomforms-responses-test",
    "POLLS_CREATOR_INDEX": "creatorEmail-createdAt-index",
    # Fake AWS credentials for moto. moto intercepts the HTTP layer, but
    # botocore still needs *some* (even fake) credentials present to sign
    # a request before moto gets a chance to intercept it -- without these,
    # moto-backed tests only pass by accident on a machine that happens to
    # have real AWS credentials configured for unrelated reasons (e.g. a
    # local ~/.aws/credentials), and fail with botocore.exceptions.
    # NoCredentialsError anywhere else, including CI. Discovered via a CI
    # run that failed while the exact same suite passed locally.
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SECURITY_TOKEN": "testing",
    "AWS_SESSION_TOKEN": "testing",
}
for key, value in _TEST_ENV_VARS.items():
    os.environ.setdefault(key, value)


@pytest.fixture
def mock_context():
    """Mock AWS Lambda context."""
    context = MagicMock()
    context.function_name = "test-function"
    context.memory_limit_in_mb = 128
    context.invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:test-function"
    context.aws_request_id = "test-request-id"
    return context


def _base_api_gateway_event() -> dict:
    """Internal helper -- returns a fresh base event dict (avoids fixture-state sharing)."""
    return {
        "httpMethod": "GET",
        "path": "/test",
        "queryStringParameters": {},
        "headers": {"Content-Type": "application/json"},
        "body": None,
        "isBase64Encoded": False,
    }


@pytest.fixture
def api_gateway_event():
    """Base API Gateway event structure."""
    return _base_api_gateway_event()


@pytest.fixture
def authorized_event():
    """
    Build an API Gateway event WITH the Cognito authorizer context populated,
    mirroring what the native COGNITO_USER_POOLS authorizer places into
    requestContext.authorizer.claims after validating the caller's Cognito
    ID token. Top-level keys can be overridden via kwargs (e.g. httpMethod,
    path, body, queryStringParameters).
    """

    def _make(email: str = "creator@example.com", **overrides) -> dict:
        event = _base_api_gateway_event()
        event["requestContext"] = {"authorizer": {"claims": {"email": email}}}
        event.update(overrides)
        return event

    return _make


@pytest.fixture
def public_event():
    """
    Build an API Gateway event for the UNAUTHENTICATED public routes
    (polls_get, responses_submit_public) -- no authorizer context at all.
    """

    def _make(**overrides) -> dict:
        event = _base_api_gateway_event()
        event["requestContext"] = {}
        event.update(overrides)
        return event

    return _make


@pytest.fixture
def sample_poll_config():
    """A minimal, valid poll config dict for reuse across tests."""
    return {
        "pollId": "poll-1",
        "creatorEmail": "creator@example.com",
        "title": "Fantasy Draft",
        "startDate": "2026-08-03",
        "endDate": "2026-08-05",
        "dayStartMinute": 8 * 60,
        "dayEndMinute": 14 * 60,
        "granularityMinutes": 30,
        "timezone": "America/New_York",
        "guestAllowed": True,
        "showResultsToRespondents": False,
    }
