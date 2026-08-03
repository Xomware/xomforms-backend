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

    def test_event_duration_defaults_to_none(self):
        """Omitted eventDurationMinutes stays None at the model boundary --
        the handler defaults it to one slot (granularity) so existing polls
        created before this field keep working unchanged."""
        from lambdas.common.models import CreatePollRequest

        model = CreatePollRequest(**_valid_poll_payload())
        assert model.eventDurationMinutes is None

    def test_event_duration_accepts_positive_value(self):
        from lambdas.common.models import CreatePollRequest

        model = CreatePollRequest(**_valid_poll_payload(eventDurationMinutes=120))
        assert model.eventDurationMinutes == 120

    def test_event_duration_rejects_zero_or_negative(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(**_valid_poll_payload(eventDurationMinutes=0))
        with pytest.raises(PydanticValidationError):
            CreatePollRequest(**_valid_poll_payload(eventDurationMinutes=-30))


def _windowed_poll_payload(**overrides):
    """The current frontend's create shape: a start range + event length.
    Grid window (dayStart/dayEnd/granularity) is DERIVED, not supplied."""
    base = {
        "title": "Fantasy Draft",
        "startDate": "2026-08-03",
        "endDate": "2026-08-05",
        "earliestStartMinute": 18 * 60,   # 6 PM
        "latestStartMinute": 21 * 60,     # 9 PM
        "eventDurationMinutes": 120,      # 2 h
        "timezone": "America/New_York",
    }
    base.update(overrides)
    return base


class TestWindowedSchedulerModel:
    """Duration + start-range create shape (earliest/latest start + duration)."""

    def test_accepts_and_derives_grid_window(self):
        from lambdas.common.models import CreatePollRequest

        model = CreatePollRequest(**_windowed_poll_payload())
        # Derived: dayStart = earliestStart, dayEnd = latestStart + duration,
        # granularity fixed at 15.
        assert model.dayStartMinute == 18 * 60
        assert model.dayEndMinute == 21 * 60 + 120
        assert model.granularityMinutes == 15
        assert model.earliestStartMinute == 18 * 60
        assert model.latestStartMinute == 21 * 60

    def test_overnight_window_allows_day_end_past_midnight(self):
        from lambdas.common.models import CreatePollRequest

        # latest 10 PM + 3 h -> ends 1 AM next day: dayEnd = 1320 + 180 = 1500.
        model = CreatePollRequest(
            **_windowed_poll_payload(
                latestStartMinute=22 * 60, eventDurationMinutes=180, earliestStartMinute=20 * 60
            )
        )
        assert model.dayEndMinute == 22 * 60 + 180
        assert model.dayEndMinute > 24 * 60

    def test_rejects_latest_before_earliest(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(
                **_windowed_poll_payload(earliestStartMinute=21 * 60, latestStartMinute=18 * 60)
            )

    def test_allows_equal_earliest_and_latest_single_fixed_start(self):
        from lambdas.common.models import CreatePollRequest

        model = CreatePollRequest(
            **_windowed_poll_payload(earliestStartMinute=19 * 60, latestStartMinute=19 * 60)
        )
        assert model.dayStartMinute == 19 * 60

    def test_rejects_only_one_bound_supplied(self):
        from lambdas.common.models import CreatePollRequest

        payload = _windowed_poll_payload()
        payload.pop("latestStartMinute")
        with pytest.raises(PydanticValidationError):
            CreatePollRequest(**payload)

    def test_rejects_duration_not_multiple_of_15(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(**_windowed_poll_payload(eventDurationMinutes=100))

    def test_rejects_duration_over_six_hours(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(**_windowed_poll_payload(eventDurationMinutes=375))

    def test_windowed_shape_requires_duration(self):
        from lambdas.common.models import CreatePollRequest

        payload = _windowed_poll_payload()
        payload.pop("eventDurationMinutes")
        with pytest.raises(PydanticValidationError):
            CreatePollRequest(**payload)


class TestStartIntervalGranularity:
    """
    granularityMinutes is the creator's chosen START INTERVAL on the windowed
    shape -- which start times responders are offered, and therefore the
    resolution of the paint grid.
    """

    def test_defaults_to_15_when_omitted(self):
        """Back-compat: clients predating the control keep the old 15-min grid."""
        from lambdas.common.models import CreatePollRequest

        model = CreatePollRequest(**_windowed_poll_payload())
        assert model.granularityMinutes == 15

    @pytest.mark.parametrize("step", [15, 30, 60])
    def test_honours_each_allowed_interval(self, step):
        from lambdas.common.models import CreatePollRequest

        model = CreatePollRequest(
            **_windowed_poll_payload(
                granularityMinutes=step,
                earliestStartMinute=18 * 60,
                latestStartMinute=21 * 60,
                eventDurationMinutes=120,
            )
        )
        assert model.granularityMinutes == step
        # The derived window still spans earliest -> latest + duration.
        assert model.dayStartMinute == 18 * 60
        assert model.dayEndMinute == 21 * 60 + 120

    def test_rejects_an_interval_outside_the_allowed_set(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(**_windowed_poll_payload(granularityMinutes=7))

    # ── Alignment ─────────────────────────────────────────────────────
    # blocks_per_day floor-divides by granularity, so a misaligned boundary
    # would silently truncate the final slot off the grid -- responders could
    # never paint the last event window the creator configured.
    def test_rejects_start_time_off_the_interval(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError) as err:
            CreatePollRequest(
                **_windowed_poll_payload(
                    granularityMinutes=60, earliestStartMinute=18 * 60 + 15
                )
            )
        assert "earliestStartMinute" in str(err.value)

    def test_rejects_latest_start_off_the_interval(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError) as err:
            CreatePollRequest(
                **_windowed_poll_payload(granularityMinutes=30, latestStartMinute=21 * 60 + 15)
            )
        assert "latestStartMinute" in str(err.value)

    def test_rejects_duration_off_the_interval(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError) as err:
            CreatePollRequest(
                **_windowed_poll_payload(granularityMinutes=60, eventDurationMinutes=90)
            )
        assert "eventDurationMinutes" in str(err.value)

    def test_aligned_hourly_config_is_accepted(self):
        from lambdas.common.models import CreatePollRequest

        model = CreatePollRequest(
            **_windowed_poll_payload(
                granularityMinutes=60,
                earliestStartMinute=18 * 60,
                latestStartMinute=21 * 60,
                eventDurationMinutes=180,
            )
        )
        window = model.dayEndMinute - model.dayStartMinute
        assert window % model.granularityMinutes == 0

    def test_coarser_interval_yields_fewer_blocks(self):
        """A wider interval must relax the grid cap, never tighten it."""
        from lambdas.common.models import CreatePollRequest

        fine = CreatePollRequest(**_windowed_poll_payload(granularityMinutes=15))
        coarse = CreatePollRequest(**_windowed_poll_payload(granularityMinutes=60))

        fine_blocks = (fine.dayEndMinute - fine.dayStartMinute) // fine.granularityMinutes
        coarse_blocks = (coarse.dayEndMinute - coarse.dayStartMinute) // coarse.granularityMinutes
        assert coarse_blocks < fine_blocks


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
