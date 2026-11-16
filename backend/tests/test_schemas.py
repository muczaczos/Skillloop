"""Unit tests of the profile and career goal models and schemas (no database)."""

import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, get_args
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.models.career_goal import CareerGoalRecord, GoalStatus, Seniority
from app.models.profile import Language, ProfileRecord
from app.schemas.career_goal import CareerGoalCreate, CareerGoalResponse, CareerGoalUpdate
from app.schemas.common import (
    DISPLAY_NAME_MAX_LENGTH,
    GOAL_DESCRIPTION_MAX_LENGTH,
    TARGET_ROLE_MAX_LENGTH,
)
from app.schemas.profile import ProfileResponse, ProfileUpdate

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
GOAL_ID = UUID("22222222-2222-2222-2222-222222222222")
T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def errors_of(exc_info: pytest.ExceptionInfo[ValidationError]) -> list[tuple[tuple[Any, ...], str]]:
    return [(tuple(e["loc"]), e["msg"]) for e in exc_info.value.errors()]


# --- CareerGoalCreate ------------------------------------------------------------------------


def test_create_accepts_a_minimal_and_a_full_goal() -> None:
    minimal = CareerGoalCreate(target_role="DevOps Engineer", seniority="junior")
    full = CareerGoalCreate.model_validate(
        {"target_role": "DevOps Engineer", "seniority": "senior", "description": "Prepare."}
    )

    assert (minimal.description, minimal.seniority) == (None, "junior")
    assert (full.description, full.seniority) == ("Prepare.", "senior")


def test_create_strips_whitespace_and_turns_blank_description_into_none() -> None:
    goal = CareerGoalCreate(target_role="  DevOps  ", seniority="mid", description="  \n ")

    assert goal.target_role == "DevOps"
    assert goal.description is None


@pytest.mark.parametrize("length", [1, TARGET_ROLE_MAX_LENGTH])
def test_target_role_boundaries_are_accepted(length: int) -> None:
    assert CareerGoalCreate(target_role="x" * length, seniority="mid").target_role == "x" * length


@pytest.mark.parametrize("value", ["", "   ", "\t\n", "x" * (TARGET_ROLE_MAX_LENGTH + 1)])
def test_target_role_outside_the_limits_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CareerGoalCreate(target_role=value, seniority="mid")

    assert errors_of(exc_info)[0][0] == ("target_role",)


def test_lengths_count_characters_like_postgresql_not_bytes() -> None:
    # 100 emoji = 400 bytes in UTF-8 but 100 characters; PostgreSQL's char_length agrees.
    assert CareerGoalCreate(target_role="😀" * 100, seniority="mid")
    with pytest.raises(ValidationError):
        CareerGoalCreate(target_role="😀" * 101, seniority="mid")
    assert CareerGoalCreate(target_role="x", seniority="mid", description="ł" * 2000)


@pytest.mark.parametrize("length", [0, GOAL_DESCRIPTION_MAX_LENGTH])
def test_description_boundaries_are_accepted(length: int) -> None:
    goal = CareerGoalCreate(target_role="x", seniority="mid", description="y" * length)

    assert goal.description == ("y" * length or None)


def test_description_over_the_limit_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        CareerGoalCreate(
            target_role="x", seniority="mid", description="y" * (GOAL_DESCRIPTION_MAX_LENGTH + 1)
        )

    assert errors_of(exc_info)[0][0] == ("description",)


@pytest.mark.parametrize("seniority", ["expert", "Junior", "MID", "", None, 1])
def test_unsupported_seniority_is_rejected(seniority: object) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CareerGoalCreate.model_validate({"target_role": "x", "seniority": seniority})

    assert errors_of(exc_info)[0][0] == ("seniority",)


@pytest.mark.parametrize("field", ["target_role", "description"])
def test_nul_characters_are_rejected_before_they_reach_the_database(field: str) -> None:
    payload = {"target_role": "x", "seniority": "mid", field: "a\x00b"}

    with pytest.raises(ValidationError) as exc_info:
        CareerGoalCreate.model_validate(payload)

    assert errors_of(exc_info)[0][0] == (field,)


