"""
Tests for lambdas/polls_create/handler.py -- POST /polls/create (authed).

Written RED-first per Phase 2 of docs/features/xomforms/PLAN.md.
"""

import json
from unittest.mock import patch


def _valid_body(**overrides):
    base = {
        "title": "Fantasy Draft",
        "startDate": "2026-08-03",
        "endDate": "2026-08-05",
        "dayStartMinute": 480,
        "dayEndMinute": 840,
        "granularityMinutes": 30,
        "timezone": "America/New_York",
    }
    base.update(overrides)
    return base


class TestPollsCreateHandler:
    @patch("lambdas.polls_create.handler.put_poll")
    def test_creates_poll_with_caller_email_as_creator(self, mock_put_poll, mock_context, authorized_event):
        from lambdas.polls_create.handler import handler

        event = authorized_event(
            email="creator@example.com",
            httpMethod="POST",
            path="/polls/create",
            body=json.dumps(_valid_body()),
        )

        response = handler(event, mock_context)
        assert response["statusCode"] == 201

        body = json.loads(response["body"])
        assert body["creatorEmail"] == "creator@example.com"
        assert body["title"] == "Fantasy Draft"
        assert "pollId" in body
        assert body["guestAllowed"] is False  # default
        assert body["showResultsToRespondents"] is False  # default

        mock_put_poll.assert_called_once()

    @patch("lambdas.polls_create.handler.put_poll")
    def test_missing_caller_identity_returns_401_no_write(self, mock_put_poll, mock_context, public_event):
        from lambdas.polls_create.handler import handler

        event = public_event(httpMethod="POST", path="/polls/create", body=json.dumps(_valid_body()))
        response = handler(event, mock_context)

        assert response["statusCode"] == 401
        mock_put_poll.assert_not_called()

    @patch("lambdas.polls_create.handler.put_poll")
    def test_invalid_payload_returns_400_no_write(self, mock_put_poll, mock_context, authorized_event):
        from lambdas.polls_create.handler import handler

        event = authorized_event(
            httpMethod="POST",
            path="/polls/create",
            body=json.dumps(_valid_body(endDate="2026-08-01", startDate="2026-08-05")),  # end before start
        )
        response = handler(event, mock_context)

        assert response["statusCode"] == 400
        mock_put_poll.assert_not_called()

    @patch("lambdas.polls_create.handler.put_poll")
    def test_guest_allowed_and_show_results_pass_through(self, mock_put_poll, mock_context, authorized_event):
        from lambdas.polls_create.handler import handler

        event = authorized_event(
            httpMethod="POST",
            path="/polls/create",
            body=json.dumps(_valid_body(guestAllowed=True, showResultsToRespondents=True)),
        )
        response = handler(event, mock_context)
        body = json.loads(response["body"])

        assert body["guestAllowed"] is True
        assert body["showResultsToRespondents"] is True

    @patch("lambdas.polls_create.handler.put_poll")
    def test_event_duration_persisted_when_provided(self, mock_put_poll, mock_context, authorized_event):
        from lambdas.polls_create.handler import handler

        event = authorized_event(
            httpMethod="POST",
            path="/polls/create",
            body=json.dumps(_valid_body(granularityMinutes=30, eventDurationMinutes=120)),
        )
        response = handler(event, mock_context)
        body = json.loads(response["body"])

        assert body["eventDurationMinutes"] == 120
        saved = mock_put_poll.call_args[0][0]
        assert saved["eventDurationMinutes"] == 120

    @patch("lambdas.polls_create.handler.put_poll")
    def test_event_duration_defaults_to_granularity_when_omitted(self, mock_put_poll, mock_context, authorized_event):
        """A poll with no explicit event length is a single-slot event, so the
        stored eventDurationMinutes defaults to the block granularity -- keeps
        pre-existing polls (created before this field) behaving identically."""
        from lambdas.polls_create.handler import handler

        event = authorized_event(
            httpMethod="POST",
            path="/polls/create",
            body=json.dumps(_valid_body(granularityMinutes=30)),  # no eventDurationMinutes
        )
        response = handler(event, mock_context)
        body = json.loads(response["body"])

        assert body["eventDurationMinutes"] == 30
        saved = mock_put_poll.call_args[0][0]
        assert saved["eventDurationMinutes"] == 30
