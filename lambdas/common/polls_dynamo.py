"""
XOMFORMS Polls DynamoDB Helpers
================================
Database operations for the xomforms-polls table.

Table Structure:
- PK: pollId (string/uuid)
- GSI creatorEmail-createdAt-index: PK creatorEmail, SK createdAt -- powers
  "my polls" (polls_list).
"""

import boto3

from lambdas.common.logger import get_logger
from lambdas.common.errors import DynamoDBError, ForbiddenError, NotFoundError
from lambdas.common.constants import POLLS_TABLE_NAME, POLLS_CREATOR_INDEX

log = get_logger(__file__)
dynamodb = boto3.resource("dynamodb", region_name="us-east-1")


def put_poll(poll: dict) -> bool:
    """Write a poll item. Overwrites on repeat calls with the same pollId."""
    try:
        table = dynamodb.Table(POLLS_TABLE_NAME)
        table.put_item(Item=poll)
        log.info(f"Poll written: {poll.get('pollId')}")
        return True
    except Exception as err:
        log.error(f"Put poll failed: {err}")
        raise DynamoDBError(message=str(err), function="put_poll", table=POLLS_TABLE_NAME)


def get_poll(poll_id: str) -> dict | None:
    """Fetch a single poll by id. Returns None if not found."""
    try:
        table = dynamodb.Table(POLLS_TABLE_NAME)
        res = table.get_item(Key={"pollId": poll_id})
        return res.get("Item")
    except Exception as err:
        log.error(f"Get poll failed: {err}")
        raise DynamoDBError(message=str(err), function="get_poll", table=POLLS_TABLE_NAME)


def query_polls_by_creator(creator_email: str) -> list[dict]:
    """Query all polls created by a given email via the creatorEmail-createdAt GSI."""
    try:
        table = dynamodb.Table(POLLS_TABLE_NAME)
        items: list[dict] = []
        kwargs = {
            "IndexName": POLLS_CREATOR_INDEX,
            "KeyConditionExpression": boto3.dynamodb.conditions.Key("creatorEmail").eq(creator_email),
        }
        while True:
            res = table.query(**kwargs)
            items.extend(res.get("Items", []))
            last_key = res.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key
        return items
    except Exception as err:
        log.error(f"Query polls by creator failed: {err}")
        raise DynamoDBError(message=str(err), function="query_polls_by_creator", table=POLLS_TABLE_NAME)


def get_poll_for_creator(poll_id: str, creator_email: str, function: str = "unknown") -> dict:
    """
    Fetch a poll and assert the caller created it, for mutating creator-only
    routes (close/delete).

    This check is security-critical and deliberately lives in ONE place so the
    two callers can't drift: polls_get is a PUBLIC route, so any pollId is
    discoverable by anyone holding a share link. Being able to name a poll must
    never imply being able to close or destroy it.
    """
    poll = get_poll(poll_id)
    if poll is None:
        raise NotFoundError(
            message=f"Poll '{poll_id}' not found", function=function, resource="poll"
        )
    if poll.get("creatorEmail") != creator_email:
        log.warning(f"Rejected non-creator mutation of poll {poll_id} by {creator_email}")
        raise ForbiddenError(
            message="Only the form's creator can modify it",
            function=function,
            reason="not_creator",
        )
    return poll


def delete_poll(poll_id: str) -> bool:
    """
    Delete a poll item. Idempotent -- deleting a missing pollId is a no-op.
    Callers MUST delete the poll's responses first (see
    responses_dynamo.delete_responses_for_poll); nothing here cascades.
    """
    try:
        table = dynamodb.Table(POLLS_TABLE_NAME)
        table.delete_item(Key={"pollId": poll_id})
        log.info(f"Poll deleted: {poll_id}")
        return True
    except Exception as err:
        log.error(f"Delete poll failed: {err}")
        raise DynamoDBError(message=str(err), function="delete_poll", table=POLLS_TABLE_NAME)


def set_poll_close_at(poll_id: str, close_at: str | None) -> bool:
    """
    Set or clear a poll's closeAt. An ISO timestamp closes the poll; None
    REMOVEs the attribute entirely, reopening it (derive_poll_status on the
    frontend and the responses TTL both key off presence/absence, so removing
    is meaningfully different from writing an empty string).
    """
    try:
        table = dynamodb.Table(POLLS_TABLE_NAME)
        if close_at is None:
            table.update_item(
                Key={"pollId": poll_id},
                UpdateExpression="REMOVE closeAt",
            )
            log.info(f"Poll reopened: {poll_id}")
        else:
            table.update_item(
                Key={"pollId": poll_id},
                UpdateExpression="SET closeAt = :c",
                ExpressionAttributeValues={":c": close_at},
            )
            log.info(f"Poll closed: {poll_id} at {close_at}")
        return True
    except Exception as err:
        log.error(f"Set poll closeAt failed: {err}")
        raise DynamoDBError(message=str(err), function="set_poll_close_at", table=POLLS_TABLE_NAME)


def update_poll_attributes(poll_id: str, changes: dict) -> dict:
    """
    Write a partial set of attributes onto a poll and return the updated item.

    UpdateExpression rather than put_item so only the supplied keys move --
    a put would need the caller to echo the whole poll back, and any attribute
    they omitted (or hadn't heard of) would be silently erased.
    """
    if not changes:
        return get_poll(poll_id) or {}
    try:
        table = dynamodb.Table(POLLS_TABLE_NAME)
        # Attribute names are aliased because several are DynamoDB reserved
        # words (e.g. "title"), which cannot appear bare in an expression.
        names = {f"#k{i}": key for i, key in enumerate(changes)}
        values = {f":v{i}": value for i, value in enumerate(changes.values())}
        sets = ", ".join(f"#k{i} = :v{i}" for i in range(len(changes)))
        res = table.update_item(
            Key={"pollId": poll_id},
            UpdateExpression=f"SET {sets}",
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
        log.info(f"Poll updated: {poll_id} fields={list(changes)}")
        return res.get("Attributes", {})
    except Exception as err:
        log.error(f"Update poll failed: {err}")
        raise DynamoDBError(
            message=str(err), function="update_poll_attributes", table=POLLS_TABLE_NAME
        )
