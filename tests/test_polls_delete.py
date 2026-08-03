"""
Tests for lambdas/polls_delete/handler.py -- POST /polls/delete (authed, creator only).

The security-critical property here: polls_get is a PUBLIC route, so any pollId
is discoverable by anyone holding a share link. Naming a poll must never imply
being able to destroy it.
"""

import json
from unittest.mock import patch

import pytest


@pytest.fixture
def owned_poll():
    return {"pollId": "poll-1", "creatorEmail": "creator@example.com", "title": "Draft night"}


class TestPollsDeleteHandler:
    @patch("lambdas.polls_delete.handler.delete_poll")
    @patch("lambdas.polls_delete.handler.delete_responses_for_poll")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_deletes_poll_and_cascades_responses(
        self, mock_get, mock_del_responses, mock_del_poll, mock_context, authorized_event, owned_poll
    ):
        from lambdas.polls_delete.handler import handler

        mock_get.return_value = owned_poll
        mock_del_responses.return_value = 3
        event = authorized_event(
            email="creator@example.com",
            httpMethod="POST",
            path="/polls/delete",
            body=json.dumps({"pollId": "poll-1"}),
        )

        response = handler(event, mock_context)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["pollId"] == "poll-1"
        assert body["deletedResponses"] == 3
        mock_del_responses.assert_called_once_with("poll-1")
        mock_del_poll.assert_called_once_with("poll-1")

    @patch("lambdas.polls_delete.handler.delete_poll")
    @patch("lambdas.polls_delete.handler.delete_responses_for_poll")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_responses_are_deleted_before_the_poll(
        self, mock_get, mock_del_responses, mock_del_poll, mock_context, authorized_event, owned_poll
    ):
        """
        Ordering matters: if the poll row went first and the cascade then failed,
        its responses would be orphaned with nothing left pointing at them.
        """
        from lambdas.polls_delete.handler import handler

        mock_get.return_value = owned_poll
        calls = []
        mock_del_responses.side_effect = lambda pid: calls.append("responses") or 0
        mock_del_poll.side_effect = lambda pid: calls.append("poll")

        event = authorized_event(
            httpMethod="POST", path="/polls/delete", body=json.dumps({"pollId": "poll-1"})
        )
        handler(event, mock_context)

        assert calls == ["responses", "poll"]

    @patch("lambdas.polls_delete.handler.delete_poll")
    @patch("lambdas.polls_delete.handler.delete_responses_for_poll")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_non_creator_gets_403_and_nothing_is_touched(
        self, mock_get, mock_del_responses, mock_del_poll, mock_context, authorized_event, owned_poll
    ):
        from lambdas.polls_delete.handler import handler

        mock_get.return_value = owned_poll
        event = authorized_event(
            email="someone-else@example.com",
            httpMethod="POST",
            path="/polls/delete",
            body=json.dumps({"pollId": "poll-1"}),
        )

        response = handler(event, mock_context)

        assert response["statusCode"] == 403
        mock_del_responses.assert_not_called()
        mock_del_poll.assert_not_called()

    @patch("lambdas.polls_delete.handler.delete_poll")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_unknown_poll_returns_404(
        self, mock_get, mock_del_poll, mock_context, authorized_event
    ):
        from lambdas.polls_delete.handler import handler

        mock_get.return_value = None
        event = authorized_event(
            httpMethod="POST", path="/polls/delete", body=json.dumps({"pollId": "nope"})
        )

        response = handler(event, mock_context)

        assert response["statusCode"] == 404
        mock_del_poll.assert_not_called()

    @patch("lambdas.polls_delete.handler.delete_poll")
    def test_missing_caller_identity_returns_401(self, mock_del_poll, mock_context, public_event):
        from lambdas.polls_delete.handler import handler

        event = public_event(
            httpMethod="POST", path="/polls/delete", body=json.dumps({"pollId": "poll-1"})
        )

        response = handler(event, mock_context)

        assert response["statusCode"] == 401
        mock_del_poll.assert_not_called()

    @patch("lambdas.polls_delete.handler.delete_poll")
    def test_missing_poll_id_returns_400(self, mock_del_poll, mock_context, authorized_event):
        from lambdas.polls_delete.handler import handler

        event = authorized_event(httpMethod="POST", path="/polls/delete", body=json.dumps({}))

        response = handler(event, mock_context)

        assert response["statusCode"] == 400
        mock_del_poll.assert_not_called()
