"""
Tests for lambdas/common/timezone.py -- grid generation from poll config,
block-id <-> UTC-instant round-trip, and DST-boundary safety.

Written RED-first per Phase 1 of docs/features/xomforms/PLAN.md.

Design under test:
- generate_grid(poll_config) enumerates every candidate block between
  [startDate, endDate] x [dayStartMinute, dayEndMinute) at granularityMinutes
  steps, IN THE POLL'S OWN TIMEZONE. Each block's wall-clock local datetime
  is localized independently (never derived by adding a timedelta to a
  running UTC value) -- this is the key correctness property that keeps
  DST transitions from silently shifting later blocks.
- blockId is a stable, timezone-naive string ("YYYY-MM-DDTHH:MM") -- safe to
  use as a dict key for overlap tally and safe to pass over the wire.
- block_id_to_utc(poll_config, block_id) recomputes the canonical UTC
  instant for a given blockId by localizing it in the poll's timezone.
"""

from datetime import datetime, timezone as dt_timezone


def _config(**overrides):
    base = {
        "startDate": "2026-08-03",
        "endDate": "2026-08-04",
        "dayStartMinute": 8 * 60,   # 08:00
        "dayEndMinute": 10 * 60,    # 10:00 (exclusive)
        "granularityMinutes": 30,
        "timezone": "America/New_York",
    }
    base.update(overrides)
    return base


class TestGenerateGrid:
    def test_generates_expected_block_count(self):
        from lambdas.common.timezone import generate_grid

        # 2 days x (10:00-08:00)/30min = 2 days x 4 blocks/day = 8 blocks
        blocks = generate_grid(_config())
        assert len(blocks) == 8

    def test_block_shape(self):
        from lambdas.common.timezone import generate_grid

        blocks = generate_grid(_config())
        first = blocks[0]
        assert set(first.keys()) == {"blockId", "utcInstant"}
        assert first["blockId"] == "2026-08-03T08:00"

    def test_blocks_are_chronologically_ordered_by_utc_instant(self):
        from lambdas.common.timezone import generate_grid

        blocks = generate_grid(_config())
        utc_values = [b["utcInstant"] for b in blocks]
        assert utc_values == sorted(utc_values)

    def test_end_date_window_is_exclusive_of_day_end_minute(self):
        """dayEndMinute=10:00 with a block starting at 08:00, 30-min granularity
        should NOT include a block starting exactly at 10:00."""
        from lambdas.common.timezone import generate_grid

        blocks = generate_grid(_config())
        block_ids = [b["blockId"] for b in blocks]
        assert "2026-08-03T10:00" not in block_ids
        assert "2026-08-03T09:30" in block_ids

    def test_different_timezones_produce_different_utc_instants_for_same_wall_time(self):
        from lambdas.common.timezone import generate_grid

        ny_blocks = generate_grid(_config(timezone="America/New_York"))
        la_blocks = generate_grid(_config(timezone="America/Los_Angeles"))

        ny_first = next(b for b in ny_blocks if b["blockId"] == "2026-08-03T08:00")
        la_first = next(b for b in la_blocks if b["blockId"] == "2026-08-03T08:00")
        assert ny_first["utcInstant"] != la_first["utcInstant"]

    def test_utc_instant_is_correct_for_known_offset(self):
        """America/New_York is UTC-4 in August (EDT) -- 08:00 local == 12:00 UTC."""
        from lambdas.common.timezone import generate_grid

        blocks = generate_grid(_config())
        first = next(b for b in blocks if b["blockId"] == "2026-08-03T08:00")
        parsed = datetime.fromisoformat(first["utcInstant"])
        assert parsed.astimezone(dt_timezone.utc).hour == 12
        assert parsed.astimezone(dt_timezone.utc).day == 3


