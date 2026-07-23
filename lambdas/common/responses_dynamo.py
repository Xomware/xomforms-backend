"""
XOMFORMS Responses DynamoDB Helpers
=====================================
Database operations for the xomforms-responses table.

Table Structure:
- PK: pollId
- SK: respondentKey (email for authed respondents, "guest#<uuid>" for guests)
- TTL attribute: closeAt (epoch seconds) -- denormalized from the poll so
  DynamoDB can auto-expire response rows once a poll closes. Omitted when
  the poll has no closeAt configured.

upsert_response() is naturally idempotent: put_item on the same
(pollId, respondentKey) key overwrites the previous item rather than
creating a duplicate -- this is what "idempotent upsert by respondentKey"
means in Phase 1 of docs/features/xomforms/PLAN.md.
"""

from datetime import datetime
import boto3

from lambdas.common.logger import get_logger
from lambdas.common.errors import DynamoDBError, ValidationError
from lambdas.common.constants import RESPONSES_TABLE_NAME
from lambdas.common.timezone import generate_grid

log = get_logger(__file__)
dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

# Sentinel field id used to synthesize `answers` for a LEGACY scheduler poll
# (no `fields`): its availability answer has no declared fieldId, so the shim
# keys it here. A qa poll that declares an explicit `availability` field keys
# under that field's real id instead (see availability_field_id).
AVAILABILITY_SENTINEL = "__availability__"

# Choice-type fields whose answer is a list of optionIds.
_CHOICE_TYPES = {"single_choice", "multi_choice", "dropdown"}


def _to_epoch_seconds(iso_timestamp: str) -> int:
    # Accept both "...Z" and "+00:00" suffixed ISO 8601 strings.
    normalized = iso_timestamp.replace("Z", "+00:00")
    return int(datetime.fromisoformat(normalized).timestamp())


def upsert_response(
    poll_id: str,
    respondent_key: str,
    display_name: str,
    blocks: list[str],
    close_at: str | None = None,
) -> bool:
    """
    Write (or overwrite) a respondent's availability for a poll.

    Idempotent by (poll_id, respondent_key) -- repeat calls with the same
    key replace the prior item rather than duplicating it.
    """
    try:
        table = dynamodb.Table(RESPONSES_TABLE_NAME)
        item = {
            "pollId": poll_id,
            "respondentKey": respondent_key,
            "displayName": display_name,
            "blocks": sorted(set(blocks)),
        }
        if close_at:
            item["closeAt"] = _to_epoch_seconds(close_at)

        table.put_item(Item=item)
        log.info(f"Response upserted: poll={poll_id} respondent={respondent_key}")
        return True
    except Exception as err:
        log.error(f"Upsert response failed: {err}")
        raise DynamoDBError(message=str(err), function="upsert_response", table=RESPONSES_TABLE_NAME)


def get_responses_for_poll(poll_id: str) -> list[dict]:
    """Fetch every response item for a poll (query by PK, scoped to that poll only)."""
    try:
        table = dynamodb.Table(RESPONSES_TABLE_NAME)
        items: list[dict] = []
        kwargs = {"KeyConditionExpression": boto3.dynamodb.conditions.Key("pollId").eq(poll_id)}
        while True:
            res = table.query(**kwargs)
            items.extend(res.get("Items", []))
            last_key = res.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return items
    except Exception as err:
        log.error(f"Get responses for poll failed: {err}")
        raise DynamoDBError(message=str(err), function="get_responses_for_poll", table=RESPONSES_TABLE_NAME)


def submit_availability(poll: dict, respondent_key: str, display_name: str, blocks: list[str]) -> dict:
    """
    Shared core for lambdas/responses_submit_authed and
    lambdas/responses_submit_public (per docs/features/xomforms/PLAN.md) --
    the two handlers differ only in how respondent_key is resolved (email
    vs "guest#<uuid>") and the guestAllowed gate, both handled by the
    caller before this runs.

    Validates every submitted blockId is a real block in the poll's own
    grid (rejects stale/forged blockIds -- e.g. from a different poll, or
    outside the configured date range/time window) before persisting.
    """
    grid_block_ids = {b["blockId"] for b in generate_grid(poll)}
    invalid = [b for b in blocks if b not in grid_block_ids]
    if invalid:
        raise ValidationError(
            message=f"blocks contains ids outside this poll's grid: {invalid[:5]}",
            function="submit_availability",
            field="blocks",
        )

    upsert_response(
        poll_id=poll["pollId"],
        respondent_key=respondent_key,
        display_name=display_name,
        blocks=blocks,
        close_at=poll.get("closeAt"),
    )

    return {
        "pollId": poll["pollId"],
        "respondentKey": respondent_key,
        "displayName": display_name,
        "blocks": sorted(set(blocks)),
    }


# ---------------------------------------------------------------------------
# Q&A forms (additive). submit_availability above is the scheduler path and is
# never touched by any of the following. See
# docs/features/xomforms-form-builder/PLAN.md.
# ---------------------------------------------------------------------------


