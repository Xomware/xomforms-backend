"""
Tests for finalizing a form: the chosen time, the calendar file, and the
notification that goes out.
"""

import json
from unittest.mock import patch

import pytest

POLL = {
    "pollId": "poll-1",
    "creatorEmail": "creator@example.com",
    "title": "Draft Night",
    "timezone": "America/New_York",
    "eventDurationMinutes": 180,
    "granularityMinutes": 30,
}

FINALIZED = {**POLL, "finalBlockId": "2026-08-09T18:30", "finalizedAt": "2026-08-04T12:00:00Z"}


class TestBlockToUtc:
    def test_converts_organizer_wall_clock_to_utc(self):
        """6:30 PM in New York during DST is 22:30 UTC, not 23:30."""
        from lambdas.common.calendar_helpers import block_to_utc

        assert block_to_utc("2026-08-09T18:30", "America/New_York").hour == 22

    def test_respects_the_date_for_dst(self):
        from lambdas.common.calendar_helpers import block_to_utc

        summer = block_to_utc("2026-08-09T18:30", "America/New_York")
        winter = block_to_utc("2026-01-09T18:30", "America/New_York")
        # EST is an hour further from UTC than EDT.
        assert winter.hour == summer.hour + 1

    def test_unknown_zone_does_not_explode(self):
        from lambdas.common.calendar_helpers import block_to_utc

        assert block_to_utc("2026-08-09T18:30", "Mars/Nowhere").hour == 18


class TestBuildIcs:
    def test_uses_crlf_line_endings(self):
        """RFC 5545 requires them; bare \\n breaks strict parsers."""
        from lambdas.common.calendar_helpers import build_ics

        assert "\r\n" in build_ics(FINALIZED)

    def test_event_spans_the_configured_duration(self):
        from lambdas.common.calendar_helpers import build_ics

        ics = build_ics(FINALIZED)
        assert "DTSTART:20260809T223000Z" in ics
        # 3 hours later, rolling past midnight UTC into the next day.
        assert "DTEND:20260810T013000Z" in ics

    def test_escapes_structural_characters(self):
        from lambdas.common.calendar_helpers import build_ics

        ics = build_ics({**FINALIZED, "title": "Draft; Night, 2026"})
        assert "Draft\; Night\\, 2026" in ics

    def test_uid_is_stable_so_re_adding_updates_one_entry(self):
        from lambdas.common.calendar_helpers import build_ics

        assert "UID:poll-1@xomforms.xomware.com" in build_ics(FINALIZED)

    def test_virtual_events_carry_the_link_as_location(self):
        from lambdas.common.calendar_helpers import build_ics

        ics = build_ics({**FINALIZED, "locationType": "virtual", "locationUrl": "https://meet.x/a"})
        assert "LOCATION:https://meet.x/a" in ics


class TestIcsHandler:
    @patch("lambdas.polls_ics.handler.get_poll")
    def test_serves_a_calendar_file(self, mock_get, mock_context, public_event):
        from lambdas.polls_ics.handler import handler

        mock_get.return_value = FINALIZED
        event = public_event(path="/polls/ics", queryStringParameters={"pollId": "poll-1"})

        res = handler(event, mock_context)
        assert res["statusCode"] == 200
        assert "text/calendar" in res["headers"]["Content-Type"]
        assert "attachment" in res["headers"]["Content-Disposition"]
        assert res["body"].startswith("BEGIN:VCALENDAR")

    @patch("lambdas.polls_ics.handler.get_poll")
    def test_undecided_form_is_a_404_not_an_empty_file(
        self, mock_get, mock_context, public_event
    ):
        """A client would silently import an empty file as a broken event."""
        from lambdas.polls_ics.handler import handler

        mock_get.return_value = POLL
        event = public_event(path="/polls/ics", queryStringParameters={"pollId": "poll-1"})

        assert handler(event, mock_context)["statusCode"] == 404


