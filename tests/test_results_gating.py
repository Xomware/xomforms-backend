"""
Tests for the results visibility gate in lambdas/results_get_public.

This gate is security-critical and is why it lives server-side: blurring
results in the UI is a CSS trick, and the JSON is one devtools tab away. On an
availability poll that leaks exactly who is free when.
"""

import json
from unittest.mock import patch

import pytest


def _poll(**overrides):
    poll = {
        "pollId": "poll-1",
        "creatorEmail": "creator@example.com",
        "title": "Draft night",
        "formType": "scheduler",
    }
    poll.update(overrides)
    return poll


class TestResolveResultsVisibility:
    """Read-time shim for polls created before resultsVisibility existed."""

    def test_explicit_value_wins(self):
        from lambdas.common.models import resolve_results_visibility

        assert resolve_results_visibility({"resultsVisibility": "after_response"}) == "after_response"

    def test_legacy_true_maps_to_always(self):
        from lambdas.common.models import resolve_results_visibility

        assert resolve_results_visibility({"showResultsToRespondents": True}) == "always"

    def test_legacy_false_maps_to_hidden(self):
        from lambdas.common.models import resolve_results_visibility

        assert resolve_results_visibility({"showResultsToRespondents": False}) == "hidden"

    def test_unknown_stored_value_falls_back_to_legacy(self):
        from lambdas.common.models import resolve_results_visibility

        poll = {"resultsVisibility": "bogus", "showResultsToRespondents": True}
        assert resolve_results_visibility(poll) == "always"


class TestResultsGate:
    @patch("lambdas.results_get_public.handler.compute_overlap")
    @patch("lambdas.results_get_public.handler.get_poll")
    def test_hidden_is_refused(self, mock_get, mock_overlap, mock_context, public_event):
        from lambdas.results_get_public.handler import handler

        mock_get.return_value = _poll(resultsVisibility="hidden")
        event = public_event(path="/results/get-public", queryStringParameters={"pollId": "poll-1"})

        response = handler(event, mock_context)

        assert response["statusCode"] == 403
        mock_overlap.assert_not_called()

    @patch("lambdas.results_get_public.handler.compute_overlap")
    @patch("lambdas.results_get_public.handler.get_poll")
    def test_always_is_open(self, mock_get, mock_overlap, mock_context, public_event):
        from lambdas.results_get_public.handler import handler

        mock_get.return_value = _poll(resultsVisibility="always")
        mock_overlap.return_value = {"blocks": []}
        event = public_event(path="/results/get-public", queryStringParameters={"pollId": "poll-1"})

        assert handler(event, mock_context)["statusCode"] == 200

    @patch("lambdas.results_get_public.handler.has_responded")
    @patch("lambdas.results_get_public.handler.compute_overlap")
    @patch("lambdas.results_get_public.handler.get_poll")
    def test_after_response_refuses_a_non_respondent(
        self, mock_get, mock_overlap, mock_responded, mock_context, public_event
    ):
        from lambdas.results_get_public.handler import handler

        mock_get.return_value = _poll(resultsVisibility="after_response")
        mock_responded.return_value = False
        event = public_event(
            path="/results/get-public",
            queryStringParameters={"pollId": "poll-1", "guestId": "abc"},
        )

        response = handler(event, mock_context)

        assert response["statusCode"] == 403
        # The payload must never be computed, let alone returned.
        mock_overlap.assert_not_called()

    @patch("lambdas.results_get_public.handler.has_responded")
    @patch("lambdas.results_get_public.handler.compute_overlap")
    @patch("lambdas.results_get_public.handler.get_poll")
    def test_after_response_allows_a_respondent(
        self, mock_get, mock_overlap, mock_responded, mock_context, public_event
    ):
        from lambdas.results_get_public.handler import handler

        mock_get.return_value = _poll(resultsVisibility="after_response")
        mock_responded.return_value = True
        mock_overlap.return_value = {"blocks": []}
        event = public_event(
            path="/results/get-public",
            queryStringParameters={"pollId": "poll-1", "guestId": "abc"},
        )

        assert handler(event, mock_context)["statusCode"] == 200
        mock_responded.assert_called_once_with("poll-1", "guest#abc")

    @patch("lambdas.results_get_public.handler.compute_overlap")
    @patch("lambdas.results_get_public.handler.get_poll")
    def test_after_response_refuses_when_no_identity_is_offered(
        self, mock_get, mock_overlap, mock_context, public_event
    ):
        """No guestId and no email means the caller cannot have responded."""
        from lambdas.results_get_public.handler import handler

        mock_get.return_value = _poll(resultsVisibility="after_response")
        event = public_event(path="/results/get-public", queryStringParameters={"pollId": "poll-1"})

        assert handler(event, mock_context)["statusCode"] == 403
        mock_overlap.assert_not_called()

    @patch("lambdas.results_get_public.handler.has_responded")
    @patch("lambdas.results_get_public.handler.compute_overlap")
    @patch("lambdas.results_get_public.handler.get_poll")
    def test_legacy_poll_without_the_setting_is_still_gated(
        self, mock_get, mock_overlap, mock_responded, mock_context, public_event
    ):
        """A pre-setting poll with the flag off must stay closed."""
        from lambdas.results_get_public.handler import handler

        mock_get.return_value = _poll(showResultsToRespondents=False)
        event = public_event(path="/results/get-public", queryStringParameters={"pollId": "poll-1"})

        assert handler(event, mock_context)["statusCode"] == 403
        mock_overlap.assert_not_called()
