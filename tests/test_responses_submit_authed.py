"""
Tests for lambdas/responses_submit_authed/handler.py -- POST /responses/submit
(authed respondent upsert, keyed by email).

Written RED-first per Phase 2 of docs/features/xomforms/PLAN.md.
"""

import json
from unittest.mock import patch


def _poll(**overrides):
    poll = {
        "pollId": "poll-1",
        "creatorEmail": "creator@example.com",
        "guestAllowed": True,
    }
    poll.update(overrides)
    return poll


class TestResponsesSubmitAuthedHandler:
    @patch("lambdas.responses_submit_authed.handler.submit_availability")
    @patch("lambdas.responses_submit_authed.handler.get_poll")
    def test_submits_keyed_by_caller_email(self, mock_get_poll, mock_submit, mock_context, authorized_event):
        from lambdas.responses_submit_authed.handler import handler

        mock_get_poll.return_value = _poll()
        mock_submit.return_value = {"pollId": "poll-1", "respondentKey": "dom@example.com", "blocks": []}

        event = authorized_event(
            email="dom@example.com",
            httpMethod="POST",
            path="/responses/submit",
            body=json.dumps({"pollId": "poll-1", "displayName": "Dom", "blocks": ["2026-08-03T08:00"]}),
        )

        response = handler(event, mock_context)
        assert response["statusCode"] == 200
        mock_submit.assert_called_once_with(
            _poll(), respondent_key="dom@example.com", display_name="Dom", blocks=["2026-08-03T08:00"]
        )

    @patch("lambdas.responses_submit_authed.handler.submit_availability")
    @patch("lambdas.responses_submit_authed.handler.get_poll")
    def test_missing_caller_identity_returns_401(self, mock_get_poll, mock_submit, mock_context, public_event):
        from lambdas.responses_submit_authed.handler import handler

        event = public_event(
            httpMethod="POST",
            path="/responses/submit",
            body=json.dumps({"pollId": "poll-1", "displayName": "Dom", "blocks": []}),
        )
        response = handler(event, mock_context)

        assert response["statusCode"] == 401
        mock_submit.assert_not_called()

    @patch("lambdas.responses_submit_authed.handler.submit_availability")
    @patch("lambdas.responses_submit_authed.handler.get_poll")
    def test_unknown_poll_returns_404(self, mock_get_poll, mock_submit, mock_context, authorized_event):
        from lambdas.responses_submit_authed.handler import handler

        mock_get_poll.return_value = None
        event = authorized_event(
            httpMethod="POST",
            path="/responses/submit",
            body=json.dumps({"pollId": "does-not-exist", "displayName": "Dom", "blocks": []}),
        )
        response = handler(event, mock_context)

        assert response["statusCode"] == 404
        mock_submit.assert_not_called()

    @patch("lambdas.responses_submit_authed.handler.submit_availability")
    @patch("lambdas.responses_submit_authed.handler.get_poll")
    def test_missing_display_name_returns_400(self, mock_get_poll, mock_submit, mock_context, authorized_event):
        from lambdas.responses_submit_authed.handler import handler

        mock_get_poll.return_value = _poll()
        event = authorized_event(
            httpMethod="POST",
            path="/responses/submit",
            body=json.dumps({"pollId": "poll-1", "displayName": "  ", "blocks": []}),
        )
        response = handler(event, mock_context)

        assert response["statusCode"] == 400
        mock_submit.assert_not_called()
