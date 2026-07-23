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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

from lambdas.common.constants import (
    ALLOWED_GRANULARITY_MINUTES,
    MAX_DATE_RANGE_DAYS,
    MAX_GRID_BLOCKS,
)


def _is_valid_timezone(tz_name: str) -> bool:
    try:
        ZoneInfo(tz_name)
        return True
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return False


class CreatePollRequest(BaseModel):
    """Creator-supplied poll config. Maps to lambdas/polls_create."""

    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    startDate: date
    endDate: date
    dayStartMinute: int = Field(ge=0, le=1439)
    dayEndMinute: int = Field(ge=1, le=1440)
    granularityMinutes: int
    timezone: str
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
    def granularity_is_allowed(cls, v: int) -> int:
        if v not in ALLOWED_GRANULARITY_MINUTES:
            raise ValueError(f"granularityMinutes must be one of {ALLOWED_GRANULARITY_MINUTES}")
        return v

    @field_validator("timezone")
    @classmethod
    def timezone_is_known(cls, v: str) -> str:
        if not _is_valid_timezone(v):
            raise ValueError(f"Unknown IANA timezone '{v}'")
        return v

    @model_validator(mode="after")
    def validate_ranges_and_grid_size(self) -> "CreatePollRequest":
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


class PollResponse(BaseModel):
    """What polls_create / polls_get return to the client."""

    pollId: str
    creatorEmail: str
    title: str
    description: str | None = None
    startDate: date
    endDate: date
    dayStartMinute: int
    dayEndMinute: int
    granularityMinutes: int
    timezone: str
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
