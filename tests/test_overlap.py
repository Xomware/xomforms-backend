"""
Tests for lambdas/common/overlap.py -- compute_overlap(poll_id): per-block
tally across N responses, ranked best time(s), tie-break by earliest.

Written RED-first per Phase 1 of docs/features/xomforms/PLAN.md.

compute_overlap() is compute-on-read: it loads the poll (for its grid
config) and all responses, tallies how many respondents selected each
blockId, and returns the full block-by-block breakdown plus the
highest-count block(s) (tie-broken by earliest chronological blockId,
per the plan's "Best-time logic" assumption -- fewest if-need-be is out of
scope for MVP since there's no if-need-be tier yet, so ties go straight to
earliest).
"""

from unittest.mock import patch


def _poll(poll_id="poll-1"):
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
        "showResultsToRespondents": False,
    }


def _response(respondent_key, blocks):
    return {"pollId": "poll-1", "respondentKey": respondent_key, "displayName": respondent_key, "blocks": blocks}


class TestComputeOverlap:
    @patch("lambdas.common.overlap.get_responses_for_poll")
    @patch("lambdas.common.overlap.get_poll")
    def test_tallies_counts_per_block(self, mock_get_poll, mock_get_responses):
        from lambdas.common.overlap import compute_overlap

        mock_get_poll.return_value = _poll()
        mock_get_responses.return_value = [
            _response("a@example.com", ["2026-08-03T08:00", "2026-08-03T08:30"]),
            _response("b@example.com", ["2026-08-03T08:00"]),
        ]

        result = compute_overlap("poll-1")
        by_id = {b["blockId"]: b for b in result["blocks"]}

        assert by_id["2026-08-03T08:00"]["count"] == 2
        assert by_id["2026-08-03T08:30"]["count"] == 1
        assert result["totalRespondents"] == 2

    @patch("lambdas.common.overlap.get_responses_for_poll")
    @patch("lambdas.common.overlap.get_poll")
    def test_includes_every_grid_block_even_with_zero_selections(self, mock_get_poll, mock_get_responses):
        """A block nobody selected must still appear in the breakdown with count=0,
        not be silently dropped -- the creator needs to see the full picture."""
        from lambdas.common.overlap import compute_overlap

        mock_get_poll.return_value = _poll()
        mock_get_responses.return_value = []

        result = compute_overlap("poll-1")
        assert len(result["blocks"]) == 2  # 08:00 and 08:30, per _poll()'s window
        assert all(b["count"] == 0 for b in result["blocks"])
        assert result["totalRespondents"] == 0

    @patch("lambdas.common.overlap.get_responses_for_poll")
    @patch("lambdas.common.overlap.get_poll")
    def test_ratio_is_count_over_total_respondents(self, mock_get_poll, mock_get_responses):
        from lambdas.common.overlap import compute_overlap

        mock_get_poll.return_value = _poll()
        mock_get_responses.return_value = [
            _response("a@example.com", ["2026-08-03T08:00"]),
            _response("b@example.com", ["2026-08-03T08:00"]),
            _response("c@example.com", []),
        ]

        result = compute_overlap("poll-1")
        by_id = {b["blockId"]: b for b in result["blocks"]}
        assert by_id["2026-08-03T08:00"]["ratio"] == 2 / 3
        assert by_id["2026-08-03T08:30"]["ratio"] == 0.0

    @patch("lambdas.common.overlap.get_responses_for_poll")
    @patch("lambdas.common.overlap.get_poll")
    def test_best_block_ids_is_the_max_count_block(self, mock_get_poll, mock_get_responses):
        from lambdas.common.overlap import compute_overlap

        mock_get_poll.return_value = _poll()
        mock_get_responses.return_value = [
            _response("a@example.com", ["2026-08-03T08:00"]),
            _response("b@example.com", ["2026-08-03T08:00"]),
            _response("c@example.com", ["2026-08-03T08:30"]),
        ]

        result = compute_overlap("poll-1")
        assert result["bestBlockIds"] == ["2026-08-03T08:00"]

    @patch("lambdas.common.overlap.get_responses_for_poll")
    @patch("lambdas.common.overlap.get_poll")
    def test_ties_are_tie_broken_by_earliest_and_all_tied_blocks_returned(self, mock_get_poll, mock_get_responses):
        """Both blocks tie at count=1 -- both are legitimate 'best' answers,
        returned in chronological order (earliest first)."""
        from lambdas.common.overlap import compute_overlap

        mock_get_poll.return_value = _poll()
        mock_get_responses.return_value = [
            _response("a@example.com", ["2026-08-03T08:30"]),
            _response("b@example.com", ["2026-08-03T08:00"]),
        ]

        result = compute_overlap("poll-1")
        assert result["bestBlockIds"] == ["2026-08-03T08:00", "2026-08-03T08:30"]

    @patch("lambdas.common.overlap.get_responses_for_poll")
    @patch("lambdas.common.overlap.get_poll")
    def test_no_responses_yields_no_best_blocks(self, mock_get_poll, mock_get_responses):
        from lambdas.common.overlap import compute_overlap

        mock_get_poll.return_value = _poll()
        mock_get_responses.return_value = []

        result = compute_overlap("poll-1")
        assert result["bestBlockIds"] == []

    @patch("lambdas.common.overlap.get_poll")
    def test_raises_not_found_for_unknown_poll(self, mock_get_poll):
        from lambdas.common.overlap import compute_overlap
        from lambdas.common.errors import NotFoundError

        mock_get_poll.return_value = None

        try:
            compute_overlap("does-not-exist")
            assert False, "expected NotFoundError"
        except NotFoundError:
            pass
