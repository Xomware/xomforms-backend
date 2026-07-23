"""
XOMFORMS Pydantic Models
========================
Request/response boundary validation. This is a conscious deviation from
xomify-backend's parse_body/require_fields pattern, sanctioned by
.claude/rules/backend.md ("validate at the boundary: Pydantic") and called
out explicitly in docs/features/xomforms/PLAN.md. lambdas/common/ otherwise
stays structurally identical to xomify-backend for portability.
"""

from datetime import date, datetime
from typing import Annotated, Any, Literal, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

from lambdas.common.constants import (
    ALLOWED_GRANULARITY_MINUTES,
    MAX_DATE_RANGE_DAYS,
    MAX_GRID_BLOCKS,
)

# ---------------------------------------------------------------------------
# Q&A form field definitions (Phase 1 of the form-builder). These are ADDITIVE:
# a poll with no `fields`/`formType` is a legacy scheduler poll and never
# touches any of this. See docs/features/xomforms-form-builder/PLAN.md.
# ---------------------------------------------------------------------------

# A choice field needs alternatives to be meaningful.
MIN_CHOICE_OPTIONS = 2
# Keep scales in a sane, chartable range (1-5, 0-10, etc.).
MAX_SCALE_SPAN = 20


def _is_valid_timezone(tz_name: str) -> bool:
    try:
        ZoneInfo(tz_name)
        return True
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return False


class FieldOption(BaseModel):
    """One selectable option on a choice-type field."""

    optionId: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=500)


class _BaseFormField(BaseModel):
    fieldId: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=500)
    required: bool = False