@pytest.mark.parametrize(
    "extra",
    [
        {"user_id": str(USER_ID)},
        {"id": str(GOAL_ID)},
        {"status": "archived"},
        {"created_at": "2026-01-01T00:00:00Z"},
        {"unknown": 1},
    ],
)
def test_unknown_and_server_controlled_fields_are_rejected_not_ignored(
    extra: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CareerGoalCreate.model_validate({"target_role": "x", "seniority": "mid", **extra})

    assert errors_of(exc_info)[0][1] == "Extra inputs are not permitted"


@pytest.mark.parametrize("payload", [{}, {"target_role": "x"}, {"seniority": "mid"}])
def test_required_fields_are_required(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CareerGoalCreate.model_validate(payload)


@pytest.mark.parametrize("value", [123, True, ["x"], {"a": 1}])
def test_text_fields_do_not_coerce_other_types(value: object) -> None:
    with pytest.raises(ValidationError):
        CareerGoalCreate.model_validate({"target_role": value, "seniority": "mid"})


# --- CareerGoalUpdate ----------------------------------------------------------------------------


def test_update_changes_contains_only_the_fields_that_were_sent() -> None:
    update = CareerGoalUpdate.model_validate({"description": "New", "target_role": " SRE "})

    assert update.changes() == {"target_role": "SRE", "description": "New"}  # declaration order


def test_update_null_or_blank_description_clears_it() -> None:
    assert CareerGoalUpdate.model_validate({"description": None}).changes() == {"description": None}
    assert CareerGoalUpdate.model_validate({"description": "  "}).changes() == {"description": None}


def test_update_distinguishes_a_missing_field_from_null() -> None:
    assert "description" not in CareerGoalUpdate.model_validate({"seniority": "mid"}).changes()


@pytest.mark.parametrize("field", ["target_role", "seniority"])
def test_update_null_is_rejected_for_required_fields(field: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CareerGoalUpdate.model_validate({field: None})

    assert errors_of(exc_info) == [((field,), "Value error, must not be null")]


def test_update_without_fields_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        CareerGoalUpdate.model_validate({})

    assert "at least one field" in errors_of(exc_info)[0][1]


@pytest.mark.parametrize("extra", [{"status": "active"}, {"user_id": str(USER_ID)}, {"id": "x"}])
def test_update_cannot_change_status_owner_or_id(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CareerGoalUpdate.model_validate({"description": "x", **extra})

    assert errors_of(exc_info)[0][1] == "Extra inputs are not permitted"


def test_update_applies_the_same_value_rules_as_create() -> None:
    for bad in (
        {"target_role": "x" * (TARGET_ROLE_MAX_LENGTH + 1)},
        {"target_role": "  "},
        {"seniority": "expert"},
        {"description": "x" * (GOAL_DESCRIPTION_MAX_LENGTH + 1)},
        {"description": "a\x00"},
    ):
        with pytest.raises(ValidationError):
            CareerGoalUpdate.model_validate(bad)


# --- ProfileUpdate ---------------------------------------------------------------------------------


def test_profile_update_changes_and_null_semantics() -> None:
    assert ProfileUpdate.model_validate({"display_name": " Ann "}).changes() == {
        "display_name": "Ann"
    }
    assert ProfileUpdate.model_validate({"display_name": None}).changes() == {"display_name": None}
    assert ProfileUpdate.model_validate({"preferred_language": "pl"}).changes() == {
        "preferred_language": "pl"
    }


def test_profile_update_rejects_null_language_and_an_empty_body() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ProfileUpdate.model_validate({"preferred_language": None})
    assert errors_of(exc_info) == [(("preferred_language",), "Value error, must not be null")]

    with pytest.raises(ValidationError):
        ProfileUpdate.model_validate({})


@pytest.mark.parametrize("value", ["", "   ", "x" * (DISPLAY_NAME_MAX_LENGTH + 1), "a\x00b"])
def test_profile_update_rejects_invalid_display_names(value: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ProfileUpdate.model_validate({"display_name": value})

    assert errors_of(exc_info)[0][0] == ("display_name",)


def test_profile_update_display_name_boundary_is_accepted() -> None:
    name = "x" * DISPLAY_NAME_MAX_LENGTH

    assert ProfileUpdate.model_validate({"display_name": name}).changes() == {"display_name": name}


@pytest.mark.parametrize("language", ["de", "EN", "", "english"])
def test_profile_update_rejects_unsupported_languages(language: str) -> None:
    with pytest.raises(ValidationError):
        ProfileUpdate.model_validate({"preferred_language": language})


@pytest.mark.parametrize("extra", [{"id": "x"}, {"user_id": str(USER_ID)}, {"created_at": "x"}])
def test_profile_update_rejects_unknown_fields(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ProfileUpdate.model_validate({"display_name": "Ann", **extra})

    assert errors_of(exc_info)[0][1] == "Extra inputs are not permitted"


# --- error details never echo the input ------------------------------------------------------------


def test_validation_messages_do_not_contain_the_submitted_value() -> None:
    canary = "CANARY-" + "x" * 200
    attempts: list[Any] = [
        lambda: CareerGoalCreate.model_validate({"target_role": canary, "seniority": "mid"}),
        lambda: CareerGoalCreate.model_validate({"target_role": "x", "seniority": canary}),
        lambda: CareerGoalUpdate.model_validate({"description": canary * 20}),
        lambda: ProfileUpdate.model_validate({"preferred_language": canary}),
        lambda: ProfileUpdate.model_validate({"display_name": canary + "\x00"}),
    ]

    for attempt in attempts:
        with pytest.raises(ValidationError) as exc_info:
            attempt()
        for _, message in errors_of(exc_info):
            assert "CANARY" not in message


# --- records and responses ------------------------------------------------------------------------------


def make_goal_record(**overrides: Any) -> CareerGoalRecord:
    values: dict[str, Any] = {
        "id": GOAL_ID,
        "user_id": USER_ID,
        "target_role": "DevOps Engineer",
        "seniority": "junior",
        "description": None,
        "status": "active",
        "created_at": T0,
        "updated_at": T0,
    }
    values.update(overrides)
    return CareerGoalRecord(**values)


def test_records_are_immutable() -> None:
    record = make_goal_record()

    with pytest.raises(ValidationError):
        record.status = "archived"  # type: ignore[misc]


def test_record_rejects_values_the_database_would_never_return() -> None:
    with pytest.raises(ValidationError):
        make_goal_record(seniority="expert")
    with pytest.raises(ValidationError):
        make_goal_record(status="deleted")


def test_goal_response_omits_the_owner_and_exposes_the_full_resource() -> None:
    response = CareerGoalResponse.model_validate(make_goal_record(description="D"))

    assert response.model_dump(mode="json") == {
        "id": str(GOAL_ID),
        "target_role": "DevOps Engineer",
        "seniority": "junior",
        "description": "D",
        "status": "active",
        "created_at": "2026-11-16T12:00:00Z",
        "updated_at": "2026-11-16T12:00:00Z",
    }


def test_profile_response_omits_the_owner_and_exposes_the_full_resource() -> None:
    record = ProfileRecord(
        id=USER_ID,
        display_name="Ann",
        preferred_language="pl",
        created_at=T0,
        updated_at=T0,
    )

    assert ProfileResponse.model_validate(record).model_dump(mode="json") == {
        "id": str(USER_ID),
        "display_name": "Ann",
        "preferred_language": "pl",
        "created_at": "2026-11-16T12:00:00Z",
        "updated_at": "2026-11-16T12:00:00Z",
    }


def test_timestamps_are_always_rendered_in_utc() -> None:
    warsaw = timezone(timedelta(hours=1))
    record = make_goal_record(created_at=datetime(2026, 11, 16, 13, 0, tzinfo=warsaw))

    assert CareerGoalResponse.model_validate(record).model_dump(mode="json")["created_at"] == (
        "2026-11-16T12:00:00Z"
    )


def test_naive_timestamps_are_rejected() -> None:
    record = make_goal_record()
    naive = record.model_copy(update={"created_at": datetime(2026, 11, 16, 12, 0)})

    with pytest.raises(ValidationError):
        CareerGoalResponse.model_validate(naive)


# --- agreement with the database migrations (skipped when the repository root is absent) ------


MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


def _migration(name: str) -> str:
    matches = sorted(MIGRATIONS.glob(f"*_{name}.sql"))
    if not matches:
        pytest.skip("supabase/migrations is not available (e.g. inside the backend container)")
    return matches[0].read_text()


def _normalized(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.lower())


def test_limits_and_allowed_values_match_the_migrations() -> None:
    goals = _normalized(_migration("create_career_goals"))
    profiles = _normalized(_migration("create_profiles"))

    assert f"char_length(btrim(target_role)) between 1 and {TARGET_ROLE_MAX_LENGTH}" in goals
    assert f"char_length(description) <= {GOAL_DESCRIPTION_MAX_LENGTH}" in goals
    assert f"char_length(display_name) between 1 and {DISPLAY_NAME_MAX_LENGTH}" in profiles
    for literal, column_sql in (
        (get_args(Seniority), "seniority in ({})"),
        (get_args(GoalStatus), "status in ({})"),
    ):
        assert column_sql.format(", ".join(f"'{v}'" for v in literal)) in goals
    assert "preferred_language in ({})".format(", ".join(f"'{v}'" for v in get_args(Language))) in (
        profiles
    )
