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
    DEFAULT_GRANULARITY_MINUTES,
    EVENT_DURATION_STEP_MINUTES,
    MAX_DATE_RANGE_DAYS,
    MAX_EVENT_DURATION_MINUTES,
    MAX_GRID_BLOCKS,
    MIN_EVENT_DURATION_MINUTES,
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
    # ---- Start-range window (duration + start-range model) ----------------
    # earliestStartMinute / latestStartMinute are the allowed range of EVENT
    # START times (minutes since local midnight). This is the first-class shape
    # the current frontend sends. From them + eventDurationMinutes we DERIVE and
    # persist the paint-grid window:
    #     dayStartMinute   = earliestStartMinute
    #     dayEndMinute     = latestStartMinute + eventDurationMinutes
    #     granularityMinutes = the creator's chosen START INTERVAL (15/30/60),
    #                          defaulting to 15 when absent for back-compat with
    #                          clients that predate the control.
    # dayEndMinute may exceed 1440 -- that is the OVERNIGHT case, where the grid
    # rolls into the next calendar day (see timezone.generate_grid).
    earliestStartMinute: int | None = Field(default=None, ge=0, le=1439)
    latestStartMinute: int | None = Field(default=None, ge=0, le=1439)
    # dayStart/dayEnd/granularity remain accepted for the LEGACY create shape
    # (and are what every poll persists + what generate_grid reads). dayEndMinute
    # is allowed past 1440 to hold a DERIVED overnight window end.
    dayStartMinute: int | None = Field(default=None, ge=0, le=1439)
    dayEndMinute: int | None = Field(default=None, ge=1, le=2880)
    granularityMinutes: int | None = None
    timezone: str | None = None
    guestAllowed: bool = False
    showResultsToRespondents: bool = False
    closeAt: datetime | None = None
    # Event length ("duration"), first-class: 15-minute steps from 15 to 360.
    # None means a single-slot event -- the handler defaults it to
    # granularityMinutes so legacy polls behave identically. Drives the results
    # "best contiguous start window" AND (windowed shape) the derived grid end.
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

    @field_validator("eventDurationMinutes")
    @classmethod
    def duration_is_allowed(cls, v: int | None) -> int | None:
        # First-class event length: 15-minute steps, MIN..MAX (15..360). ge=1 on
        # the field already rejects 0/negative; this layers on the step + cap.
        if v is None:
            return v
        if v % EVENT_DURATION_STEP_MINUTES != 0:
            raise ValueError(
                f"eventDurationMinutes must be a multiple of {EVENT_DURATION_STEP_MINUTES}"
            )
        if v < MIN_EVENT_DURATION_MINUTES or v > MAX_EVENT_DURATION_MINUTES:
            raise ValueError(
                f"eventDurationMinutes must be between {MIN_EVENT_DURATION_MINUTES} "
                f"and {MAX_EVENT_DURATION_MINUTES}"
            )
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
        # Two accepted create shapes:
        #   - WINDOWED (current frontend): earliestStartMinute + latestStartMinute
        #     + eventDurationMinutes. The grid window is DERIVED; granularity is
        #     the creator's start interval (default 15). Supports overnight
        #     (latestStart + duration > 1440).
        #   - LEGACY (pre-redesign clients): dayStartMinute + dayEndMinute +
        #     granularityMinutes supplied directly. Unchanged rules.
        has_earliest = self.earliestStartMinute is not None
        has_latest = self.latestStartMinute is not None
        if has_earliest or has_latest:
            self._derive_window_from_start_range(has_earliest, has_latest)
        else:
            self._require_legacy_grid_fields()

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
                f"or event length."
            )

        return self

    def _derive_window_from_start_range(self, has_earliest: bool, has_latest: bool) -> None:
        """
        Windowed shape: validate the start range + duration and DERIVE the
        persisted grid window (dayStart/dayEnd/granularity). dayEnd may exceed
        1440 -- that is the overnight case, resolved by generate_grid rolling
        blocks into the next calendar day.
        """
        if not (has_earliest and has_latest):
            raise ValueError(
                "scheduler poll requires both earliestStartMinute and latestStartMinute"
            )
        required = {
            "startDate": self.startDate,
            "endDate": self.endDate,
            "eventDurationMinutes": self.eventDurationMinutes,
            "timezone": self.timezone,
        }
        missing = [k for k, v in required.items() if v is None]
        if missing:
            raise ValueError(f"scheduler poll missing required fields: {', '.join(missing)}")

        if self.latestStartMinute < self.earliestStartMinute:
            raise ValueError("latestStartMinute must be on or after earliestStartMinute")

        # The creator's chosen start interval IS the grid resolution. Absent =>
        # 15, so clients predating the control keep their exact prior behavior.
        # Membership in ALLOWED_GRANULARITY_MINUTES is enforced by the
        # granularity_is_allowed field validator before we get here.
        if self.granularityMinutes is None:
            self.granularityMinutes = DEFAULT_GRANULARITY_MINUTES

        # Every boundary must land ON the grid. blocks_per_day floor-divides by
        # granularity, so a misaligned start range or duration would silently
        # truncate the final slot off the grid -- responders would never be able
        # to paint the last event window the creator thinks they configured.
        misaligned = [
            name
            for name, value in (
                ("earliestStartMinute", self.earliestStartMinute),
                ("latestStartMinute", self.latestStartMinute),
                ("eventDurationMinutes", self.eventDurationMinutes),
            )
            if value % self.granularityMinutes != 0
        ]
        if misaligned:
            raise ValueError(
                f"{', '.join(misaligned)} must be a multiple of the "
                f"granularityMinutes start interval ({self.granularityMinutes})"
            )

        # Derive + persist the paint-grid window.
        self.dayStartMinute = self.earliestStartMinute
        self.dayEndMinute = self.latestStartMinute + self.eventDurationMinutes

    def _require_legacy_grid_fields(self) -> None:
        """Legacy shape: the full grid config must be supplied directly."""
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
    # Start-range window (duration + start-range model). Persisted alongside the
    # derived grid window so the frontend can round-trip the creator's inputs.
    earliestStartMinute: int | None = None
    latestStartMinute: int | None = None
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
