"""
Tests for the guest->account claim (lambdas/common/responses_dynamo.claim_guest_responses
and lambdas/responses_claim).

The sharp edges are all about a guestId identifying a BROWSER rather than a
person, so the rules that keep it honest are what's asserted here.
"""

import os
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

RESPONSES_TABLE_NAME = os.environ["RESPONSES_TABLE_NAME"]
RESPONDENT_INDEX = "respondentKey-pollId-index"


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
            GlobalSecondaryIndexes=[
                {
                    "IndexName": RESPONDENT_INDEX,
                    "KeySchema": [
                        {"AttributeName": "respondentKey", "KeyType": "HASH"},
                        {"AttributeName": "pollId", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
                }
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        yield boto3.resource("dynamodb", region_name="us-east-1").Table(RESPONSES_TABLE_NAME)


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


NOW = datetime.now(timezone.utc)
RECENT = _iso(NOW - timedelta(minutes=5))
OLD = _iso(NOW - timedelta(days=9))
WINDOW = _iso(NOW - timedelta(hours=24))

GUEST = "guest#abc-123"
EMAIL = "dom@example.com"


def _put(table, poll_id, respondent_key, submitted_at=RECENT, **extra):
    item = {
        "pollId": poll_id,
        "respondentKey": respondent_key,
        "displayName": "Dom",
        "blocks": ["2026-08-03T18:00"],
        "submittedAt": submitted_at,
    }
    item.update(extra)
    table.put_item(Item=item)


class TestClaimGuestResponses:
    def test_rekeys_a_recent_guest_response_to_the_account(self, responses_table):
        from lambdas.common.responses_dynamo import claim_guest_responses, get_response

        _put(responses_table, "poll-1", GUEST)

        result = claim_guest_responses(GUEST, EMAIL, WINDOW)

        assert result["claimed"] == ["poll-1"]
        # Moved, not copied: the guest row must not survive.
        assert get_response("poll-1", GUEST) is None
        claimed = get_response("poll-1", EMAIL)
        assert claimed["blocks"] == ["2026-08-03T18:00"]
        assert claimed["claimedFrom"] == GUEST

    def test_skips_responses_older_than_the_window(self, responses_table):
        """A shared laptop must not hand over the previous person's answers."""
        from lambdas.common.responses_dynamo import claim_guest_responses, get_response

        _put(responses_table, "poll-1", GUEST, submitted_at=OLD)

        result = claim_guest_responses(GUEST, EMAIL, WINDOW)

        assert result["claimed"] == []
        assert result["skippedStale"] == ["poll-1"]
        # Left exactly where it was.
        assert get_response("poll-1", GUEST) is not None
        assert get_response("poll-1", EMAIL) is None

    def test_rows_without_submitted_at_are_not_claimed(self, responses_table):
        """No provable age means fail closed, not over-claim."""
        from lambdas.common.responses_dynamo import claim_guest_responses

        responses_table.put_item(
            Item={"pollId": "poll-1", "respondentKey": GUEST, "displayName": "Dom"}
        )

        result = claim_guest_responses(GUEST, EMAIL, WINDOW)

        assert result["claimed"] == []
        assert result["skippedStale"] == ["poll-1"]

    def test_existing_authed_response_wins(self, responses_table):
        """The signed-in answer is the deliberate one; never clobber it."""
        from lambdas.common.responses_dynamo import claim_guest_responses, get_response

        _put(responses_table, "poll-1", GUEST, displayName="GuestMe")
        _put(responses_table, "poll-1", EMAIL, displayName="RealMe")

        result = claim_guest_responses(GUEST, EMAIL, WINDOW)

        assert result["claimed"] == []
        assert result["skippedExisting"] == ["poll-1"]
        assert get_response("poll-1", EMAIL)["displayName"] == "RealMe"
        # The redundant guest row is cleaned up rather than left dangling.
        assert get_response("poll-1", GUEST) is None

    def test_is_idempotent(self, responses_table):
        """A retried claim after a partial failure must not duplicate or error."""
        from lambdas.common.responses_dynamo import claim_guest_responses, get_response

        _put(responses_table, "poll-1", GUEST)

        first = claim_guest_responses(GUEST, EMAIL, WINDOW)
        second = claim_guest_responses(GUEST, EMAIL, WINDOW)

        assert first["claimed"] == ["poll-1"]
        assert second["claimed"] == []
        assert get_response("poll-1", EMAIL) is not None

    def test_claims_across_several_forms(self, responses_table):
        from lambdas.common.responses_dynamo import claim_guest_responses

        _put(responses_table, "poll-1", GUEST)
        _put(responses_table, "poll-2", GUEST)
        _put(responses_table, "poll-3", GUEST, submitted_at=OLD)

        result = claim_guest_responses(GUEST, EMAIL, WINDOW)

        assert sorted(result["claimed"]) == ["poll-1", "poll-2"]
        assert result["skippedStale"] == ["poll-3"]

    def test_leaves_other_guests_alone(self, responses_table):
        from lambdas.common.responses_dynamo import claim_guest_responses, get_response

        _put(responses_table, "poll-1", GUEST)
        _put(responses_table, "poll-1", "guest#someone-else")

        claim_guest_responses(GUEST, EMAIL, WINDOW)

        assert get_response("poll-1", "guest#someone-else") is not None


class TestQueryResponsesByRespondent:
    def test_returns_only_that_respondents_rows(self, responses_table):
        from lambdas.common.responses_dynamo import query_responses_by_respondent

        _put(responses_table, "poll-1", EMAIL)
        _put(responses_table, "poll-2", EMAIL)
        _put(responses_table, "poll-1", "other@example.com")

        rows = query_responses_by_respondent(EMAIL)

        assert sorted(r["pollId"] for r in rows) == ["poll-1", "poll-2"]

    def test_empty_for_someone_who_never_responded(self, responses_table):
        from lambdas.common.responses_dynamo import query_responses_by_respondent

        assert query_responses_by_respondent("nobody@example.com") == []


class TestHasResponded:
    def test_true_only_when_a_row_exists(self, responses_table):
        from lambdas.common.responses_dynamo import has_responded

        _put(responses_table, "poll-1", EMAIL)

        assert has_responded("poll-1", EMAIL) is True
        assert has_responded("poll-1", "other@example.com") is False
