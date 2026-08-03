"""
Tests for invite rendering + send (lambdas/common/email_helpers, lambdas/invites_send).
"""

import json
from unittest.mock import patch

import pytest


OWNED = {"pollId": "poll-1", "creatorEmail": "creator@example.com", "title": "Draft night"}


class TestRenderInvite:
    def test_html_escapes_creator_supplied_values(self):
        """
        A form title is arbitrary creator input landing inside email markup.
        Unescaped, a title containing a tag injects into every invite sent.
        """
        from lambdas.common.email_helpers import render_invite

        html_body, _ = render_invite(None, "Dom", "<script>alert(1)</script>", "poll-1")

        assert "<script>" not in html_body
        assert "&lt;script&gt;" in html_body

    def test_falls_back_to_a_neutral_greeting(self):
        from lambdas.common.email_helpers import render_invite

        _, text_body = render_invite(None, "Dom", "Draft night", "poll-1")
        assert "Hi there," in text_body

    def test_uses_the_supplied_recipient_name(self):
        from lambdas.common.email_helpers import render_invite

        _, text_body = render_invite("Sam", "Dom", "Draft night", "poll-1")
        assert "Hi Sam," in text_body

    def test_substitutes_every_placeholder(self):
        """A leftover {{token}} in a sent email is a visible bug."""
        from lambdas.common.email_helpers import render_invite

        html_body, text_body = render_invite("Sam", "Dom", "Draft night", "poll-1")
        assert "{{" not in html_body
        assert "{{" not in text_body

    def test_links_to_the_public_respond_url(self):
        from lambdas.common.email_helpers import render_invite

        _, text_body = render_invite("Sam", "Dom", "Draft night", "poll-1")
        assert "/f/poll-1" in text_body


class TestInvitesSend:
    @patch("lambdas.invites_send.handler.update_poll_attributes")
    @patch("lambdas.invites_send.handler.send_invite")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_sends_one_message_per_recipient(
        self, mock_get, mock_send, mock_update, mock_context, authorized_event
    ):
        """
        Individually, never one message with everyone on it -- a shared To:
        line would disclose the whole invitee list to every recipient.
        """
        from lambdas.invites_send.handler import handler

        mock_get.return_value = OWNED
        event = authorized_event(
            httpMethod="POST",
            path="/invites/send",
            body=json.dumps({"pollId": "poll-1", "recipients": ["a@x.com", "b@x.com"]}),
        )

        response = handler(event, mock_context)

        assert response["statusCode"] == 200
        assert mock_send.call_count == 2
        body = json.loads(response["body"])
        assert body["sent"] == 2
        assert body["failed"] == 0

    @patch("lambdas.invites_send.handler.update_poll_attributes")
    @patch("lambdas.invites_send.handler.send_invite")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_one_bad_address_does_not_abort_the_batch(
        self, mock_get, mock_send, mock_update, mock_context, authorized_event
    ):
        from lambdas.invites_send.handler import handler

        mock_get.return_value = OWNED
        mock_send.side_effect = [None, Exception("SES said no"), None]
        event = authorized_event(
            httpMethod="POST",
            path="/invites/send",
            body=json.dumps({"pollId": "poll-1", "recipients": ["a@x.com", "b@x.com", "c@x.com"]}),
        )

        response = handler(event, mock_context)
        body = json.loads(response["body"])

        assert response["statusCode"] == 200
        assert body["sent"] == 2
        assert body["failed"] == 1
        failed = [r for r in body["results"] if r["status"] == "failed"]
        # The creator has to be able to see WHICH one failed.
        assert failed[0]["email"] == "b@x.com"

    @patch("lambdas.invites_send.handler.send_invite")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_non_creator_gets_403(self, mock_get, mock_send, mock_context, authorized_event):
        from lambdas.invites_send.handler import handler

        mock_get.return_value = OWNED
        event = authorized_event(
            email="intruder@example.com",
            httpMethod="POST",
            path="/invites/send",
            body=json.dumps({"pollId": "poll-1", "recipients": ["a@x.com"]}),
        )

        assert handler(event, mock_context)["statusCode"] == 403
        mock_send.assert_not_called()

    @patch("lambdas.invites_send.handler.update_poll_attributes")
    @patch("lambdas.invites_send.handler.send_invite")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_dedupes_repeated_addresses(
        self, mock_get, mock_send, mock_update, mock_context, authorized_event
    ):
        from lambdas.invites_send.handler import handler

        mock_get.return_value = OWNED
        event = authorized_event(
            httpMethod="POST",
            path="/invites/send",
            body=json.dumps({"pollId": "poll-1", "recipients": ["a@x.com", "A@X.com "]}),
        )

        handler(event, mock_context)
        assert mock_send.call_count == 1

    @patch("lambdas.invites_send.handler.send_invite")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_rejects_a_malformed_address(
        self, mock_get, mock_send, mock_context, authorized_event
    ):
        from lambdas.invites_send.handler import handler

        mock_get.return_value = OWNED
        event = authorized_event(
            httpMethod="POST",
            path="/invites/send",
            body=json.dumps({"pollId": "poll-1", "recipients": ["not-an-email"]}),
        )

        assert handler(event, mock_context)["statusCode"] == 400
        mock_send.assert_not_called()

    @patch("lambdas.invites_send.handler.send_invite")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_rejects_an_empty_recipient_list(
        self, mock_get, mock_send, mock_context, authorized_event
    ):
        from lambdas.invites_send.handler import handler

        mock_get.return_value = OWNED
        event = authorized_event(
            httpMethod="POST",
            path="/invites/send",
            body=json.dumps({"pollId": "poll-1", "recipients": []}),
        )

        assert handler(event, mock_context)["statusCode"] == 400
        mock_send.assert_not_called()

    @patch("lambdas.invites_send.handler.send_invite")
    def test_missing_caller_identity_returns_401(self, mock_send, mock_context, public_event):
        from lambdas.invites_send.handler import handler

        event = public_event(
            httpMethod="POST",
            path="/invites/send",
            body=json.dumps({"pollId": "poll-1", "recipients": ["a@x.com"]}),
        )

        assert handler(event, mock_context)["statusCode"] == 401
        mock_send.assert_not_called()


class TestInvitesList:
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_returns_recorded_invites(self, mock_get, mock_context, authorized_event):
        from lambdas.invites_list.handler import handler

        mock_get.return_value = {**OWNED, "invites": [{"email": "a@x.com", "status": "sent"}]}
        event = authorized_event(
            httpMethod="GET", path="/invites/list", queryStringParameters={"pollId": "poll-1"}
        )

        body = json.loads(handler(event, mock_context)["body"])
        assert body["invites"][0]["email"] == "a@x.com"

    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_non_creator_gets_403(self, mock_get, mock_context, authorized_event):
        """An invite list is a list of someone's contacts."""
        from lambdas.invites_list.handler import handler

        mock_get.return_value = OWNED
        event = authorized_event(
            email="intruder@example.com",
            httpMethod="GET",
            path="/invites/list",
            queryStringParameters={"pollId": "poll-1"},
        )

        assert handler(event, mock_context)["statusCode"] == 403
