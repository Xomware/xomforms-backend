"""
Tests for the blocks->answers back-compat shim + submit_answers() validation
in lambdas/common/responses_dynamo.py.

Phase 1: a legacy scheduler response item has `blocks` only; a Q&A response
has `answers` (fieldId -> value). read_answers() reads whichever is present,
synthesizing `answers` for legacy items so downstream analytics is uniform.
submit_answers() validates per-field values against the poll's declared field
definitions -- the generalization of submit_availability's "reject blockIds
outside the grid" guard. submit_availability is UNTOUCHED (see
test_scheduler_golden.py).

Written RED-first.
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


def _qa_poll(**overrides):
    poll = {
        "pollId": "poll-qa",
        "creatorEmail": "creator@example.com",
        "title": "RSVP",
        "formType": "qa",
        "guestAllowed": True,
        "showResultsToRespondents": False,
        "fields": [
            {
                "fieldId": "f1",
                "type": "single_choice",
                "label": "Attending?",
                "required": True,
                "options": [
                    {"optionId": "o1", "label": "Yes"},
                    {"optionId": "o2", "label": "No"},
                ],
            },
            {
                "fieldId": "f2",
                "type": "multi_choice",
                "label": "Sessions",
                "options": [
                    {"optionId": "s1", "label": "AM"},
                    {"optionId": "s2", "label": "PM"},
                ],
            },
            {"fieldId": "f3", "type": "scale", "label": "Excitement", "min": 1, "max": 5},
        ],
    }
    poll.update(overrides)
    return poll


def _legacy_scheduler_poll():
    """A poll with NO fields/formType -- the current live scheduler shape."""
    return {
        "pollId": "poll-sched",
        "creatorEmail": "creator@example.com",
        "title": "Draft",
        "startDate": "2026-08-03",
        "endDate": "2026-08-03",
        "dayStartMinute": 8 * 60,
        "dayEndMinute": 9 * 60,
        "granularityMinutes": 30,
        "timezone": "America/New_York",
        "guestAllowed": True,
        "showResultsToRespondents": False,
    }


class TestReadAnswersShim:
    def test_legacy_blocks_item_is_synthesized_into_answers(self):
        from lambdas.common.responses_dynamo import read_answers

        item = {"pollId": "poll-sched", "respondentKey": "a@x.com", "blocks": ["2026-08-03T08:00"]}
        answers = read_answers(item, _legacy_scheduler_poll())

        # A legacy poll has no availability field id, so it lands under the sentinel.
        assert answers == {"__availability__": ["2026-08-03T08:00"]}

    def test_item_with_answers_is_returned_as_is(self):
        from lambdas.common.responses_dynamo import read_answers

        item = {"pollId": "poll-qa", "respondentKey": "a@x.com", "answers": {"f1": ["o1"], "f3": 4}}
        answers = read_answers(item, _qa_poll())
        assert answers == {"f1": ["o1"], "f3": 4}

    def test_legacy_item_missing_blocks_defaults_to_empty_list(self):
        from lambdas.common.responses_dynamo import read_answers

        item = {"pollId": "poll-sched", "respondentKey": "a@x.com"}
        answers = read_answers(item, _legacy_scheduler_poll())
        assert answers == {"__availability__": []}


class TestSubmitAnswers:
    def test_persists_valid_answers(self, responses_table):
        from lambdas.common.responses_dynamo import submit_answers, get_responses_for_poll

        result = submit_answers(
            _qa_poll(), "sam@x.com", "Sam", {"f1": ["o1"], "f2": ["s1", "s2"], "f3": 5}
        )
        assert result["answers"]["f1"] == ["o1"]

        stored = get_responses_for_poll("poll-qa")
        assert len(stored) == 1
        assert stored[0]["answers"]["f3"] == 5
        assert "blocks" not in stored[0]

    def test_rejects_unknown_option_id(self, responses_table):
        from lambdas.common.responses_dynamo import submit_answers
        from lambdas.common.errors import ValidationError

        with pytest.raises(ValidationError):
            submit_answers(_qa_poll(), "sam@x.com", "Sam", {"f1": ["nope"]})

    def test_rejects_scale_out_of_range(self, responses_table):
        from lambdas.common.responses_dynamo import submit_answers
        from lambdas.common.errors import ValidationError

        with pytest.raises(ValidationError):
            submit_answers(_qa_poll(), "sam@x.com", "Sam", {"f3": 99})

    def test_single_choice_rejects_multiple_selections(self, responses_table):
        from lambdas.common.responses_dynamo import submit_answers
        from lambdas.common.errors import ValidationError

        with pytest.raises(ValidationError):
            submit_answers(_qa_poll(), "sam@x.com", "Sam", {"f1": ["o1", "o2"]})

    def test_rejects_unknown_field_id(self, responses_table):
        from lambdas.common.responses_dynamo import submit_answers
        from lambdas.common.errors import ValidationError

        with pytest.raises(ValidationError):
            submit_answers(_qa_poll(), "sam@x.com", "Sam", {"ghost": ["o1"]})

    def test_required_field_must_be_answered(self, responses_table):
        from lambdas.common.responses_dynamo import submit_answers
        from lambdas.common.errors import ValidationError

        # f1 is required; omitting it must fail.
        with pytest.raises(ValidationError):
            submit_answers(_qa_poll(), "sam@x.com", "Sam", {"f3": 3})

    def test_optional_fields_may_be_omitted(self, responses_table):
        from lambdas.common.responses_dynamo import submit_answers

        result = submit_answers(_qa_poll(), "sam@x.com", "Sam", {"f1": ["o2"]})
        assert result["answers"]["f1"] == ["o2"]

    def test_idempotent_upsert_by_respondent_key(self, responses_table):
        from lambdas.common.responses_dynamo import submit_answers, get_responses_for_poll

        submit_answers(_qa_poll(), "sam@x.com", "Sam", {"f1": ["o1"]})
        submit_answers(_qa_poll(), "sam@x.com", "Sam", {"f1": ["o2"]})

        stored = get_responses_for_poll("poll-qa")
        assert len(stored) == 1
        assert stored[0]["answers"]["f1"] == ["o2"]


class TestAvailabilityFieldId:
    def test_sentinel_for_legacy_poll(self):
        from lambdas.common.responses_dynamo import availability_field_id

        assert availability_field_id(_legacy_scheduler_poll()) == "__availability__"

    def test_uses_declared_availability_field_when_present(self):
        from lambdas.common.responses_dynamo import availability_field_id

        poll = _qa_poll(fields=[{"fieldId": "avail1", "type": "availability", "label": "When?"}])
        assert availability_field_id(poll) == "avail1"