class TestFinalizeHandler:
    @patch("lambdas.polls_finalize.handler.send_confirmation")
    @patch("lambdas.polls_finalize.handler.get_responses_for_poll")
    @patch("lambdas.polls_finalize.handler.update_poll_attributes")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_records_the_time_and_closes_the_form(
        self, mock_get, mock_update, mock_responses, mock_send, mock_context, authorized_event
    ):
        from lambdas.polls_finalize.handler import handler

        mock_get.return_value = POLL
        mock_update.return_value = FINALIZED
        mock_responses.return_value = []
        event = authorized_event(
            httpMethod="POST",
            path="/polls/finalize",
            body=json.dumps({"pollId": "poll-1", "blockId": "2026-08-09T18:30"}),
        )

        assert handler(event, mock_context)["statusCode"] == 200
        _, changes = mock_update.call_args[0]
        assert changes["finalBlockId"] == "2026-08-09T18:30"
        # Deciding closes it: further availability can't change the outcome.
        assert changes["closeAt"]

    @patch("lambdas.polls_finalize.handler.send_confirmation")
    @patch("lambdas.polls_finalize.handler.get_responses_for_poll")
    @patch("lambdas.polls_finalize.handler.update_poll_attributes")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_notifies_each_respondent_once(
        self, mock_get, mock_update, mock_responses, mock_send, mock_context, authorized_event
    ):
        from lambdas.polls_finalize.handler import handler

        mock_get.return_value = POLL
        mock_update.return_value = FINALIZED
        mock_responses.return_value = [
            {"respondentKey": "guest#a", "email": "sam@x.com", "displayName": "Sam"},
            # Same person, answered twice from one browser.
            {"respondentKey": "guest#b", "email": "SAM@x.com", "displayName": "Sam"},
            {"respondentKey": "dom@x.com", "displayName": "Dom"},
        ]
        event = authorized_event(
            httpMethod="POST",
            path="/polls/finalize",
            body=json.dumps({"pollId": "poll-1", "blockId": "2026-08-09T18:30"}),
        )

        body = json.loads(handler(event, mock_context)["body"])
        assert mock_send.call_count == 2
        assert body["notified"] == 2

    @patch("lambdas.polls_finalize.handler.send_confirmation")
    @patch("lambdas.polls_finalize.handler.get_responses_for_poll")
    @patch("lambdas.polls_finalize.handler.update_poll_attributes")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_a_bounced_address_does_not_undo_the_decision(
        self, mock_get, mock_update, mock_responses, mock_send, mock_context, authorized_event
    ):
        from lambdas.polls_finalize.handler import handler

        mock_get.return_value = POLL
        mock_update.return_value = FINALIZED
        mock_responses.return_value = [
            {"respondentKey": "guest#a", "email": "bad@x.com", "displayName": "Sam"},
            {"respondentKey": "guest#b", "email": "ok@x.com", "displayName": "Alex"},
        ]
        mock_send.side_effect = [Exception("bounced"), None]
        event = authorized_event(
            httpMethod="POST",
            path="/polls/finalize",
            body=json.dumps({"pollId": "poll-1", "blockId": "2026-08-09T18:30"}),
        )

        res = handler(event, mock_context)
        body = json.loads(res["body"])
        assert res["statusCode"] == 200
        assert body["notified"] == 1
        assert body["failed"] == 1

    @patch("lambdas.polls_finalize.handler.send_confirmation")
    @patch("lambdas.polls_finalize.handler.get_responses_for_poll")
    @patch("lambdas.polls_finalize.handler.update_poll_attributes")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_can_correct_a_choice_without_mailing_everyone_again(
        self, mock_get, mock_update, mock_responses, mock_send, mock_context, authorized_event
    ):
        from lambdas.polls_finalize.handler import handler

        mock_get.return_value = POLL
        mock_update.return_value = FINALIZED
        mock_responses.return_value = [{"respondentKey": "a@x.com", "displayName": "A"}]
        event = authorized_event(
            httpMethod="POST",
            path="/polls/finalize",
            body=json.dumps(
                {"pollId": "poll-1", "blockId": "2026-08-09T18:30", "notify": False}
            ),
        )

        handler(event, mock_context)
        mock_send.assert_not_called()

    @patch("lambdas.polls_finalize.handler.update_poll_attributes")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_non_creator_gets_403(
        self, mock_get, mock_update, mock_context, authorized_event
    ):
        from lambdas.polls_finalize.handler import handler

        mock_get.return_value = POLL
        event = authorized_event(
            email="intruder@example.com",
            httpMethod="POST",
            path="/polls/finalize",
            body=json.dumps({"pollId": "poll-1", "blockId": "2026-08-09T18:30"}),
        )

        assert handler(event, mock_context)["statusCode"] == 403
        mock_update.assert_not_called()

    @patch("lambdas.polls_finalize.handler.update_poll_attributes")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_rejects_a_free_form_time(self, mock_get, mock_update, mock_context, authorized_event):
        from lambdas.polls_finalize.handler import handler

        mock_get.return_value = POLL
        event = authorized_event(
            httpMethod="POST",
            path="/polls/finalize",
            body=json.dumps({"pollId": "poll-1", "blockId": "next tuesday"}),
        )

        assert handler(event, mock_context)["statusCode"] == 400
        mock_update.assert_not_called()


class TestRespondentsList:
    @patch("lambdas.responses_list.handler.get_responses_for_poll")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_returns_contact_details_for_the_creator(
        self, mock_get, mock_responses, mock_context, authorized_event
    ):
        from lambdas.responses_list.handler import handler

        mock_get.return_value = POLL
        mock_responses.return_value = [
            {"respondentKey": "guest#a", "email": "sam@x.com", "displayName": "Sam", "blocks": ["b"]},
            {"respondentKey": "dom@x.com", "displayName": "Dom", "blocks": []},
        ]
        event = authorized_event(
            httpMethod="GET", path="/responses/list", queryStringParameters={"pollId": "poll-1"}
        )

        body = json.loads(handler(event, mock_context)["body"])
        by_name = {r["displayName"]: r for r in body["respondents"]}
        assert by_name["Sam"]["isGuest"] is True
        # An authed respondent's key IS their address.
        assert by_name["Dom"]["email"] == "dom@x.com"
        assert by_name["Dom"]["isGuest"] is False

    @patch("lambdas.responses_list.handler.get_responses_for_poll")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_non_creator_cannot_read_the_roster(
        self, mock_get, mock_responses, mock_context, authorized_event
    ):
        """It's a list of people's names and email addresses."""
        from lambdas.responses_list.handler import handler

        mock_get.return_value = POLL
        event = authorized_event(
            email="intruder@example.com",
            httpMethod="GET",
            path="/responses/list",
            queryStringParameters={"pollId": "poll-1"},
        )

        assert handler(event, mock_context)["statusCode"] == 403
