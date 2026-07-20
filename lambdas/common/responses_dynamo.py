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
from lambdas.common.errors import DynamoDBError
from lambdas.common.constants import RESPONSES_TABLE_NAME

log = get_logger(__file__)
dynamodb = boto3.resource("dynamodb", region_name="us-east-1")


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