def availability_field_id(poll: dict) -> str:
    """
    The fieldId under which a poll's availability answer lives.

    A qa poll may declare an explicit `availability` field -> use its id. A
    legacy scheduler poll has no `fields` -> use the sentinel so its `blocks`
    can be read uniformly as an answer.
    """
    for field in poll.get("fields") or []:
        if field.get("type") == "availability":
            return field["fieldId"]
    return AVAILABILITY_SENTINEL


def read_answers(item: dict, poll: dict) -> dict:
    """
    Back-compat read shim: return a response item's answers map regardless of
    how it was written.

    - New Q&A items store `answers` directly -> return it.
    - Legacy scheduler items store `blocks` only -> synthesize
      `{ availability_field_id(poll): blocks }` so the analytics engine sees a
      uniform fieldId -> value shape. NO backfill is written; this is read-only.
    """
    if "answers" in item and item["answers"] is not None:
        return item["answers"]
    return {availability_field_id(poll): item.get("blocks", [])}


def upsert_answers(
    poll_id: str,
    respondent_key: str,
    display_name: str,
    answers: dict,
    close_at: str | None = None,
) -> bool:
    """
    Write (or overwrite) a respondent's Q&A answers for a poll. Idempotent by
    (poll_id, respondent_key) -- the same key replaces the prior item. Writes
    `answers` (never `blocks`); the read shim handles either.
    """
    try:
        table = dynamodb.Table(RESPONSES_TABLE_NAME)
        item = {
            "pollId": poll_id,
            "respondentKey": respondent_key,
            "displayName": display_name,
            "answers": answers,
        }
        if close_at:
            item["closeAt"] = _to_epoch_seconds(close_at)

        table.put_item(Item=item)
        log.info(f"Answers upserted: poll={poll_id} respondent={respondent_key}")
        return True
    except Exception as err:
        log.error(f"Upsert answers failed: {err}")
        raise DynamoDBError(message=str(err), function="upsert_answers", table=RESPONSES_TABLE_NAME)


def _validate_answer_for_field(field: dict, value) -> object:
    """
    Validate + normalize one field's answer value against its declared type.
    The generalization of submit_availability's "reject blockIds outside the
    grid" guard. Returns the normalized value to persist.
    """
    field_type = field["type"]
    field_id = field["fieldId"]

    if field_type in _CHOICE_TYPES:
        if not isinstance(value, list):
            raise ValidationError(
                message=f"answer for '{field_id}' must be a list of optionIds",
                function="submit_answers",
                field=field_id,
            )
        valid_ids = {o["optionId"] for o in field.get("options", [])}
        invalid = [v for v in value if v not in valid_ids]
        if invalid:
            raise ValidationError(
                message=f"answer for '{field_id}' has unknown optionIds: {invalid[:5]}",
                function="submit_answers",
                field=field_id,
            )
        if field_type in ("single_choice", "dropdown") and len(value) > 1:
            raise ValidationError(
                message=f"'{field_id}' accepts at most one selection",
                function="submit_answers",
                field=field_id,
            )
        # Preserve deterministic order; drop dupes.
        return sorted(set(value))

    if field_type == "scale":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError(
                message=f"answer for '{field_id}' must be a number",
                function="submit_answers",
                field=field_id,
            )
        ivalue = int(value)
        if ivalue != value or ivalue < field["min"] or ivalue > field["max"]:
            raise ValidationError(
                message=f"answer for '{field_id}' must be an integer in [{field['min']}, {field['max']}]",
                function="submit_answers",
                field=field_id,
            )
        return ivalue

    raise ValidationError(
        message=f"unsupported field type '{field_type}' for '{field_id}'",
        function="submit_answers",
        field=field_id,
    )


def _is_blank_answer(value) -> bool:
    return value is None or value == [] or value == ""


def submit_answers(poll: dict, respondent_key: str, display_name: str, answers: dict) -> dict:
    """
    Shared core for the authed/public submit handlers when the poll is a qa
    form. Validates every answer against the poll's declared fields (unknown
    fieldIds and out-of-set/out-of-range values are rejected), enforces
    required fields, then persists via upsert_answers.
    """
    fields_by_id = {f["fieldId"]: f for f in poll.get("fields") or []}

    # Reject answers for fields this poll doesn't declare (stale/forged).
    unknown = [fid for fid in answers if fid not in fields_by_id]
    if unknown:
        raise ValidationError(
            message=f"answers reference unknown fieldIds: {unknown[:5]}",
            function="submit_answers",
            field="answers",
        )

    normalized: dict = {}
    for field_id, field in fields_by_id.items():
        value = answers.get(field_id)
        if _is_blank_answer(value):
            if field.get("required"):
                raise ValidationError(
                    message=f"'{field_id}' is required",
                    function="submit_answers",
                    field=field_id,
                )
            continue
        normalized[field_id] = _validate_answer_for_field(field, value)

    upsert_answers(
        poll_id=poll["pollId"],
        respondent_key=respondent_key,
        display_name=display_name,
        answers=normalized,
        close_at=poll.get("closeAt"),
    )

    return {
        "pollId": poll["pollId"],
        "respondentKey": respondent_key,
        "displayName": display_name,
        "answers": normalized,
    }
