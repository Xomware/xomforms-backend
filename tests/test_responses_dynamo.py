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


def _sample_poll(poll_id="poll-1", **overrides):
    poll = {
        "pollId": poll_id,
        "creatorEmail": "creator@example.com",
        "title": "Fantasy Draft",
        "startDate": "2026-08-03",
        "endDate": "2026-08-03",
        "dayStartMinute": 8 * 60,
        "dayEndMinute": 9 * 60,
        "granularityMinutes": 30,
        "timezone": "America/New_York",
        "guestAllowed": True,
        "showResultsToRespondents": False,
    }
    poll.update(overrides)
    return poll


class TestSubmitAvailability:
    """
    submit_availability(poll, respondent_key, display_name, blocks) is the
    core shared by responses_submit_authed and responses_submit_public per
    docs/features/xomforms/PLAN.md -- the two handlers differ only in how
    respondent_key is resolved and the guestAllowed gate (handled in the
    handlers, not here).
    """

    def test_accepts_blocks_that_are_in_the_polls_grid(self, responses_table):
        from lambdas.common.responses_dynamo import submit_availability, get_responses_for_poll

        poll = _sample_poll()
        result = submit_availability(poll, "dom@example.com", "Dom", ["2026-08-03T08:00", "2026-08-03T08:30"])

        assert result["blocks"] == ["2026-08-03T08:00", "2026-08-03T08:30"]
        stored = get_responses_for_poll("poll-1")
        assert len(stored) == 1
        assert stored[0]["respondentKey"] == "dom@example.com"

    def test_rejects_block_ids_outside_the_polls_grid(self, responses_table):
        """Guards against a stale or forged blockId (e.g. from a different
        poll, or outside the configured time window) being persisted."""
        from lambdas.common.responses_dynamo import submit_availability
        from lambdas.common.errors import ValidationError

        poll = _sample_poll()
        try:
            submit_availability(poll, "dom@example.com", "Dom", ["2026-08-03T08:00", "1999-01-01T00:00"])
            assert False, "expected ValidationError"
        except ValidationError:
            pass

    def test_accepts_empty_blocks_as_no_availability(self, responses_table):
        from lambdas.common.responses_dynamo import submit_availability

        poll = _sample_poll()
        result = submit_availability(poll, "dom@example.com", "Dom", [])
        assert result["blocks"] == []

    def test_denormalizes_poll_close_at_onto_the_response(self, responses_table):
        from lambdas.common.responses_dynamo import submit_availability, get_responses_for_poll

        poll = _sample_poll(closeAt="2026-09-01T00:00:00Z")
        submit_availability(poll, "dom@example.com", "Dom", [])

        stored = get_responses_for_poll("poll-1")
        assert "closeAt" in stored[0]
