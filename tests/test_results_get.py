"""
Tests for lambdas/results_get/handler.py -- GET /results/get (authed --
creator's own dashboard view).

Written RED-first per Phase 2 of docs/features/xomforms/PLAN.md. Split from
a single public results route into this authed creator-only route + the
separate public lambdas/results_get_public/handler.py -- see the comment
block at the top of xomforms-infrastructure/terraform/lambda.tf for why.
"""

import json
from unittest.mock import patch


class TestResultsGetHandler:
    @patch("lambdas.results_get.handler.compute_overlap")
    @patch("lambdas.results_get.handler.get_poll")
    def test_creator_can_always_view_own_results(self, mock_get_poll, mock_compute, mock_context, authorized_event):
        from lambdas.results_get.handler import handler

        mock_get_poll.return_value = {"pollId": "poll-1", "creatorEmail": "creator@example.com", "showResultsToRespondents": False}
        mock_compute.return_value = {"pollId": "poll-1", "totalRespondents": 0, "blocks": [], "bestBlockIds": []}

        event = authorized_event(
            email="creator@example.com", httpMethod="GET", path="/results/get", queryStringParameters={"pollId": "poll-1"}
        )
        response = handler(event, mock_context)

        assert response["statusCode"] == 200
        mock_compute.assert_called_once_with("poll-1")

    @patch("lambdas.results_get.handler.compute_overlap")
    @patch("lambdas.results_get.handler.get_poll")
    def test_non_creator_caller_gets_403_even_when_authed(self, mock_get_poll, mock_compute, mock_context, authorized_event):
        """This route is CUSTOM-authed but creator-only -- any other authed
        caller (not the poll's creatorEmail) is still rejected here. The
        respondent-facing view lives on results_get_public instead."""
        from lambdas.results_get.handler import handler

        mock_get_poll.return_value = {"pollId": "poll-1", "creatorEmail": "creator@example.com", "showResultsToRespondents": True}
        event = authorized_event(
            email="someone-else@example.com", httpMethod="GET", path="/results/get", queryStringParameters={"pollId": "poll-1"}
        )
        response = handler(event, mock_context)

        assert response["statusCode"] == 403
        mock_compute.assert_not_called()

    @patch("lambdas.results_get.handler.compute_overlap")
    @patch("lambdas.results_get.handler.get_poll")
    def test_missing_caller_identity_returns_401(self, mock_get_poll, mock_compute, mock_context, public_event):
        from lambdas.results_get.handler import handler

        event = public_event(httpMethod="GET", path="/results/get", queryStringParameters={"pollId": "poll-1"})
        response = handler(event, mock_context)

        assert response["statusCode"] == 401
        mock_compute.assert_not_called()

    @patch("lambdas.results_get.handler.compute_overlap")
    @patch("lambdas.results_get.handler.get_poll")
    def test_unknown_poll_returns_404(self, mock_get_poll, mock_compute, mock_context, authorized_event):
        from lambdas.results_get.handler import handler

        mock_get_poll.return_value = None
        event = authorized_event(httpMethod="GET", path="/results/get", queryStringParameters={"pollId": "nope"})
        response = handler(event, mock_context)

        assert response["statusCode"] == 404
