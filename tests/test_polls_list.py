"""
Tests for lambdas/polls_list/handler.py -- GET /polls/list (authed, creator GSI).

Written RED-first per Phase 2 of docs/features/xomforms/PLAN.md.
"""

import json
from unittest.mock import patch


class TestPollsListHandler:
    @patch("lambdas.polls_list.handler.query_polls_by_creator")
    def test_returns_callers_polls(self, mock_query, mock_context, authorized_event):
        from lambdas.polls_list.handler import handler

        mock_query.return_value = [{"pollId": "poll-1"}, {"pollId": "poll-2"}]
        event = authorized_event(email="creator@example.com", httpMethod="GET", path="/polls/list")

        response = handler(event, mock_context)
        assert response["statusCode"] == 200

        body = json.loads(response["body"])
        assert len(body["polls"]) == 2
        mock_query.assert_called_once_with("creator@example.com")

    @patch("lambdas.polls_list.handler.query_polls_by_creator")
    def test_missing_caller_identity_returns_401(self, mock_query, mock_context, public_event):
        from lambdas.polls_list.handler import handler

        event = public_event(httpMethod="GET", path="/polls/list")
        response = handler(event, mock_context)

        assert response["statusCode"] == 401
        mock_query.assert_not_called()

    @patch("lambdas.polls_list.handler.query_polls_by_creator")
    def test_empty_list_for_creator_with_no_polls(self, mock_query, mock_context, authorized_event):
        from lambdas.polls_list.handler import handler

        mock_query.return_value = []
        event = authorized_event(httpMethod="GET", path="/polls/list")

        response = handler(event, mock_context)
        body = json.loads(response["body"])
        assert body["polls"] == []
