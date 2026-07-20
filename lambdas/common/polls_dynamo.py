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
from lambdas.common.errors import DynamoDBError
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
