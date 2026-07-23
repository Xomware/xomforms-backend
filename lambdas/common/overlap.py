"""
XOMFORMS Overlap Computation
=============================
compute_overlap(poll_id): compute-on-read per-block tally across all
responses to a poll, plus the ranked best time(s). MVP scoring is
per-block max only (no contiguous-window / "find a 2-hour block" scoring --
that's deferred to v2 per docs/features/xomforms/PLAN.md).

Tie-break rule (per the plan's "Best-time logic" assumption): all blocks
tied at the max count are returned in bestBlockIds, in chronological
(earliest-first) order. There is no if-need-be tier in the MVP model, so
"fewest if-need-be then earliest" collapses to just "earliest" for now.
"""

import math

from lambdas.common.logger import get_logger
from lambdas.common.errors import NotFoundError
from lambdas.common.polls_dynamo import get_poll
from lambdas.common.responses_dynamo import get_responses_for_poll
from lambdas.common.timezone import generate_grid

log = get_logger(__file__)


def _compute_best_window(blocks: list[dict], respondent_sets: list[set], slot_count: int) -> tuple[list[str], int]:
    """
    Find the best contiguous START window of length `slot_count` slots.

    A window is `slot_count` consecutive grid blocks WITHIN A SINGLE DAY (a
    window may never straddle the day-end gap into the next day's first slot).
    Its score is the number of respondents free for the *entire* window --
    i.e. whose selected blocks are a superset of every slot in the window.

    Returns (bestWindowStartIds, bestWindowCount): all start blockIds tied at
    the max score in chronological order, and that score. Empty/0 when there
    are no respondents or no valid window fits.
    """
    if slot_count < 1:
        slot_count = 1

    # Group blocks by their calendar date prefix ("YYYY-MM-DD"), preserving the
    # incoming chronological order so consecutive list indices are consecutive
    # in wall-clock time within a day.
    by_day: dict[str, list[dict]] = {}
    for block in blocks:
        day = block["blockId"].split("T")[0]
        by_day.setdefault(day, []).append(block)

    best_count = 0
    best_start_ids: list[str] = []
    for day in sorted(by_day.keys()):
        day_blocks = by_day[day]
        for i in range(len(day_blocks) - slot_count + 1):
            window = day_blocks[i : i + slot_count]
            window_ids = {b["blockId"] for b in window}
            count = sum(1 for s in respondent_sets if window_ids <= s)
            if count == 0:
                continue
            start_id = window[0]["blockId"]
            if count > best_count:
                best_count = count
                best_start_ids = [start_id]
            elif count == best_count:
                best_start_ids.append(start_id)

    return best_start_ids, best_count


def compute_overlap(poll_id: str) -> dict:
    """
    Returns:
        {
          "pollId": str,
          "totalRespondents": int,
          "blocks": [{"blockId", "utcInstant", "count", "total", "ratio"}, ...],
          "bestBlockIds": [str, ...],  # chronological, all tied at max count
        }
    """
    poll = get_poll(poll_id)
    if poll is None:
        raise NotFoundError(message=f"Poll '{poll_id}' not found", function="compute_overlap", resource="poll")

    responses = get_responses_for_poll(poll_id)
    total_respondents = len(responses)

    tally: dict[str, int] = {}
    for response in responses:
        for block_id in response.get("blocks", []):
            tally[block_id] = tally.get(block_id, 0) + 1

    grid = generate_grid(poll)  # chronologically ordered by generate_grid()

    blocks = []
    max_count = 0
    for block in grid:
        block_id = block["blockId"]
        count = tally.get(block_id, 0)
        max_count = max(max_count, count)
        ratio = (count / total_respondents) if total_respondents > 0 else 0.0
        blocks.append({
            "blockId": block_id,
            "utcInstant": block["utcInstant"],
            "count": count,
            "total": total_respondents,
            "ratio": ratio,
        })

    if total_respondents > 0 and max_count > 0:
        best_block_ids = [b["blockId"] for b in blocks if b["count"] == max_count]
    else:
        best_block_ids = []

    # Best contiguous START window of the poll's event length. eventDurationMinutes
    # defaults to one slot (granularity) for polls created before the field
    # existed, so single-slot events collapse to the per-block best.
    granularity = int(poll["granularityMinutes"])
    event_duration = int(poll.get("eventDurationMinutes") or granularity)
    slot_count = max(1, math.ceil(event_duration / granularity))

    respondent_sets = [set(r.get("blocks", [])) for r in responses]
    best_window_start_ids, best_window_count = _compute_best_window(blocks, respondent_sets, slot_count)

    log.info(
        f"compute_overlap poll={poll_id} respondents={total_respondents} "
        f"best={best_block_ids} slotCount={slot_count} bestWindow={best_window_start_ids}"
    )

    return {
        "pollId": poll_id,
        "totalRespondents": total_respondents,
        "blocks": blocks,
        "bestBlockIds": best_block_ids,
        "eventDurationMinutes": event_duration,
        "slotCount": slot_count,
        "bestWindowStartIds": best_window_start_ids,
        "bestWindowCount": best_window_count,
    }
