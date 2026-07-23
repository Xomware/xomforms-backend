"""
Tests for the additive Q&A form field model layer in lambdas/common/models.py.

Phase 1 of docs/features/xomforms-form-builder/PLAN.md: a poll gains an
optional `formType` + typed `fields` array (single_choice / multi_choice /
dropdown / scale), and a `SubmitAnswersRequest` carries a per-field `answers`
map. The scheduler path (formType absent => "scheduler") is unchanged and is
guarded by test_scheduler_golden.py.

Written RED-first.
"""

import pytest
from pydantic import ValidationError as PydanticValidationError


def _scheduler_payload(**overrides):
    base = {
        "title": "Fantasy Draft",
        "startDate": "2026-08-03",
        "endDate": "2026-08-05",
        "dayStartMinute": 8 * 60,
        "dayEndMinute": 14 * 60,
        "granularityMinutes": 30,
        "timezone": "America/New_York",
    }
    base.update(overrides)
    return base


def _qa_payload(**overrides):
    base = {
        "title": "Team RSVP",
        "formType": "qa",
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
                "fieldId": "f4",
                "type": "scale",
                "label": "Excitement",
                "min": 1,
                "max": 5,
                "minLabel": "Meh",
                "maxLabel": "Hyped",
            },
        ],
    }
    base.update(overrides)
    return base


class TestFormTypeDefault:
    def test_absent_form_type_defaults_to_scheduler(self):
        from lambdas.common.models import CreatePollRequest

        model = CreatePollRequest(**_scheduler_payload())
        assert model.formType == "scheduler"
        assert model.fields is None

    def test_scheduler_payload_still_validates_scheduler_scalars(self):
        """A scheduler form (default type) must still validate the grid config
        exactly as before -- end-before-start is still rejected."""
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(**_scheduler_payload(startDate="2026-08-10", endDate="2026-08-01"))


class TestQaFormValidation:
    def test_accepts_valid_qa_form(self):
        from lambdas.common.models import CreatePollRequest

        model = CreatePollRequest(**_qa_payload())
        assert model.formType == "qa"
        assert model.fields is not None
        assert len(model.fields) == 2
        assert model.fields[0].type == "single_choice"
        assert model.fields[1].type == "scale"

    def test_qa_form_does_not_require_scheduler_scalars(self):
        """A Q&A form has no date range / grid -- those scalars are optional."""
        from lambdas.common.models import CreatePollRequest

        model = CreatePollRequest(**_qa_payload())
        assert model.startDate is None
        assert model.granularityMinutes is None

    def test_qa_form_requires_at_least_one_field(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(**_qa_payload(fields=[]))

    def test_choice_field_requires_at_least_two_options(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(
                **_qa_payload(
                    fields=[
                        {
                            "fieldId": "f1",
                            "type": "single_choice",
                            "label": "Pick one",
                            "options": [{"optionId": "o1", "label": "Only"}],
                        }
                    ]
                )
            )

    def test_choice_field_rejects_duplicate_option_ids(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(
                **_qa_payload(
                    fields=[
                        {
                            "fieldId": "f1",
                            "type": "multi_choice",
                            "label": "Pick",
                            "options": [
                                {"optionId": "dup", "label": "A"},
                                {"optionId": "dup", "label": "B"},
                            ],
                        }
                    ]
                )
            )

    def test_scale_rejects_min_ge_max(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(
                **_qa_payload(
                    fields=[{"fieldId": "s", "type": "scale", "label": "x", "min": 5, "max": 5}]
                )
            )

    def test_qa_form_rejects_duplicate_field_ids(self):
        from lambdas.common.models import CreatePollRequest

        dup = {
            "fieldId": "same",
            "type": "dropdown",
            "label": "Team",
            "options": [{"optionId": "o1", "label": "A"}, {"optionId": "o2", "label": "B"}],
        }
        with pytest.raises(PydanticValidationError):
            CreatePollRequest(**_qa_payload(fields=[dup, dict(dup)]))

    def test_unknown_field_type_is_rejected(self):
        from lambdas.common.models import CreatePollRequest

        with pytest.raises(PydanticValidationError):
            CreatePollRequest(
                **_qa_payload(fields=[{"fieldId": "f", "type": "file_upload", "label": "x"}])
            )


class TestSubmitAnswersRequest:
    def test_accepts_answers_map(self):
        from lambdas.common.models import SubmitAnswersRequest

        model = SubmitAnswersRequest(
            displayName="Sam",
            answers={"f1": ["o1"], "f4": 5},
        )
        assert model.displayName == "Sam"
        assert model.answers["f1"] == ["o1"]
        assert model.answers["f4"] == 5

    def test_display_name_required_non_blank(self):
        from lambdas.common.models import SubmitAnswersRequest

        with pytest.raises(PydanticValidationError):
            SubmitAnswersRequest(displayName="  ", answers={})

    def test_answers_defaults_to_empty_map(self):
        from lambdas.common.models import SubmitAnswersRequest

        model = SubmitAnswersRequest(displayName="Sam")
        assert model.answers == {}
