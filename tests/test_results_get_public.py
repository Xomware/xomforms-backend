"""
Tests for lambdas/results_get_public/handler.py -- GET /results/get-public
(public, respondent-facing view, guests included). Allowed only when the
poll's showResultsToRespondents flag is true.

Written RED-first per Phase 2 of docs/features/xomforms/PLAN.md.
"""

from unittest.mock import patch


class TestResultsGetPublicHandler:
    @patch("lambdas.results_get_public.handler.compute_overlap")
    @patch("lambdas.results_get_public.handler.get_poll")
    def test_allowed_when_show_results_to_respondents_true(self, mock_get_poll, mock_compute, mock_context, public_event):
        from lambdas.results_get_public.handler import handler

        mock_get_poll.return_value = {"pollId": "poll-1", "showResultsToRespondents": True}
        mock_compute.return_value = {"pollId": "poll-1", "totalRespondents": 0, "blocks": [], "bestBlockIds": []}

        event = public_event(httpMethod="GET", path="/results/get-public", queryStringParameters={"pollId": "poll-1"})
        response = handler(event, mock_context)

        assert response["statusCode"] == 200
        mock_compute.assert_called_once_with("poll-1")

    @patch("lambdas.results_get_public.handler.compute_overlap")
    @patch("lambdas.results_get_public.handler.get_poll")
    def test_rejected_with_403_when_show_results_to_respondents_false(self, mock_get_poll, mock_compute, mock_context, public_event):
        """The default -- showResultsToRespondents defaults off per the
        plan's Assumptions, so this route is closed unless the creator
        explicitly opted in."""
        from lambdas.results_get_public.handler import handler

        mock_get_poll.return_value = {"pollId": "poll-1", "showResultsToRespondents": False}
        event = public_event(httpMethod="GET", path="/results/get-public", queryStringParameters={"pollId": "poll-1"})
        response = handler(event, mock_context)

        assert response["statusCode"] == 403
        mock_compute.assert_not_called()

    @patch("lambdas.results_get_public.handler.compute_overlap")
    @patch("lambdas.results_get_public.handler.get_poll")
    def test_unknown_poll_returns_404(self, mock_get_poll, mock_compute, mock_context, public_event):
        from lambdas.results_get_public.handler import handler

        mock_get_poll.return_value = None
        event = public_event(httpMethod="GET", path="/results/get-public", queryStringParameters={"pollId": "nope"})
        response = handler(event, mock_context)

        assert response["statusCode"] == 404

    @patch("lambdas.results_get_public.handler.compute_overlap")
    @patch("lambdas.results_get_public.handler.get_poll")
    def test_no_authorizer_context_required(self, mock_get_poll, mock_compute, mock_context, public_event):
        """Guests reach this route with zero identity context -- confirms
        the handler never touches get_caller_email."""
        from lambdas.results_get_public.handler import handler

        mock_get_poll.return_value = {"pollId": "poll-1", "showResultsToRespondents": True}
        mock_compute.return_value = {}
        event = public_event(httpMethod="GET", path="/results/get-public", queryStringParameters={"pollId": "poll-1"})
        assert event["requestContext"] == {}

        response = handler(event, mock_context)
        assert response["statusCode"] == 200
