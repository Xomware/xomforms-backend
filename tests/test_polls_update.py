"""
Tests for lambdas/polls_update -- creator-editable form settings.
"""

import json
from unittest.mock import patch

OWNED = {"pollId": "poll-1", "creatorEmail": "creator@example.com", "title": "Draft night"}


class TestPollsUpdate:
    @patch("lambdas.polls_update.handler.update_poll_attributes")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_updates_only_the_supplied_fields(
        self, mock_get, mock_update, mock_context, authorized_event
    ):
        """
        A partial write, so a client toggling one setting can't clobber a
        concurrent change to another.
        """
        from lambdas.polls_update.handler import handler

        mock_get.return_value = OWNED
        mock_update.return_value = {**OWNED, "allowResponseEdits": False}
        event = authorized_event(
            httpMethod="POST",
            path="/polls/update",
            body=json.dumps({"pollId": "poll-1", "allowResponseEdits": False}),
        )

        assert handler(event, mock_context)["statusCode"] == 200
        _, changes = mock_update.call_args[0]
        assert changes == {"allowResponseEdits": False}

    @patch("lambdas.polls_update.handler.update_poll_attributes")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_keeps_the_legacy_boolean_in_sync(
        self, mock_get, mock_update, mock_context, authorized_event
    ):
        """Any client still reading showResultsToRespondents must not drift."""
        from lambdas.polls_update.handler import handler

        mock_get.return_value = OWNED
        mock_update.return_value = OWNED
        event = authorized_event(
            httpMethod="POST",
            path="/polls/update",
            body=json.dumps({"pollId": "poll-1", "resultsVisibility": "always"}),
        )

        handler(event, mock_context)
        _, changes = mock_update.call_args[0]
        assert changes["showResultsToRespondents"] is True

        mock_update.reset_mock()
        event = authorized_event(
            httpMethod="POST",
            path="/polls/update",
            body=json.dumps({"pollId": "poll-1", "resultsVisibility": "after_response"}),
        )
        handler(event, mock_context)
        _, changes = mock_update.call_args[0]
        assert changes["showResultsToRespondents"] is False

    @patch("lambdas.polls_update.handler.update_poll_attributes")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_rejects_an_unknown_visibility(
        self, mock_get, mock_update, mock_context, authorized_event
    ):
        from lambdas.polls_update.handler import handler

        mock_get.return_value = OWNED
        event = authorized_event(
            httpMethod="POST",
            path="/polls/update",
            body=json.dumps({"pollId": "poll-1", "resultsVisibility": "sometimes"}),
        )

        assert handler(event, mock_context)["statusCode"] == 400
        mock_update.assert_not_called()

    @patch("lambdas.polls_update.handler.update_poll_attributes")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_non_creator_gets_403(self, mock_get, mock_update, mock_context, authorized_event):
        from lambdas.polls_update.handler import handler

        mock_get.return_value = OWNED
        event = authorized_event(
            email="intruder@example.com",
            httpMethod="POST",
            path="/polls/update",
            body=json.dumps({"pollId": "poll-1", "allowResponseEdits": False}),
        )

        assert handler(event, mock_context)["statusCode"] == 403
        mock_update.assert_not_called()

    @patch("lambdas.polls_update.handler.update_poll_attributes")
    @patch("lambdas.common.polls_dynamo.get_poll")
    def test_no_settings_supplied_is_a_400(
        self, mock_get, mock_update, mock_context, authorized_event
    ):
        from lambdas.polls_update.handler import handler

        mock_get.return_value = OWNED
        event = authorized_event(
            httpMethod="POST", path="/polls/update", body=json.dumps({"pollId": "poll-1"})
        )

        assert handler(event, mock_context)["statusCode"] == 400
        mock_update.assert_not_called()

    def test_grid_config_is_not_editable(self):
        """
        Changing the date range or granularity under respondents who already
        answered would invalidate their submissions -- painted blocks might no
        longer exist on the grid. Excluded by construction, not by validation.
        """
        from lambdas.common.models import UpdatePollRequest

        req = UpdatePollRequest(
            pollId="poll-1", allowResponseEdits=False, **{"title": "New title"}
        )
        assert set(req.changes()) == {"allowResponseEdits", "title"}
        assert not hasattr(req, "granularityMinutes")


class TestResolveAllowResponseEdits:
    def test_defaults_to_true_when_absent(self):
        """
        Submit has ALWAYS been an idempotent upsert by respondentKey, so
        re-submitting already replaced your answer. Defaulting to False would
        be a silent regression dressed up as a new setting.
        """
        from lambdas.common.models import resolve_allow_response_edits

        assert resolve_allow_response_edits({}) is True

    def test_respects_an_explicit_false(self):
        from lambdas.common.models import resolve_allow_response_edits

        assert resolve_allow_response_edits({"allowResponseEdits": False}) is False
