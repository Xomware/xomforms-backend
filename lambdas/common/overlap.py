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

from lambdas.common.logger import get_logger
from lambdas.common.errors import NotFoundError
from lambdas.common.polls_dynamo import get_poll
from lambdas.common.responses_dynamo import get_responses_for_poll
from lambdas.common.timezone import generate_grid

log = get_logger(__file__)


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

    log.info(f"compute_overlap poll={poll_id} respondents={total_respondents} best={best_block_ids}")

    return {
        "pollId": poll_id,
        "totalRespondents": total_respondents,
        "blocks": blocks,
        "bestBlockIds": best_block_ids,
    }
