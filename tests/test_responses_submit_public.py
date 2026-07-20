"""
Tests for lambdas/responses_submit_public/handler.py -- POST /responses/submit-guest
(guest submit, keyed by guest#<uuid>, gated by guestAllowed).

Written RED-first per Phase 2 of docs/features/xomforms/PLAN.md.
"""

import json
from unittest.mock import patch


class TestResponsesSubmitPublicHandler:
    @patch("lambdas.responses_submit_public.handler.submit_availability")
    @patch("lambdas.responses_submit_public.handler.get_poll")
    def test_submits_as_guest_when_guest_allowed(self, mock_get_poll, mock_submit, mock_context, public_event):
        from lambdas.responses_submit_public.handler import handler

        mock_get_poll.return_value = {"pollId": "poll-1", "guestAllowed": True}
        mock_submit.return_value = {"pollId": "poll-1", "respondentKey": "guest#abc", "blocks": []}

        event = public_event(
            httpMethod="POST",
            path="/responses/submit-guest",
            body=json.dumps({"pollId": "poll-1", "displayName": "Guest Dom", "blocks": [], "guestId": "abc"}),
        )
        response = handler(event, mock_context)

        assert response["statusCode"] == 200
        mock_submit.assert_called_once_with(
            {"pollId": "poll-1", "guestAllowed": True},
            respondent_key="guest#abc",
            display_name="Guest Dom",
            blocks=[],
        )

    @patch("lambdas.responses_submit_public.handler.submit_availability")
    @patch("lambdas.responses_submit_public.handler.get_poll")
    def test_mints_a_guest_id_when_none_provided(self, mock_get_poll, mock_submit, mock_context, public_event):
        from lambdas.responses_submit_public.handler import handler

        mock_get_poll.return_value = {"pollId": "poll-1", "guestAllowed": True}
        mock_submit.return_value = {}

        event = public_event(
            httpMethod="POST",
            path="/responses/submit-guest",
            body=json.dumps({"pollId": "poll-1", "displayName": "Guest Dom", "blocks": []}),
        )
        response = handler(event, mock_context)

        assert response["statusCode"] == 200
        _, kwargs = mock_submit.call_args
        assert kwargs["respondent_key"].startswith("guest#")
        assert len(kwargs["respondent_key"]) > len("guest#")

    @patch("lambdas.responses_submit_public.handler.submit_availability")
    @patch("lambdas.responses_submit_public.handler.get_poll")
    def test_rejects_with_403_when_guest_not_allowed(self, mock_get_poll, mock_submit, mock_context, public_event):
        """The guestAllowed gate -- the one real deviation from xomify's
        fully-authed setup, called out explicitly in the plan's Risks."""
        from lambdas.responses_submit_public.handler import handler

        mock_get_poll.return_value = {"pollId": "poll-1", "guestAllowed": False}
        event = public_event(
            httpMethod="POST",
            path="/responses/submit-guest",
            body=json.dumps({"pollId": "poll-1", "displayName": "Guest Dom", "blocks": []}),
        )
        response = handler(event, mock_context)

        assert response["statusCode"] == 403
        mock_submit.assert_not_called()

    @patch("lambdas.responses_submit_public.handler.submit_availability")
    @patch("lambdas.responses_submit_public.handler.get_poll")
    def test_unknown_poll_returns_404(self, mock_get_poll, mock_submit, mock_context, public_event):
        from lambdas.responses_submit_public.handler import handler

        mock_get_poll.return_value = None
        event = public_event(
            httpMethod="POST",
            path="/responses/submit-guest",
            body=json.dumps({"pollId": "does-not-exist", "displayName": "Guest Dom", "blocks": []}),
        )
        response = handler(event, mock_context)

        assert response["statusCode"] == 404
        mock_submit.assert_not_called()

    @patch("lambdas.responses_submit_public.handler.submit_availability")
    @patch("lambdas.responses_submit_public.handler.get_poll")
    def test_no_authorizer_context_required(self, mock_get_poll, mock_submit, mock_context, public_event):
        from lambdas.responses_submit_public.handler import handler

        mock_get_poll.return_value = {"pollId": "poll-1", "guestAllowed": True}
        mock_submit.return_value = {}
        event = public_event(
            httpMethod="POST",
            path="/responses/submit-guest",
            body=json.dumps({"pollId": "poll-1", "displayName": "Guest Dom", "blocks": []}),
        )
        assert event["requestContext"] == {}

        response = handler(event, mock_context)
        assert response["statusCode"] == 200