class _ChoiceFieldMixin(_BaseFormField):
    options: list[FieldOption] = Field(min_length=MIN_CHOICE_OPTIONS, max_length=200)

    @model_validator(mode="after")
    def _unique_option_ids(self):
        ids = [o.optionId for o in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError("option ids must be unique within a field")
        return self


class SingleChoiceField(_ChoiceFieldMixin):
    """Radio-button field: exactly one option selected. Answer = [optionId]."""

    type: Literal["single_choice"]


class MultiChoiceField(_ChoiceFieldMixin):
    """Checkboxes field: zero or more options. Answer = optionId[]."""

    type: Literal["multi_choice"]


class DropdownField(_ChoiceFieldMixin):
    """Dropdown: same data/analytics as single_choice, compact renderer."""

    type: Literal["dropdown"]


class ScaleField(_BaseFormField):
    """Linear scale (1-5, 0-10, ...). Answer = one int in [min, max]."""

    type: Literal["scale"]
    min: int = Field(ge=0, le=1000)
    max: int = Field(ge=1, le=1000)
    minLabel: str | None = Field(default=None, max_length=100)
    maxLabel: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _range_is_sane(self):
        if self.max <= self.min:
            raise ValueError("scale max must be greater than min")
        if (self.max - self.min) > MAX_SCALE_SPAN:
            raise ValueError(f"scale span cannot exceed {MAX_SCALE_SPAN}")
        return self


# Discriminated union over `type`. New field types (Phases 2-3) append here.
FormField = Annotated[
    Union[SingleChoiceField, MultiChoiceField, DropdownField, ScaleField],
    Field(discriminator="type"),
]


class CreatePollRequest(BaseModel):
    """
    Creator-supplied poll config. Maps to lambdas/polls_create.

    Two shapes share this model, discriminated by `formType`:
      - "scheduler" (default, and the only shape before the form-builder):
        the scheduler scalars (startDate..timezone) are REQUIRED and the grid
        is size-validated exactly as before. A legacy client that omits
        `formType` gets this path byte-for-byte.
      - "qa": a Q&A form with a typed `fields` array; the scheduler scalars
        do not apply and are left unset.
    """

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    # Form type + typed fields (additive; absent => scheduler).
    formType: Literal["scheduler", "qa"] = "scheduler"
    fields: list[FormField] | None = Field(default=None, max_length=100)
    # Scheduler scalars -- REQUIRED for a scheduler poll (enforced in the model
    # validator below), unused for a qa poll. Optional at the type level so a
    # qa form need not supply a date range/grid.
    startDate: date | None = None
    endDate: date | None = None
    dayStartMinute: int | None = Field(default=None, ge=0, le=1439)
    dayEndMinute: int | None = Field(default=None, ge=1, le=1440)
    granularityMinutes: int | None = None
    timezone: str | None = None
    guestAllowed: bool = False
    showResultsToRespondents: bool = False
    closeAt: datetime | None = None
    # How long the scheduled event actually runs, in minutes. None means a
    # single-slot event -- the handler defaults it to granularityMinutes so
    # polls created before this field behave identically. Used purely for the
    # results "best contiguous start window" computation; it does NOT change
    # the paint-all-availability response model.
    eventDurationMinutes: int | None = Field(default=None, ge=1)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title cannot be blank")
        return v.strip()

    @field_validator("granularityMinutes")
    @classmethod
    def granularity_is_allowed(cls, v: int | None) -> int | None:
        if v is None:
            return v
        if v not in ALLOWED_GRANULARITY_MINUTES:
            raise ValueError(f"granularityMinutes must be one of {ALLOWED_GRANULARITY_MINUTES}")
        return v

    @field_validator("timezone")
    @classmethod
    def timezone_is_known(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _is_valid_timezone(v):
            raise ValueError(f"Unknown IANA timezone '{v}'")
        return v

    @model_validator(mode="after")
    def validate_form(self) -> "CreatePollRequest":
        if self.formType == "qa":
            return self._validate_qa()
        return self._validate_scheduler()

    def _validate_qa(self) -> "CreatePollRequest":
        if not self.fields:
            raise ValueError("a qa form must declare at least one field")
        ids = [f.fieldId for f in self.fields]
        if len(ids) != len(set(ids)):
            raise ValueError("fieldId must be unique within a form")
        return self

    def _validate_scheduler(self) -> "CreatePollRequest":
        # A scheduler poll must carry the full grid config -- unchanged rules.
        required = {
            "startDate": self.startDate,
            "endDate": self.endDate,
            "dayStartMinute": self.dayStartMinute,
            "dayEndMinute": self.dayEndMinute,
            "granularityMinutes": self.granularityMinutes,
            "timezone": self.timezone,
        }
        missing = [k for k, v in required.items() if v is None]
        if missing:
            raise ValueError(f"scheduler poll missing required fields: {', '.join(missing)}")

        if self.endDate < self.startDate:
            raise ValueError("endDate must be on or after startDate")

        if self.dayEndMinute <= self.dayStartMinute:
            raise ValueError("dayEndMinute must be after dayStartMinute")

        date_range_days = (self.endDate - self.startDate).days + 1
        if date_range_days > MAX_DATE_RANGE_DAYS:
            raise ValueError(f"Date range cannot exceed {MAX_DATE_RANGE_DAYS} days")

        blocks_per_day = (self.dayEndMinute - self.dayStartMinute) // self.granularityMinutes
        total_blocks = date_range_days * blocks_per_day
        if total_blocks > MAX_GRID_BLOCKS:
            raise ValueError(
                f"Grid would contain {total_blocks} blocks, exceeding the cap of "
                f"{MAX_GRID_BLOCKS} (keeps a fully-selected response item well under "
                f"DynamoDB's 400 KB item limit). Narrow the date range, time window, "
                f"or granularity."
            )

        return self


class SubmitAvailabilityRequest(BaseModel):
    """
    Respondent-supplied availability. Shared by both responses_submit_authed
    and responses_submit_public -- the two handlers only differ in identity
    resolution (see submit_availability() in responses_dynamo.py).
    """

    displayName: str = Field(min_length=1, max_length=100)
    blocks: list[str] = Field(default_factory=list, max_length=MAX_GRID_BLOCKS)

    @field_validator("displayName")
    @classmethod
    def display_name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("displayName cannot be blank")
        return v.strip()

    @field_validator("blocks")
    @classmethod
    def dedupe_blocks(cls, v: list[str]) -> list[str]:
        # Preserve determinism (sorted) rather than insertion order, since
        # dict submission order isn't meaningful here.
        return sorted(set(v))


class SubmitAnswersRequest(BaseModel):
    """
    Respondent-supplied answers to a Q&A form (formType == "qa"). The answer
    VALUES are validated per-field against the poll's declared field set in
    responses_dynamo.submit_answers() -- this model only checks the envelope
    (displayName + an answers map), mirroring how SubmitAvailabilityRequest
    checks the blocks envelope while submit_availability() validates the blocks
    against the grid.
    """

    displayName: str = Field(min_length=1, max_length=100)
    answers: dict[str, Any] = Field(default_factory=dict)

    @field_validator("displayName")
    @classmethod
    def display_name_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("displayName cannot be blank")
        return v.strip()


class PollResponse(BaseModel):
    """What polls_create / polls_get return to the client."""

    pollId: str
    creatorEmail: str
    title: str
    description: str | None = None
    # Additive form-builder fields (absent on legacy scheduler polls).
    formType: Literal["scheduler", "qa"] = "scheduler"
    fields: list[FormField] | None = None
    # Scheduler scalars are optional so a qa poll (which has none) still
    # serializes cleanly through this model.
    startDate: date | None = None
    endDate: date | None = None
    dayStartMinute: int | None = None
    dayEndMinute: int | None = None
    granularityMinutes: int | None = None
    timezone: str | None = None
    guestAllowed: bool
    showResultsToRespondents: bool
    closeAt: datetime | None = None
    eventDurationMinutes: int | None = None
    createdAt: datetime


class BlockTally(BaseModel):
    """One block's overlap tally, part of OverlapResult."""

    blockId: str
    utcInstant: str
    count: int
    total: int
    ratio: float


class OverlapResult(BaseModel):
    """What results_get returns -- the overlap heatmap + ranked best time(s)."""

    pollId: str
    totalRespondents: int
    blocks: list[BlockTally]
    bestBlockIds: list[str]
    # Event-length window fields (additive). eventDurationMinutes is the poll's
    # configured event length; slotCount is how many contiguous grid blocks it
    # spans. bestWindowStartIds are the start blockIds of the contiguous
    # same-day window(s) where the most respondents are free for the WHOLE
    # window, and bestWindowCount is that headcount. For a single-slot event
    # these collapse to the per-block best.
    eventDurationMinutes: int
    slotCount: int
    bestWindowStartIds: list[str]
    bestWindowCount: int


# ---------------------------------------------------------------------------
# Q&A analytics result models (the per-field generalization of OverlapResult).
# ---------------------------------------------------------------------------


class OptionTally(BaseModel):
    """One option's tally on a choice/dropdown/availability field."""

    optionId: str
    label: str
    count: int
    total: int
    ratio: float


class ScaleBucket(BaseModel):
    """One integer value's tally on a scale field (a histogram bucket)."""

    value: int
    count: int
    total: int
    ratio: float


class FieldResult(BaseModel):
    """
    Per-field analytics. Choice/dropdown/multi fields populate `options`;
    scale fields populate `buckets` + `mean`/`min`/`max`. Text/list fields
    (Phase 2) will populate neither and rely on `totalResponses` only.
    """

    fieldId: str
    type: str
    label: str
    totalResponses: int
    options: list[OptionTally] = Field(default_factory=list)
    buckets: list[ScaleBucket] = Field(default_factory=list)
    mean: float | None = None
    min: int | None = None
    max: int | None = None
    scaleMin: int | None = None
    scaleMax: int | None = None


class FormResult(BaseModel):
    """What results_get returns for a qa poll -- per-field tallies."""

    pollId: str
    totalRespondents: int
    fields: list[FieldResult]
