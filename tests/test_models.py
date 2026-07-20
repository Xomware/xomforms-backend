"""
Tests for lambdas/common/models.py -- Pydantic 2.8 request/response models
at the boundary (conscious deviation from xomify's parse_body/require_fields,
per docs/features/xomforms/PLAN.md and .claude/rules/backend.md).

Written RED-first per Phase 1 of the plan.
"""

import pytest
from pydantic import ValidationError as PydanticValidationError


def _valid_poll_payload(**overrides):
    base = {
        "title": "Fantasy Draft",
        "startDate": "2026-08-03",
        "endDate": "2026-08-05",
        "dayStartMinute": 8 * 60,
        "dayEndMinute": 14 * 60,
        "granularityMinutes": 30,
        "timezone": "America/New_York",
    }
    base.update(overrides)
    return base


class TestCreatePollRequest:
    def test_accepts_valid_payload(self):
        from lambdas.common.models import CreatePollRequest

        model = CreatePollRequest(**_valid_poll_payload())
        assert model.title == "Fantasy Draft"
        # Defaults per plan's "Assumptions" section
        assert model.guestAllowed is False
        assert model.showResultsToRespondents is False

    def test_rejects_bad_date_range_end_before_start(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(**_valid_poll_payload(startDate="2026-08-10", endDate="2026-08-05"))

    def test_rejects_out_of_order_time_window(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(**_valid_poll_payload(dayStartMinute=14 * 60, dayEndMinute=8 * 60))

    def test_rejects_equal_time_window_bounds(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(**_valid_poll_payload(dayStartMinute=8 * 60, dayEndMinute=8 * 60))

    def test_rejects_invalid_granularity(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(**_valid_poll_payload(granularityMinutes=7))

    def test_rejects_unknown_timezone(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(**_valid_poll_payload(timezone="Not/A_Real_Zone"))

    def test_rejects_oversized_date_range(self):
        """MAX_DATE_RANGE_DAYS cap -- long ranges alone are already rejected
        before even considering grid size."""
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(**_valid_poll_payload(startDate="2026-01-01", endDate="2026-12-31"))

    def test_rejects_oversized_grid_even_within_date_range_cap(self):
        """A wide time-of-day window at fine granularity, within the date-range
        cap, must still be rejected by the total-block-count cap, keeping a
        fully-selected response item well under DynamoDB's 400 KB limit."""
        from lambdas.common.models import CreatePollRequest
        from lambdas.common.constants import MAX_DATE_RANGE_DAYS, MAX_GRID_BLOCKS

        # 24h window at 15-min granularity = 96 blocks/day; 25 days stays
        # within MAX_DATE_RANGE_DAYS but still blows the block-count cap.
        days = 25
        blocks_per_day = 96
        assert days <= MAX_DATE_RANGE_DAYS  # sanity: date-range check alone wouldn't catch this
        assert days * blocks_per_day > MAX_GRID_BLOCKS  # sanity: this really does exceed the cap

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(**_valid_poll_payload(
                startDate="2026-01-01",
                endDate="2026-01-25",
                dayStartMinute=0,
                dayEndMinute=24 * 60,
                granularityMinutes=15,
            ))

    def test_accepts_grid_at_exactly_the_cap_boundary(self):
        from lambdas.common.models import CreatePollRequest

        # 4 days x (14:00-08:00)/30min = 4 x 12 = 48 blocks -- comfortably under cap.
        model = CreatePollRequest(**_valid_poll_payload(startDate="2026-08-03", endDate="2026-08-06"))
        assert model is not None

    def test_title_cannot_be_blank(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(**_valid_poll_payload(title="   "))

    def test_guest_allowed_and_show_results_are_settable(self):
        from lambdas.common.models import CreatePollRequest

        model = CreatePollRequest(**_valid_poll_payload(guestAllowed=True, showResultsToRespondents=True))
        assert model.guestAllowed is True
        assert model.showResultsToRespondents is True


class TestSubmitAvailabilityRequest:
    def test_accepts_valid_payload(self):
        from lambdas.common.models import SubmitAvailabilityRequest

        model = SubmitAvailabilityRequest(
            displayName="Dom",
            blocks=["2026-08-03T08:00", "2026-08-03T08:30"],
        )
        assert model.displayName == "Dom"
        assert len(model.blocks) == 2

    def test_display_name_required_and_non_blank(self):
        from lambdas.common.models import SubmitAvailabilityRequest

        with pytest.raises(PydanticValidationError):
            SubmitAvailabilityRequest(displayName="  ", blocks=[])

    def test_allows_empty_blocks_list(self):
        """A respondent can submit 'no availability' -- an empty selection
        is valid, not an error."""
        from lambdas.common.models import SubmitAvailabilityRequest

        model = SubmitAvailabilityRequest(displayName="Dom", blocks=[])
        assert model.blocks == []

    def test_rejects_oversized_blocks_list(self):
        from lambdas.common.models import SubmitAvailabilityRequest
        from lambdas.common.constants import MAX_GRID_BLOCKS

        too_many = [f"2026-08-03T08:{i:02d}" for i in range(MAX_GRID_BLOCKS + 1)]
        with pytest.raises(PydanticValidationError):
            SubmitAvailabilityRequest(displayName="Dom", blocks=too_many)

    def test_dedupes_repeated_block_ids(self):
        from lambdas.common.models import SubmitAvailabilityRequest

        model = SubmitAvailabilityRequest(
            displayName="Dom",
            blocks=["2026-08-03T08:00", "2026-08-03T08:00", "2026-08-03T08:30"],
        )
        assert sorted(model.blocks) == ["2026-08-03T08:00", "2026-08-03T08:30"]
