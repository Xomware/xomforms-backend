"""
Tests for lambdas/polls_get/handler.py -- GET /polls/get (public read).

Written RED-first per Phase 2 of docs/features/xomforms/PLAN.md.
"""

from unittest.mock import patch


class TestPollsGetHandler:
    @patch("lambdas.polls_get.handler.get_poll")
    def test_returns_poll_when_found(self, mock_get_poll, mock_context, public_event):
        from lambdas.polls_get.handler import handler

        mock_get_poll.return_value = {"pollId": "poll-1", "title": "Fantasy Draft"}
        event = public_event(httpMethod="GET", path="/polls/get", queryStringParameters={"pollId": "poll-1"})

        response = handler(event, mock_context)
        assert response["statusCode"] == 200
        mock_get_poll.assert_called_once_with("poll-1")

    @patch("lambdas.polls_get.handler.get_poll")
    def test_returns_404_when_poll_missing(self, mock_get_poll, mock_context, public_event):
        from lambdas.polls_get.handler import handler

        mock_get_poll.return_value = None
        event = public_event(httpMethod="GET", path="/polls/get", queryStringParameters={"pollId": "nope"})

        response = handler(event, mock_context)
        assert response["statusCode"] == 404

    @patch("lambdas.polls_get.handler.get_poll")
    def test_returns_400_when_poll_id_missing(self, mock_get_poll, mock_context, public_event):
        from lambdas.polls_get.handler import handler

        event = public_event(httpMethod="GET", path="/polls/get", queryStringParameters={})
        response = handler(event, mock_context)

        assert response["statusCode"] == 400
        mock_get_poll.assert_not_called()

    @patch("lambdas.polls_get.handler.get_poll")
    def test_no_authorizer_context_required(self, mock_get_poll, mock_context, public_event):
        """Sanity: this route works with zero identity context at all (public)."""
        from lambdas.polls_get.handler import handler

        mock_get_poll.return_value = {"pollId": "poll-1"}
        event = public_event(httpMethod="GET", path="/polls/get", queryStringParameters={"pollId": "poll-1"})
        assert event["requestContext"] == {}

        response = handler(event, mock_context)
        assert response["statusCode"] == 200
