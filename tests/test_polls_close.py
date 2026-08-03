"""
Tests for lambdas/polls_close/handler.py -- POST /polls/close (authed, creator only).

Closing is the non-destructive counterpart to delete: responses survive, and
the action is reversible via {"reopen": true}.
"""

import json
from unittest.mock import patch

import pytest


@pytest.fixture
def owned_poll():
    return {"pollId": "poll-1", "creatorEmail": "creator@example.com", "title": "Draft night"}


class TestPollsCloseHandler:
    @patch("lambdas.polls_close.handler.set_poll_close_at")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_closing_stamps_close_at(
        self, mock_get, mock_set, mock_context, authorized_event, owned_poll
    ):
        from lambdas.polls_close.handler import handler

        mock_get.return_value = owned_poll
        event = authorized_event(
            email="creator@example.com",
            httpMethod="POST",
            path="/polls/close",
            body=json.dumps({"pollId": "poll-1"}),
        )

        response = handler(event, mock_context)

        assert response["statusCode"] == 200
        poll_id, close_at = mock_set.call_args[0]
        assert poll_id == "poll-1"
        assert close_at is not None
        # Echoed back so the dashboard can re-derive status without a re-read.
        body = json.loads(response["body"])
        assert body["closeAt"] == close_at

    @patch("lambdas.polls_close.handler.set_poll_close_at")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_reopening_clears_close_at(
        self, mock_get, mock_set, mock_context, authorized_event, owned_poll
    ):
        from lambdas.polls_close.handler import handler

        mock_get.return_value = {**owned_poll, "closeAt": "2026-01-01T00:00:00Z"}
        event = authorized_event(
            httpMethod="POST",
            path="/polls/close",
            body=json.dumps({"pollId": "poll-1", "reopen": True}),
        )

        response = handler(event, mock_context)

        assert response["statusCode"] == 200
        mock_set.assert_called_once_with("poll-1", None)
        # The echoed poll must not still carry the old closeAt, or the client
        # would render the row as closed right after reopening it.
        body = json.loads(response["body"])
        assert "closeAt" not in body

    @patch("lambdas.polls_close.handler.set_poll_close_at")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_non_creator_gets_403(
        self, mock_get, mock_set, mock_context, authorized_event, owned_poll
    ):
        from lambdas.polls_close.handler import handler

        mock_get.return_value = owned_poll
        event = authorized_event(
            email="intruder@example.com",
            httpMethod="POST",
            path="/polls/close",
            body=json.dumps({"pollId": "poll-1"}),
        )

        response = handler(event, mock_context)

        assert response["statusCode"] == 403
        mock_set.assert_not_called()

    @patch("lambdas.polls_close.handler.set_poll_close_at")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_unknown_poll_returns_404(self, mock_get, mock_set, mock_context, authorized_event):
        from lambdas.polls_close.handler import handler

        mock_get.return_value = None
        event = authorized_event(
            httpMethod="POST", path="/polls/close", body=json.dumps({"pollId": "nope"})
        )

        response = handler(event, mock_context)

        assert response["statusCode"] == 404
        mock_set.assert_not_called()

    @patch("lambdas.polls_close.handler.set_poll_close_at")
    def test_missing_caller_identity_returns_401(self, mock_set, mock_context, public_event):
        from lambdas.polls_close.handler import handler

        event = public_event(
            httpMethod="POST", path="/polls/close", body=json.dumps({"pollId": "poll-1"})
        )

        response = handler(event, mock_context)

        assert response["statusCode"] == 401
        mock_set.assert_not_called()

    @patch("lambdas.polls_close.handler.set_poll_close_at")
    def test_missing_poll_id_returns_400(self, mock_set, mock_context, authorized_event):
        from lambdas.polls_close.handler import handler

        event = authorized_event(httpMethod="POST", path="/polls/close", body=json.dumps({}))

        response = handler(event, mock_context)

        assert response["statusCode"] == 400
        mock_set.assert_not_called()
