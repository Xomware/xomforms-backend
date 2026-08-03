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


class TestDeletePoll:
    def test_delete_removes_the_item(self, polls_table):
        from lambdas.common.polls_dynamo import put_poll, get_poll, delete_poll

        put_poll(_sample_poll())
        assert get_poll("poll-1") is not None

        delete_poll("poll-1")
        assert get_poll("poll-1") is None

    def test_delete_is_idempotent(self, polls_table):
        """Deleting an already-gone poll must not raise -- a retried request
        after a partial failure has to be safe."""
        from lambdas.common.polls_dynamo import delete_poll

        assert delete_poll("never-existed") is True

    def test_delete_leaves_other_polls_alone(self, polls_table):
        from lambdas.common.polls_dynamo import put_poll, get_poll, delete_poll

        put_poll(_sample_poll(poll_id="poll-1"))
        put_poll(_sample_poll(poll_id="poll-2"))

        delete_poll("poll-1")

        assert get_poll("poll-1") is None
        assert get_poll("poll-2") is not None


class TestSetPollCloseAt:
    def test_sets_close_at(self, polls_table):
        from lambdas.common.polls_dynamo import put_poll, get_poll, set_poll_close_at

        put_poll(_sample_poll())
        set_poll_close_at("poll-1", "2026-08-04T00:00:00Z")

        assert get_poll("poll-1")["closeAt"] == "2026-08-04T00:00:00Z"

    def test_reopening_removes_the_attribute_entirely(self, polls_table):
        """Status derives from PRESENCE of closeAt, so reopening must REMOVE
        it rather than write an empty value."""
        from lambdas.common.polls_dynamo import put_poll, get_poll, set_poll_close_at

        put_poll(_sample_poll())
        set_poll_close_at("poll-1", "2026-08-04T00:00:00Z")
        set_poll_close_at("poll-1", None)

        assert "closeAt" not in get_poll("poll-1")

    def test_leaves_other_attributes_untouched(self, polls_table):
        from lambdas.common.polls_dynamo import put_poll, get_poll, set_poll_close_at

        put_poll(_sample_poll())
        set_poll_close_at("poll-1", "2026-08-04T00:00:00Z")

        poll = get_poll("poll-1")
        assert poll["title"] == "Fantasy Draft"
        assert poll["creatorEmail"] == "creator@example.com"


class TestGetPollForCreator:
    def test_returns_the_poll_for_its_creator(self, polls_table):
        from lambdas.common.polls_dynamo import put_poll, get_poll_for_creator

        put_poll(_sample_poll())
        poll = get_poll_for_creator("poll-1", "creator@example.com")
        assert poll["pollId"] == "poll-1"

    def test_raises_forbidden_for_a_non_creator(self, polls_table):
        """pollIds are public (polls_get is unauthenticated), so naming a poll
        must never imply the right to mutate it."""
        from lambdas.common.errors import ForbiddenError
        from lambdas.common.polls_dynamo import put_poll, get_poll_for_creator

        put_poll(_sample_poll())
        with pytest.raises(ForbiddenError):
            get_poll_for_creator("poll-1", "intruder@example.com")

    def test_raises_not_found_for_a_missing_poll(self, polls_table):
        from lambdas.common.errors import NotFoundError
        from lambdas.common.polls_dynamo import get_poll_for_creator

        with pytest.raises(NotFoundError):
            get_poll_for_creator("nope", "creator@example.com")
