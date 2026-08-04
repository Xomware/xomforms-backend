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


class TestInviteDetails:
    """
    The details block is the one placeholder holding real markup, so it is
    substituted after the escaping pass -- worth pinning down.
    """

    POLL = {
        "eventDurationMinutes": 180,
        "earliestStartMinute": 18 * 60,
        "latestStartMinute": 21 * 60,
        "startDate": "2026-08-09",
        "endDate": "2026-08-16",
        "timezone": "America/New_York",
    }

    def test_states_that_the_recipient_picks_a_start_time(self):
        from lambdas.common.email_helpers import render_invite

        html_body, text_body = render_invite("Sam", "Dom", "Draft night", "p1", self.POLL)
        assert "start time" in html_body
        assert "6:00 PM - 9:00 PM" in text_body

    def test_includes_the_event_length(self):
        from lambdas.common.email_helpers import render_invite

        _, text_body = render_invite("Sam", "Dom", "Draft night", "p1", self.POLL)
        assert "3 hours" in text_body

    def test_uses_the_hosted_logo(self):
        from lambdas.common.email_helpers import render_invite

        html_body, _ = render_invite("Sam", "Dom", "Draft night", "p1", self.POLL)
        # Not a data: URI -- Gmail and Outlook both drop those.
        assert "/assets/xomforms-banner.png" in html_body
        assert "data:image" not in html_body

    def test_escapes_values_inside_the_details_block(self):
        from lambdas.common.email_helpers import render_invite

        html_body, _ = render_invite(
            "Sam", "Dom", "Draft", "p1", {**self.POLL, "timezone": "<script>x</script>"}
        )
        assert "<script>" not in html_body

    def test_omits_the_block_entirely_for_a_form_with_no_schedule(self):
        """A Q&A form has no start range; an empty styled box would look broken."""
        from lambdas.common.email_helpers import render_invite

        html_body, text_body = render_invite("Sam", "Dom", "RSVP", "p1", None)
        assert "{{detailsBlock}}" not in html_body
        assert "{{detailsText}}" not in text_body
        assert "Event length" not in html_body

    def test_leaves_no_unsubstituted_tokens(self):
        from lambdas.common.email_helpers import render_invite

        html_body, text_body = render_invite("Sam", "Dom", "Draft night", "p1", self.POLL)
        assert "{{" not in html_body
        assert "{{" not in text_body


class TestInviteInstructions:
    def test_includes_the_organizers_note(self):
        from lambdas.common.email_helpers import render_invite

        poll = {"instructions": "Only pick slots you can commit to all season."}
        html_body, text_body = render_invite("Sam", "Dom", "League", "p1", poll)
        assert "all season" in html_body
        assert "all season" in text_body
        assert "note from the organizer" in text_body.lower()

    def test_escapes_the_note(self):
        """Creator free text landing inside email markup."""
        from lambdas.common.email_helpers import render_invite

        html_body, _ = render_invite(
            "Sam", "Dom", "League", "p1", {"instructions": "<script>alert(1)</script>"}
        )
        assert "<script>" not in html_body
        assert "&lt;script&gt;" in html_body

    def test_keeps_line_breaks_the_creator_typed(self):
        from lambdas.common.email_helpers import render_invite

        html_body, _ = render_invite("Sam", "Dom", "League", "p1", {"instructions": "one\ntwo"})
        assert "one<br />two" in html_body

    def test_omits_the_block_when_there_is_no_note(self):
        from lambdas.common.email_helpers import render_invite

        html_body, text_body = render_invite("Sam", "Dom", "League", "p1", {"instructions": "   "})
        assert "note from the organizer" not in html_body.lower()
        assert "{{instructionsBlock}}" not in html_body
        assert "{{instructionsText}}" not in text_body


class TestInviteMarkupHealth:
    """
    Cheap structural guards. An email is fire-and-forget -- there's no error
    surface once it lands in someone's inbox, so the obvious breakages are
    worth pinning.
    """

    POLL = {
        "eventDurationMinutes": 180,
        "earliestStartMinute": 18 * 60,
        "latestStartMinute": 21 * 60,
        "startDate": "2026-08-09",
        "endDate": "2026-08-16",
        "timezone": "America/New_York",
        "instructions": "Season commitment only.",
    }

    def _html(self):
        from lambdas.common.email_helpers import render_invite

        return render_invite("Sam", "Dom", "Draft night", "p1", self.POLL)[0]

    def test_tags_are_balanced(self):
        html_body = self._html()
        for tag in ("table", "tr", "td", "html", "body"):
            assert html_body.count(f"<{tag}") == html_body.count(f"</{tag}>"), tag

    def test_images_carry_dimensions_and_alt(self):
        """Outlook collapses images with no width/height; alt covers blocking."""
        html_body = self._html()
        for img in [seg for seg in html_body.split("<img ")[1:]]:
            head = img[: img.index(">")]
            assert "width=" in head and "height=" in head
            assert "alt=" in head

    def test_cta_points_at_the_form(self):
        html_body = self._html()
        assert html_body.count("/f/p1") >= 2  # button + fallback link

    def test_no_external_css_or_script(self):
        html_body = self._html()
        assert "<script" not in html_body.lower()
        assert "stylesheet" not in html_body.lower()


class TestInviteLocation:
    def test_in_person_shows_venue_and_address(self):
        from lambdas.common.email_helpers import render_invite

        poll = {
            "locationType": "in_person",
            "locationName": "Fenway Park",
            "locationAddress": "4 Jersey St, Boston, MA",
        }
        _, text_body = render_invite("Sam", "Dom", "Draft", "p1", poll)
        assert "Fenway Park" in text_body
        assert "4 Jersey St" in text_body

    def test_address_alone_is_enough(self):
        from lambdas.common.email_helpers import render_invite

        poll = {"locationType": "in_person", "locationAddress": "4 Jersey St, Boston, MA"}
        _, text_body = render_invite("Sam", "Dom", "Draft", "p1", poll)
        assert "4 Jersey St" in text_body

    def test_virtual_says_online(self):
        from lambdas.common.email_helpers import render_invite

        _, text_body = render_invite("Sam", "Dom", "Draft", "p1", {"locationType": "virtual"})
        assert "Online" in text_body

    def test_no_location_claims_nothing(self):
        """Unstated is not the same as virtual."""
        from lambdas.common.email_helpers import render_invite

        _, text_body = render_invite("Sam", "Dom", "Draft", "p1", {"eventDurationMinutes": 60})
        assert "Online" not in text_body
        assert "Where" not in text_body

    def test_escapes_a_hostile_venue_name(self):
        from lambdas.common.email_helpers import render_invite

        poll = {"locationType": "in_person", "locationName": "<script>x</script>"}
        html_body, _ = render_invite("Sam", "Dom", "Draft", "p1", poll)
        assert "<script>" not in html_body
