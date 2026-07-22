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


def _poll_window(start, end, day_start_min, day_end_min, granularity, event_duration=None):
    poll = {
        "pollId": "poll-1",
        "creatorEmail": "creator@example.com",
        "title": "Fantasy Draft",
        "startDate": start,
        "endDate": end,
        "dayStartMinute": day_start_min,
        "dayEndMinute": day_end_min,
        "granularityMinutes": granularity,
        "timezone": "America/New_York",
        "guestAllowed": True,
        "showResultsToRespondents": False,
    }
    if event_duration is not None:
        poll["eventDurationMinutes"] = event_duration
    return poll


class TestBestWindow:
    """
    compute_overlap also reports the best contiguous START WINDOW of the
    poll's event length -- the start time where the most respondents are free
    for the WHOLE window (not just a single slot). Additive to the existing
    per-block tally; back-compatible when a poll predates eventDurationMinutes.
    """

    @patch("lambdas.common.overlap.get_responses_for_poll")
    @patch("lambdas.common.overlap.get_poll")
    def test_single_slot_window_when_duration_absent_matches_best_block(self, mock_get_poll, mock_get_responses):
        """A poll with no eventDurationMinutes is a one-slot event: slotCount
        collapses to 1 and the best window equals the best single block."""
        from lambdas.common.overlap import compute_overlap

        mock_get_poll.return_value = _poll()  # 08:00-09:00 @30min, no eventDurationMinutes
        mock_get_responses.return_value = [
            _response("a@example.com", ["2026-08-03T08:00"]),
            _response("b@example.com", ["2026-08-03T08:00"]),
            _response("c@example.com", ["2026-08-03T08:30"]),
        ]

        result = compute_overlap("poll-1")
        assert result["eventDurationMinutes"] == 30  # defaulted to granularity
        assert result["slotCount"] == 1
        assert result["bestWindowStartIds"] == ["2026-08-03T08:00"]
        assert result["bestWindowCount"] == 2

    @patch("lambdas.common.overlap.get_responses_for_poll")
    @patch("lambdas.common.overlap.get_poll")
    def test_multi_slot_window_picks_best_contiguous_start(self, mock_get_poll, mock_get_responses):
        """With a 60-minute event over 30-minute slots (slotCount 2), the best
        window is the start whose respondents are free for BOTH slots."""
        from lambdas.common.overlap import compute_overlap

        # 08:00-10:00 @30min -> blocks 08:00, 08:30, 09:00, 09:30
        mock_get_poll.return_value = _poll_window(
            "2026-08-03", "2026-08-03", 8 * 60, 10 * 60, 30, event_duration=60
        )
        mock_get_responses.return_value = [
            _response("a@example.com", ["2026-08-03T08:00", "2026-08-03T08:30", "2026-08-03T09:00"]),
            _response("b@example.com", ["2026-08-03T08:30", "2026-08-03T09:00", "2026-08-03T09:30"]),
            _response("c@example.com", ["2026-08-03T08:30", "2026-08-03T09:00"]),
        ]

        result = compute_overlap("poll-1")
        assert result["slotCount"] == 2
        assert result["eventDurationMinutes"] == 60
        # window [08:30, 09:00] is covered fully by all three -> count 3, the max
        assert result["bestWindowStartIds"] == ["2026-08-03T08:30"]
        assert result["bestWindowCount"] == 3

    @patch("lambdas.common.overlap.get_responses_for_poll")
    @patch("lambdas.common.overlap.get_poll")
    def test_window_does_not_span_a_day_boundary(self, mock_get_poll, mock_get_responses):
        """A multi-slot window must stay within a single day -- the last slot of
        one day plus the first slot of the next is NOT a valid contiguous window."""
        from lambdas.common.overlap import compute_overlap

        # Two days, 08:00-09:00 @30min -> 2 blocks/day; 60-min event -> slotCount 2.
        mock_get_poll.return_value = _poll_window(
            "2026-08-03", "2026-08-04", 8 * 60, 9 * 60, 30, event_duration=60
        )
        mock_get_responses.return_value = [
            # Covers day-1 fully, plus only the first slot of day-2.
            _response("a@example.com", ["2026-08-03T08:00", "2026-08-03T08:30", "2026-08-04T08:00"]),
        ]

        result = compute_overlap("poll-1")
        assert result["slotCount"] == 2
        # Only two valid windows exist (one per day, each starting at 08:00).
        # Day 1's window is fully covered (count 1); day 2's is not (missing 08:30).
        # A cross-day [08:30 day1, 08:00 day2] window must NOT appear.
        assert result["bestWindowStartIds"] == ["2026-08-03T08:00"]
        assert result["bestWindowCount"] == 1

    @patch("lambdas.common.overlap.get_responses_for_poll")
    @patch("lambdas.common.overlap.get_poll")
    def test_no_responses_yields_empty_best_window(self, mock_get_poll, mock_get_responses):
        from lambdas.common.overlap import compute_overlap

        mock_get_poll.return_value = _poll_window(
            "2026-08-03", "2026-08-03", 8 * 60, 10 * 60, 30, event_duration=60
        )
        mock_get_responses.return_value = []

        result = compute_overlap("poll-1")
        assert result["bestWindowStartIds"] == []
        assert result["bestWindowCount"] == 0
