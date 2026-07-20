"""
Tests for lambdas/common/polls_dynamo.py -- CRUD for xomforms-polls.

Table shape (see docs/features/xomforms/PLAN.md):
  PK: pollId
  GSI: creatorEmail-createdAt-index (PK creatorEmail, SK createdAt) -- "my polls"

Written RED-first per Phase 1 of the plan. Uses moto (not just
unittest.mock) since this is the first real DynamoDB-shape validation in
this repo family -- worth actually exercising put/get/query against a
faked table rather than mocking the boto3 client entirely.
"""

import os
import boto3
import pytest
from moto import mock_aws

POLLS_TABLE_NAME = os.environ["POLLS_TABLE_NAME"]
POLLS_CREATOR_INDEX = os.environ["POLLS_CREATOR_INDEX"]


@pytest.fixture
def polls_table():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=POLLS_TABLE_NAME,
            KeySchema=[{"AttributeName": "pollId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "pollId", "AttributeType": "S"},
                {"AttributeName": "creatorEmail", "AttributeType": "S"},
                {"AttributeName": "createdAt", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": POLLS_CREATOR_INDEX,
                    "KeySchema": [
                        {"AttributeName": "creatorEmail", "KeyType": "HASH"},
                        {"AttributeName": "createdAt", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                }
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        yield boto3.resource("dynamodb", region_name="us-east-1").Table(POLLS_TABLE_NAME)


def _sample_poll(poll_id="poll-1", creator_email="creator@example.com", created_at="2026-07-20T10:00:00Z"):
    return {
        "pollId": poll_id,
        "creatorEmail": creator_email,
        "title": "Fantasy Draft",
        "startDate": "2026-08-03",
        "endDate": "2026-08-05",
        "dayStartMinute": 480,
        "dayEndMinute": 840,
        "granularityMinutes": 30,
        "timezone": "America/New_York",
        "guestAllowed": True,
        "showResultsToRespondents": False,
        "createdAt": created_at,
    }


class TestPutAndGetPoll:
    def test_put_then_get_round_trips(self, polls_table):
        from lambdas.common.polls_dynamo import put_poll, get_poll

        poll = _sample_poll()
        put_poll(poll)

        fetched = get_poll("poll-1")
        assert fetched is not None
        assert fetched["title"] == "Fantasy Draft"
        assert fetched["creatorEmail"] == "creator@example.com"

    def test_get_missing_poll_returns_none(self, polls_table):
        from lambdas.common.polls_dynamo import get_poll

        assert get_poll("does-not-exist") is None


class TestQueryPollsByCreator:
    def test_returns_only_that_creators_polls(self, polls_table):
        from lambdas.common.polls_dynamo import put_poll, query_polls_by_creator

        put_poll(_sample_poll(poll_id="poll-1", creator_email="a@example.com"))
        put_poll(_sample_poll(poll_id="poll-2", creator_email="a@example.com", created_at="2026-07-21T10:00:00Z"))
        put_poll(_sample_poll(poll_id="poll-3", creator_email="b@example.com"))

        results = query_polls_by_creator("a@example.com")
        poll_ids = {p["pollId"] for p in results}
        assert poll_ids == {"poll-1", "poll-2"}

    def test_returns_empty_list_for_creator_with_no_polls(self, polls_table):
        from lambdas.common.polls_dynamo import query_polls_by_creator

        assert query_polls_by_creator("nobody@example.com") == []
