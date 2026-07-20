"""
Tests for lambdas/common/responses_dynamo.py -- upsert/query for
xomforms-responses.

Table Structure (see docs/features/xomforms/PLAN.md):
  PK: pollId
  SK: respondentKey (email, or "guest#<uuid>")
  TTL: closeAt (epoch seconds, denormalized from the poll so DynamoDB can
       auto-expire response rows once a poll closes)

Written RED-first per Phase 1 of the plan.
"""

import os
import boto3
import pytest
from moto import mock_aws

RESPONSES_TABLE_NAME = os.environ["RESPONSES_TABLE_NAME"]


@pytest.fixture
def responses_table():
    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName=RESPONSES_TABLE_NAME,
            KeySchema=[
                {"AttributeName": "pollId", "KeyType": "HASH"},
                {"AttributeName": "respondentKey", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pollId", "AttributeType": "S"},
                {"AttributeName": "respondentKey", "AttributeType": "S"},
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        yield boto3.resource("dynamodb", region_name="us-east-1").Table(RESPONSES_TABLE_NAME)


class TestUpsertResponse:
    def test_creates_new_response(self, responses_table):
        from lambdas.common.responses_dynamo import upsert_response, get_responses_for_poll

        upsert_response(
            poll_id="poll-1",
            respondent_key="dom@example.com",
            display_name="Dom",
            blocks=["2026-08-03T08:00", "2026-08-03T08:30"],
        )

        responses = get_responses_for_poll("poll-1")
        assert len(responses) == 1
        assert responses[0]["displayName"] == "Dom"
        assert responses[0]["blocks"] == ["2026-08-03T08:00", "2026-08-03T08:30"]

    def test_resubmit_by_same_respondent_key_overwrites_not_duplicates(self, responses_table):
        """Idempotent upsert: same (pollId, respondentKey) -> one item, latest wins."""
        from lambdas.common.responses_dynamo import upsert_response, get_responses_for_poll

        upsert_response(
            poll_id="poll-1",
            respondent_key="dom@example.com",
            display_name="Dom",
            blocks=["2026-08-03T08:00"],
        )
        upsert_response(
            poll_id="poll-1",
            respondent_key="dom@example.com",
            display_name="Dom",
            blocks=["2026-08-03T09:00", "2026-08-03T09:30"],
        )

        responses = get_responses_for_poll("poll-1")
        assert len(responses) == 1  # not 2 -- overwrite, not append
        assert responses[0]["blocks"] == ["2026-08-03T09:00", "2026-08-03T09:30"]

    def test_different_respondents_produce_separate_items(self, responses_table):
        from lambdas.common.responses_dynamo import upsert_response, get_responses_for_poll

        upsert_response(poll_id="poll-1", respondent_key="a@example.com", display_name="A", blocks=[])
        upsert_response(poll_id="poll-1", respondent_key="guest#abc123", display_name="Guest A", blocks=[])

        responses = get_responses_for_poll("poll-1")
        assert len(responses) == 2

    def test_close_at_is_stored_as_ttl_epoch_seconds(self, responses_table):
        from lambdas.common.responses_dynamo import upsert_response, get_responses_for_poll

        upsert_response(
            poll_id="poll-1",
            respondent_key="dom@example.com",
            display_name="Dom",
            blocks=[],
            close_at="2026-09-01T00:00:00Z",
        )

        responses = get_responses_for_poll("poll-1")
        # boto3's DynamoDB *resource* API returns numeric attributes as
        # Decimal, not int -- that's expected SDK behavior (same reason
        # utility_helpers.XomformsJSONEncoder handles Decimal on the way
        # out to JSON). We only assert it's a whole-number epoch timestamp.
        close_at = responses[0]["closeAt"]
        assert close_at == int(close_at)
        assert close_at > 0

    def test_close_at_omitted_when_poll_has_no_close_at(self, responses_table):
        from lambdas.common.responses_dynamo import upsert_response, get_responses_for_poll

        upsert_response(poll_id="poll-1", respondent_key="dom@example.com", display_name="Dom", blocks=[])

        responses = get_responses_for_poll("poll-1")
        assert "closeAt" not in responses[0]


class TestGetResponsesForPoll:
    def test_returns_empty_list_for_poll_with_no_responses(self, responses_table):
        from lambdas.common.responses_dynamo import get_responses_for_poll

        assert get_responses_for_poll("no-such-poll") == []

    def test_scoped_to_the_requested_poll_only(self, responses_table):
        from lambdas.common.responses_dynamo import upsert_response, get_responses_for_poll

        upsert_response(poll_id="poll-1", respondent_key="a@example.com", display_name="A", blocks=[])
        upsert_response(poll_id="poll-2", respondent_key="a@example.com", display_name="A", blocks=[])

        assert len(get_responses_for_poll("poll-1")) == 1
        assert len(get_responses_for_poll("poll-2")) == 1
