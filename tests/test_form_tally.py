"""
Tests for the per-field analytics engine in lambdas/common/overlap.py.

Phase 1: tally_field() reduces a list of respondent answers into a per-option
histogram (single_choice / multi_choice / dropdown) or a per-value scale
distribution + mean/min/max. compute_form_results() dispatches tally_field
across a Q&A poll's fields. The availability path (compute_overlap: grid +
contiguous window) is UNTOUCHED -- guarded by test_scheduler_golden.py.

Written RED-first.
"""

from unittest.mock import patch


def _qa_poll():
    return {
        "pollId": "poll-qa",
        "creatorEmail": "creator@example.com",
        "title": "RSVP",
        "formType": "qa",
        "fields": [
            {
                "fieldId": "f1",
                "type": "single_choice",
                "label": "Attending?",
                "options": [
                    {"optionId": "o1", "label": "Yes"},
                    {"optionId": "o2", "label": "No"},
                ],
            },
            {
                "fieldId": "f2",
                "type": "multi_choice",
                "label": "Sessions",
                "options": [
                    {"optionId": "s1", "label": "AM"},
                    {"optionId": "s2", "label": "PM"},
                    {"optionId": "s3", "label": "Eve"},
                ],
            },
            {"fieldId": "f3", "type": "scale", "label": "Excitement", "min": 1, "max": 5},
        ],
    }


def _resp(key, answers):
    return {"pollId": "poll-qa", "respondentKey": key, "displayName": key, "answers": answers}


class TestTallySingleChoice:
    def test_counts_one_selection_per_respondent(self):
        from lambdas.common.overlap import tally_field

        field = _qa_poll()["fields"][0]
        answers = [{"f1": ["o1"]}, {"f1": ["o1"]}, {"f1": ["o2"]}]
        result = tally_field(field, answers)

        by_opt = {o["optionId"]: o for o in result["options"]}
        assert by_opt["o1"]["count"] == 2
        assert by_opt["o2"]["count"] == 1
        assert by_opt["o1"]["ratio"] == 2 / 3
        assert result["totalResponses"] == 3

    def test_option_with_zero_selections_still_appears(self):
        from lambdas.common.overlap import tally_field

        field = _qa_poll()["fields"][0]
        result = tally_field(field, [{"f1": ["o1"]}])
        by_opt = {o["optionId"]: o for o in result["options"]}
        assert by_opt["o2"]["count"] == 0


class TestTallyMultiChoice:
    def test_respondents_may_pick_many(self):
        from lambdas.common.overlap import tally_field

        field = _qa_poll()["fields"][1]
        answers = [{"f2": ["s1", "s2"]}, {"f2": ["s2"]}, {"f2": ["s2", "s3"]}]
        result = tally_field(field, answers)

        by_opt = {o["optionId"]: o for o in result["options"]}
        assert by_opt["s1"]["count"] == 1
        assert by_opt["s2"]["count"] == 3
        assert by_opt["s3"]["count"] == 1
        # totalResponses counts respondents who answered the field, not picks.
        assert result["totalResponses"] == 3


class TestTallyScale:
    def test_mean_and_distribution(self):
        from lambdas.common.overlap import tally_field

        field = _qa_poll()["fields"][2]
        answers = [{"f3": 5}, {"f3": 5}, {"f3": 3}, {"f3": 1}]
        result = tally_field(field, answers)

        assert result["mean"] == (5 + 5 + 3 + 1) / 4
        assert result["min"] == 1
        assert result["max"] == 5
        by_val = {b["value"]: b for b in result["buckets"]}
        assert by_val[5]["count"] == 2
        assert by_val[1]["count"] == 1
        # A value in range with no votes still appears with count 0.
        assert by_val[2]["count"] == 0
        assert result["totalResponses"] == 4

    def test_no_answers_yields_null_mean(self):
        from lambdas.common.overlap import tally_field

        field = _qa_poll()["fields"][2]
        result = tally_field(field, [])
        assert result["mean"] is None
        assert result["totalResponses"] == 0


class TestComputeFormResults:
    @patch("lambdas.common.overlap.get_responses_for_poll")
    @patch("lambdas.common.overlap.get_poll")
    def test_dispatches_all_fields(self, mock_get_poll, mock_get_responses):
        from lambdas.common.overlap import compute_form_results

        mock_get_poll.return_value = _qa_poll()
        mock_get_responses.return_value = [
            _resp("a", {"f1": ["o1"], "f2": ["s1"], "f3": 5}),
            _resp("b", {"f1": ["o2"], "f2": ["s1", "s2"], "f3": 3}),
        ]

        result = compute_form_results("poll-qa")
        assert result["pollId"] == "poll-qa"
        assert result["totalRespondents"] == 2
        assert len(result["fields"]) == 3

        f1 = next(f for f in result["fields"] if f["fieldId"] == "f1")
        by_opt = {o["optionId"]: o for o in f1["options"]}
        assert by_opt["o1"]["count"] == 1
        assert by_opt["o2"]["count"] == 1

        f3 = next(f for f in result["fields"] if f["fieldId"] == "f3")
        assert f3["mean"] == 4.0

    @patch("lambdas.common.overlap.get_responses_for_poll")
    @patch("lambdas.common.overlap.get_poll")
    def test_reads_legacy_blocks_via_shim_for_availability_field(self, mock_get_poll, mock_get_responses):
        """A qa poll containing an availability field still tallies legacy
        blocks-only response items through read_answers()."""
        from lambdas.common.overlap import compute_form_results

        poll = {
            "pollId": "poll-qa",
            "formType": "qa",
            "fields": [{"fieldId": "avail", "type": "availability", "label": "When?"}],
            "startDate": "2026-08-03",
            "endDate": "2026-08-03",
            "dayStartMinute": 8 * 60,
            "dayEndMinute": 9 * 60,
            "granularityMinutes": 30,
            "timezone": "America/New_York",
        }
        mock_get_poll.return_value = poll
        # One item written the legacy way (blocks), one the new way (answers).
        mock_get_responses.return_value = [
            {"pollId": "poll-qa", "respondentKey": "a", "blocks": ["2026-08-03T08:00"]},
            {"pollId": "poll-qa", "respondentKey": "b", "answers": {"avail": ["2026-08-03T08:00"]}},
        ]

        result = compute_form_results("poll-qa")
        avail = next(f for f in result["fields"] if f["fieldId"] == "avail")
        by_opt = {o["optionId"]: o for o in avail["options"]}
        assert by_opt["2026-08-03T08:00"]["count"] == 2
