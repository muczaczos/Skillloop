"""Building blocks shared by the API schemas.

The limits mirror the CHECK constraints of the database migrations. The database stays the
final barrier; these rules exist so that clients get a precise 422 before any SQL runs, and
so that valid input can never trip a constraint (which would be a bug, not a client error).
Lengths count characters (code points), exactly like PostgreSQL's char_length().
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import AfterValidator, AwareDatetime, BaseModel, BeforeValidator, Field

TARGET_ROLE_MAX_LENGTH = 100  # career_goals_target_role_check
GOAL_DESCRIPTION_MAX_LENGTH = 2000  # career_goals_description_check
DISPLAY_NAME_MAX_LENGTH = 100  # profiles_display_name_check
COMPETENCY_NAME_MAX_LENGTH = 100  # competencies_name_check
COMPETENCY_TEXT_MAX_LENGTH = 1000  # competencies_description_check, competencies_rationale_check
COMPETENCY_CATEGORY_MAX_LENGTH = 60  # competencies_category_check
# competencies.display_order is a PostgreSQL integer (int4); the CHECK only forbids negatives.
DISPLAY_ORDER_MAX = 2_147_483_647


def _clean(value: Any) -> Any:
    """Strip surrounding whitespace and refuse NUL, which PostgreSQL cannot store in text.

    A JSON body may legally contain \\u0000, so without this check it would only fail in
    the database. The message never repeats the input.
    """
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError("must not contain NUL characters")
        return value.strip()
    return value


def _clean_optional(value: Any) -> Any:
    """Like _clean, but text that is empty after stripping means "no value"."""
    cleaned = _clean(value)
    return None if cleaned == "" else cleaned


TargetRole = Annotated[
    str,
    BeforeValidator(_clean),
    Field(min_length=1, max_length=TARGET_ROLE_MAX_LENGTH),
]
DisplayName = Annotated[
    str,
    BeforeValidator(_clean),
    Field(min_length=1, max_length=DISPLAY_NAME_MAX_LENGTH),
]
# The length limit sits on the str branch, so that None (no description) is still accepted.
GoalDescription = Annotated[
    Annotated[str, Field(max_length=GOAL_DESCRIPTION_MAX_LENGTH)] | None,
    BeforeValidator(_clean_optional),
]


CompetencyName = Annotated[
    str,
    BeforeValidator(_clean),
    Field(min_length=1, max_length=COMPETENCY_NAME_MAX_LENGTH),
]
# Optional texts: text that is empty after stripping means "no value" (None), like GoalDescription.
CompetencyText = Annotated[
    Annotated[str, Field(max_length=COMPETENCY_TEXT_MAX_LENGTH)] | None,
    BeforeValidator(_clean_optional),
]
CompetencyCategory = Annotated[
    Annotated[str, Field(max_length=COMPETENCY_CATEGORY_MAX_LENGTH)] | None,
    BeforeValidator(_clean_optional),
]
# strict: only a JSON integer is accepted, not "5", 5.0 or true.
DisplayOrder = Annotated[int, Field(strict=True, ge=0, le=DISPLAY_ORDER_MAX)]


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


# Timestamps are always rendered in UTC, whatever the session time zone of the database is.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]


def reject_null(value: Any) -> Any:
    """For PATCH fields that are NOT NULL in the database: an explicit null is an error."""
    if value is None:
        raise ValueError("must not be null")
    return value


def provided_fields(model: BaseModel) -> Iterator[tuple[str, Any]]:
    """Yield (name, value) of the fields the client actually sent, in declaration order."""
    for name in type(model).model_fields:
        if name in model.model_fields_set:
            yield name, getattr(model, name)
