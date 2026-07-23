"""
GOLDEN-OUTPUT GUARANTEE: the live scheduler stays byte-for-byte unchanged.

Phase 1 of the form-builder is strictly additive. This file pins the exact
OverlapResult shape/values that compute_overlap produces for a legacy poll
(no `formType`, no `fields`) and asserts the results handlers still route
such polls to compute_overlap -- NOT the new compute_form_results path.

If any of these fail, the "scheduler is 100% untouched" guarantee is broken.
"""

import json
from unittest.mock import patch


def _legacy_poll(poll_id="poll-1"):
    """The current live scheduler poll shape -- no formType, no fields."""
    return {
        "pollId": poll_id,
        "creatorEmail": "creator@example.com",
        "title": "Fantasy Draft",
        "startDate": "2026-08-03",
        "endDate": "2026-08-03",
        "dayStartMinute": 8 * 60,
        "dayEndMinute": 9 * 60,
        "granularityMinutes": 30,
        "timezone": "America/New_York",
        "guestAllowed": True,
        "showResultsToRespondents": True,
    }


def _resp(key, blocks):
    return {"pollId": "poll-1", "respondentKey": key, "displayName": key, "blocks": blocks}


class TestComputeOverlapGoldenOutput:
    @patch("lambdas.common.overlap.get_responses_for_poll")
    @patch("lambdas.common.overlap.get_poll")
    def test_legacy_poll_returns_exact_overlap_result_shape(self, mock_get_poll, mock_get_responses):
        from lambdas.common.overlap import compute_overlap

        mock_get_poll.return_value = _legacy_poll()
        mock_get_responses.return_value = [
            _resp("a@example.com", ["2026-08-03T08:00", "2026-08-03T08:30"]),
            _resp("b@example.com", ["2026-08-03T08:00"]),
        ]

        result = compute_overlap("poll-1")

        # The keys AND their meaning must be exactly today's contract.
        assert set(result.keys()) == {
            "pollId",
            "totalRespondents",
            "blocks",
            "bestBlockIds",
            "eventDurationMinutes",
            "slotCount",
            "bestWindowStartIds",
            "bestWindowCount",
        }
        assert result["pollId"] == "poll-1"
        assert result["totalRespondents"] == 2
        assert result["blocks"] == [
            {
                "blockId": "2026-08-03T08:00",
                "utcInstant": "2026-08-03T08:00:00-04:00",
                "count": 2,
                "total": 2,
                "ratio": 1.0,
            },
            {
                "blockId": "2026-08-03T08:30",
                "utcInstant": "2026-08-03T08:30:00-04:00",
                "count": 1,
                "total": 2,
                "ratio": 0.5,
            },
        ]
        assert result["bestBlockIds"] == ["2026-08-03T08:00"]
        assert result["eventDurationMinutes"] == 30
        assert result["slotCount"] == 1
        assert result["bestWindowStartIds"] == ["2026-08-03T08:00"]
        assert result["bestWindowCount"] == 2


class TestResultsHandlerRoutesLegacyPollToOverlap:
    @patch("lambdas.results_get.handler.compute_form_results")
    @patch("lambdas.results_get.handler.compute_overlap")
    @patch("lambdas.results_get.handler.get_poll")
    def test_creator_results_uses_overlap_for_legacy_poll(
        self, mock_get_poll, mock_overlap, mock_form_results, mock_context, authorized_event
    ):
        from lambdas.results_get.handler import handler

        mock_get_poll.return_value = _legacy_poll()
        mock_overlap.return_value = {"pollId": "poll-1", "blocks": []}

        event = authorized_event(email="creator@example.com", path="/results/get")
        event["queryStringParameters"] = {"pollId": "poll-1"}
        response = handler(event, mock_context)

        assert response["statusCode"] == 200
        mock_overlap.assert_called_once_with("poll-1")
        mock_form_results.assert_not_called()

    @patch("lambdas.results_get.handler.compute_form_results")
    @patch("lambdas.results_get.handler.compute_overlap")
    @patch("lambdas.results_get.handler.get_poll")
    def test_creator_results_uses_form_results_for_qa_poll(
        self, mock_get_poll, mock_overlap, mock_form_results, mock_context, authorized_event
    ):
        from lambdas.results_get.handler import handler

        qa_poll = {
            "pollId": "poll-qa",
            "creatorEmail": "creator@example.com",
            "formType": "qa",
            "fields": [
                {
                    "fieldId": "f1",
                    "type": "single_choice",
                    "label": "x",
                    "options": [{"optionId": "o1", "label": "A"}, {"optionId": "o2", "label": "B"}],
                }
            ],
        }
        mock_get_poll.return_value = qa_poll
        mock_form_results.return_value = {"pollId": "poll-qa", "fields": []}

        event = authorized_event(email="creator@example.com", path="/results/get")
        event["queryStringParameters"] = {"pollId": "poll-qa"}
        response = handler(event, mock_context)

        assert response["statusCode"] == 200
        mock_form_results.assert_called_once_with("poll-qa")
        mock_overlap.assert_not_called()


class TestSubmitHandlerRoutesLegacyPollToAvailability:
    @patch("lambdas.responses_submit_authed.handler.submit_answers")
    @patch("lambdas.responses_submit_authed.handler.submit_availability")
    @patch("lambdas.responses_submit_authed.handler.get_poll")
    def test_authed_submit_uses_availability_for_legacy_poll(
        self, mock_get_poll, mock_avail, mock_answers, mock_context, authorized_event
    ):
        from lambdas.responses_submit_authed.handler import handler

        mock_get_poll.return_value = _legacy_poll()
        mock_avail.return_value = {"pollId": "poll-1", "blocks": []}

        event = authorized_event(
            email="a@example.com",
            path="/responses/submit",
            body=json.dumps({"pollId": "poll-1", "displayName": "A", "blocks": ["2026-08-03T08:00"]}),
        )
        response = handler(event, mock_context)

        assert response["statusCode"] == 200
        mock_avail.assert_called_once()
        mock_answers.assert_not_called()