class TestBlockIdUtcRoundTrip:
    def test_block_id_to_utc_matches_generated_grid(self):
        from lambdas.common.timezone import generate_grid, block_id_to_utc

        config = _config()
        blocks = generate_grid(config)
        for block in blocks:
            assert block_id_to_utc(config, block["blockId"]) == block["utcInstant"]

    def test_block_id_to_utc_raises_on_malformed_block_id(self):
        from lambdas.common.timezone import block_id_to_utc
        from lambdas.common.errors import ValidationError

        config = _config()
        try:
            block_id_to_utc(config, "not-a-valid-block-id")
            assert False, "expected ValidationError"
        except ValidationError:
            pass


class TestDstBoundarySafety:
    """
    America/New_York spring-forward 2026-03-08 02:00 -> 03:00 (2:00-2:59
    local time does not exist that day). America/New_York fall-back
    2026-11-01 02:00 -> 01:00 (1:00-1:59 local time occurs twice).

    Correctness bar for MVP: grid generation must never raise, must never
    produce two blocks with an identical blockId, and must localize each
    block's wall-clock time independently (not by adding timedelta to a
    running UTC instant) so blocks on either side of the transition are not
    silently shifted. Exact disambiguation of the repeated fall-back hour
    is a documented, accepted approximation (fold=0 / pre-transition
    offset) -- not exhaustively asserted here since real users are very
    unlikely to be scheduling availability at 1-3 AM.
    """

    def test_spring_forward_grid_generation_does_not_raise(self):
        from lambdas.common.timezone import generate_grid

        config = _config(
            startDate="2026-03-08",
            endDate="2026-03-08",
            dayStartMinute=1 * 60,   # 01:00
            dayEndMinute=4 * 60,     # 04:00
            granularityMinutes=30,
        )
        blocks = generate_grid(config)
        assert len(blocks) == 6  # 01:00, 01:30, 02:00, 02:30, 03:00, 03:30
        block_ids = [b["blockId"] for b in blocks]
        assert len(block_ids) == len(set(block_ids))  # no duplicate blockIds

    def test_spring_forward_utc_instants_are_non_decreasing(self):
        """Even through the gap hour, per-block localization must not produce
        a UTC instant that goes backwards relative to the previous block."""
        from lambdas.common.timezone import generate_grid

        config = _config(
            startDate="2026-03-08",
            endDate="2026-03-08",
            dayStartMinute=1 * 60,
            dayEndMinute=4 * 60,
            granularityMinutes=30,
        )
        blocks = generate_grid(config)
        utc_values = [b["utcInstant"] for b in blocks]
        assert utc_values == sorted(utc_values)

    def test_fall_back_grid_generation_does_not_raise(self):
        from lambdas.common.timezone import generate_grid

        config = _config(
            startDate="2026-11-01",
            endDate="2026-11-01",
            dayStartMinute=0 * 60,    # 00:00
            dayEndMinute=3 * 60,      # 03:00
            granularityMinutes=30,
        )
        blocks = generate_grid(config)
        assert len(blocks) == 6
        block_ids = [b["blockId"] for b in blocks]
        assert len(block_ids) == len(set(block_ids))

    def test_fall_back_block_id_to_utc_round_trip_is_self_consistent(self):
        from lambdas.common.timezone import generate_grid, block_id_to_utc

        config = _config(
            startDate="2026-11-01",
            endDate="2026-11-01",
            dayStartMinute=0 * 60,
            dayEndMinute=3 * 60,
            granularityMinutes=30,
        )
        blocks = generate_grid(config)
        for block in blocks:
            assert block_id_to_utc(config, block["blockId"]) == block["utcInstant"]

    def test_non_dst_week_has_no_surprise_offset_jumps(self):
        """Sanity check well outside any transition: every block should be
        exactly granularityMinutes apart in UTC."""
        from lambdas.common.timezone import generate_grid
        from datetime import timedelta

        config = _config(
            startDate="2026-07-01",
            endDate="2026-07-01",
            dayStartMinute=8 * 60,
            dayEndMinute=12 * 60,
            granularityMinutes=30,
        )
        blocks = generate_grid(config)
        parsed = [datetime.fromisoformat(b["utcInstant"]) for b in blocks]
        for a, b in zip(parsed, parsed[1:]):
            assert b - a == timedelta(minutes=30)
