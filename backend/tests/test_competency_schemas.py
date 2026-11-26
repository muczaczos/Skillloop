"""Unit tests of the competency record, schemas and domain errors (no database)."""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any, get_args
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.core.errors import AppError, CompetencyLimitReachedError, CompetencyNameExistsError
from app.models.competency import (
    CompetencyRecord,
    CompetencyStatus,
    ExpectedLevel,
    Importance,
    KnowledgeType,
)
from app.schemas.common import (
    COMPETENCY_CATEGORY_MAX_LENGTH,
    COMPETENCY_NAME_MAX_LENGTH,
    COMPETENCY_TEXT_MAX_LENGTH,
    DISPLAY_ORDER_MAX,
)
from app.schemas.competency import (
    CompetencyCreate,
    CompetencyListResponse,
    CompetencyResponse,
    CompetencyUpdate,
)
from tests.test_schemas import _migration, _normalized, errors_of

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
GOAL_ID = UUID("22222222-2222-2222-2222-222222222222")
COMPETENCY_ID = UUID("33333333-3333-3333-3333-333333333333")
T0 = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)

REQUIRED = {
    "name": "Docker",
    "knowledge_type": "application",
    "importance": "high",
    "expected_level": "intermediate",
}
TEXT_FIELDS = ["description", "rationale", "category"]


def payload(**overrides: Any) -> dict[str, Any]:
    return {**REQUIRED, **overrides}


def first_error_location(data: dict[str, Any]) -> tuple[Any, ...]:
    with pytest.raises(ValidationError) as exc_info:
        CompetencyCreate.model_validate(data)
    return errors_of(exc_info)[0][0]


# --- CompetencyCreate: accepted input ----------------------------------------------------------


def test_create_accepts_a_minimal_competency_and_applies_the_defaults() -> None:
    competency = CompetencyCreate.model_validate(REQUIRED)

    assert competency.name == "Docker"
    assert (competency.description, competency.rationale, competency.category) == (None,) * 3
    assert competency.display_order == 0


def test_create_accepts_a_full_competency() -> None:
    competency = CompetencyCreate.model_validate(
        payload(
            description="Containers.",
            rationale="Used everywhere.",
            category="Containers",
            display_order=4,
        )
    )

    assert competency.model_dump() == {
        **REQUIRED,
        "description": "Containers.",
        "rationale": "Used everywhere.",
        "category": "Containers",
        "display_order": 4,
    }


def test_create_strips_surrounding_whitespace() -> None:
    competency = CompetencyCreate.model_validate(
        payload(name="  Docker \n", description=" D ", rationale="\tR\t", category=" C ")
    )

    assert (competency.name, competency.description) == ("Docker", "D")
    assert (competency.rationale, competency.category) == ("R", "C")


@pytest.mark.parametrize("field", TEXT_FIELDS)
@pytest.mark.parametrize("blank", ["", "   ", "\t\n ", None])
def test_blank_optional_texts_become_none(field: str, blank: str | None) -> None:
    assert getattr(CompetencyCreate.model_validate(payload(**{field: blank})), field) is None


def test_the_enum_values_of_the_database_are_accepted() -> None:
    for knowledge_type in get_args(KnowledgeType):
        for importance in get_args(Importance):
            for level in get_args(ExpectedLevel):
                data = payload(
                    knowledge_type=knowledge_type, importance=importance, expected_level=level
                )
                assert CompetencyCreate.model_validate(data).model_dump()["importance"] == (
                    importance
                )


# --- CompetencyCreate: lengths -----------------------------------------------------------------


@pytest.mark.parametrize("length", [1, COMPETENCY_NAME_MAX_LENGTH])
def test_name_boundaries_are_accepted(length: int) -> None:
    assert CompetencyCreate.model_validate(payload(name="x" * length)).name == "x" * length


@pytest.mark.parametrize("name", ["", " ", "\t\n", "x" * (COMPETENCY_NAME_MAX_LENGTH + 1)])
def test_name_outside_the_limits_is_rejected(name: str) -> None:
    assert first_error_location(payload(name=name)) == ("name",)


def test_name_longer_than_the_limit_only_because_of_whitespace_is_accepted() -> None:
    name = "x" * COMPETENCY_NAME_MAX_LENGTH

    assert CompetencyCreate.model_validate(payload(name=f"  {name}  ")).name == name


def test_lengths_count_characters_like_postgresql_not_bytes() -> None:
    assert CompetencyCreate.model_validate(payload(name="😀" * 100))
    assert first_error_location(payload(name="😀" * 101)) == ("name",)


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("description", COMPETENCY_TEXT_MAX_LENGTH),
        ("rationale", COMPETENCY_TEXT_MAX_LENGTH),
        ("category", COMPETENCY_CATEGORY_MAX_LENGTH),
    ],
)
def test_optional_text_boundaries(field: str, limit: int) -> None:
    accepted = CompetencyCreate.model_validate(payload(**{field: "y" * limit}))

    assert getattr(accepted, field) == "y" * limit
    assert first_error_location(payload(**{field: "y" * (limit + 1)})) == (field,)


# --- CompetencyCreate: required fields, enums, display_order -----------------------------------


@pytest.mark.parametrize("field", sorted(REQUIRED))
def test_missing_required_fields_are_rejected(field: str) -> None:
    data = {k: v for k, v in REQUIRED.items() if k != field}

    assert first_error_location(data) == (field,)


@pytest.mark.parametrize("field", sorted(REQUIRED))
def test_null_in_a_required_field_is_rejected(field: str) -> None:
    assert first_error_location(payload(**{field: None})) == (field,)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("knowledge_type", "theory"),
        ("knowledge_type", "Memory"),
        ("importance", "urgent"),
        ("importance", "HIGH"),
        ("expected_level", "expert"),
        ("expected_level", "not_assessed"),
        ("expected_level", ""),
    ],
)
def test_unknown_enum_values_are_rejected(field: str, value: str) -> None:
    assert first_error_location(payload(**{field: value})) == (field,)


@pytest.mark.parametrize("value", [0, 1, 4, DISPLAY_ORDER_MAX])
def test_display_order_in_range_is_accepted(value: int) -> None:
    assert CompetencyCreate.model_validate(payload(display_order=value)).display_order == value


@pytest.mark.parametrize("value", [-1, DISPLAY_ORDER_MAX + 1, 2**40])
def test_display_order_out_of_range_is_rejected(value: int) -> None:
    assert first_error_location(payload(display_order=value)) == ("display_order",)


@pytest.mark.parametrize("value", ["5", 5.0, 5.5, True, False, None, [1]])
def test_display_order_must_be_a_real_integer(value: Any) -> None:
    assert first_error_location(payload(display_order=value)) == ("display_order",)


# --- CompetencyCreate: fields a client may not send --------------------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        {"id": str(COMPETENCY_ID)},
        {"career_goal_id": str(GOAL_ID)},
        {"user_id": str(USER_ID)},
        {"status": "archived"},
        {"created_at": "2026-11-24T12:00:00Z"},
        {"updated_at": "2026-11-24T12:00:00Z"},
        {"current_level": "advanced"},
        {"source": "ai_generated"},
        {"unknown": 1},
    ],
)
def test_fields_that_a_client_may_not_send_are_rejected_not_ignored(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CompetencyCreate.model_validate(payload(**extra))

    assert errors_of(exc_info) == [((next(iter(extra)),), "Extra inputs are not permitted")]


@pytest.mark.parametrize("field", ["name", *TEXT_FIELDS])
def test_nul_characters_are_rejected_before_they_reach_the_database(field: str) -> None:
    assert first_error_location(payload(**{field: "a\x00b"})) == (field,)


def test_a_body_that_is_not_an_object_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CompetencyCreate.model_validate(["Docker"])


# --- CompetencyRecord and CompetencyResponse ---------------------------------------------------


def make_record(**overrides: Any) -> CompetencyRecord:
    values: dict[str, Any] = {
        "id": COMPETENCY_ID,
        "career_goal_id": GOAL_ID,
        "user_id": USER_ID,
        "name": "Docker",
        "description": None,
        "rationale": None,
        "category": None,
        "knowledge_type": "application",
        "importance": "high",
        "expected_level": "intermediate",
        "display_order": 0,
        "status": "active",
        "created_at": T0,
        "updated_at": T0,
    }
    values.update(overrides)
    return CompetencyRecord(**values)


def test_records_are_immutable() -> None:
    with pytest.raises(ValidationError):
        make_record().status = "archived"  # type: ignore[misc]


@pytest.mark.parametrize(
    "bad",
    [
        {"status": "deleted"},
        {"knowledge_type": "theory"},
        {"importance": "urgent"},
        {"expected_level": "not_assessed"},
    ],
)
def test_record_rejects_values_the_database_would_never_return(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_record(**bad)


def test_response_omits_the_owner_and_exposes_the_full_resource() -> None:
    response = CompetencyResponse.model_validate(
        make_record(description="D", rationale="R", category="C", display_order=3)
    )

    assert response.model_dump(mode="json") == {
        "id": str(COMPETENCY_ID),
        "career_goal_id": str(GOAL_ID),
        "name": "Docker",
        "description": "D",
        "rationale": "R",
        "category": "C",
        "knowledge_type": "application",
        "importance": "high",
        "expected_level": "intermediate",
        "display_order": 3,
        "status": "active",
        "created_at": "2026-11-24T12:00:00Z",
        "updated_at": "2026-11-24T12:00:00Z",
    }
    assert "user_id" not in CompetencyResponse.model_fields


def test_timestamps_are_always_rendered_in_utc() -> None:
    warsaw = timezone(timedelta(hours=1))
    record = make_record(updated_at=datetime(2026, 11, 24, 13, 0, tzinfo=warsaw))

    assert CompetencyResponse.model_validate(record).model_dump(mode="json")["updated_at"] == (
        "2026-11-24T12:00:00Z"
    )


def test_naive_timestamps_are_rejected() -> None:
    naive = make_record().model_copy(update={"created_at": datetime(2026, 11, 24, 12, 0)})

    with pytest.raises(ValidationError):
        CompetencyResponse.model_validate(naive)


def test_the_list_response_wraps_the_competencies_in_an_object() -> None:
    records = [make_record(name="Docker"), make_record(name="Git", status="archived")]

    body = CompetencyListResponse.model_validate({"competencies": records}).model_dump(mode="json")

    assert list(body) == ["competencies"]
    assert [c["name"] for c in body["competencies"]] == ["Docker", "Git"]
    assert CompetencyListResponse(competencies=[]).model_dump() == {"competencies": []}


def test_the_documented_examples_are_valid() -> None:
    for example in CompetencyCreate.model_json_schema()["examples"]:
        CompetencyCreate.model_validate(example)
    for example in CompetencyResponse.model_json_schema()["examples"]:
        response = CompetencyResponse.model_validate(example)
        assert response.model_dump(mode="json")["created_at"] == example["created_at"]


# --- domain errors -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error_class", "code"),
    [
        (CompetencyLimitReachedError, "COMPETENCY_LIMIT_REACHED"),
        (CompetencyNameExistsError, "COMPETENCY_NAME_EXISTS"),
    ],
)
def test_competency_errors_are_409_domain_errors(error_class: type[AppError], code: str) -> None:
    error = error_class()

    assert isinstance(error, AppError)
    assert (error.status_code, error.code) == (409, code)
    assert error.message == str(error) == error_class.default_message
    assert "50" not in error.message  # the limit lives in the database function only


# --- agreement with the database migration (skipped when the repository root is absent) -------


def test_limits_and_allowed_values_match_the_migration() -> None:
    sql = _normalized(_migration("create_competencies"))

    assert f"char_length(btrim(name)) between 1 and {COMPETENCY_NAME_MAX_LENGTH}" in sql
    assert f"char_length(description) <= {COMPETENCY_TEXT_MAX_LENGTH}" in sql
    assert f"char_length(rationale) <= {COMPETENCY_TEXT_MAX_LENGTH}" in sql
    assert f"char_length(btrim(category)) between 1 and {COMPETENCY_CATEGORY_MAX_LENGTH}" in sql
    assert "display_order integer not null default 0" in sql
    assert "display_order >= 0" in sql
    assert DISPLAY_ORDER_MAX == 2**31 - 1  # the largest value of a PostgreSQL integer
    for literal, column_sql in (
        (get_args(KnowledgeType), "knowledge_type in ({})"),
        (get_args(Importance), "importance in ({})"),
        (get_args(ExpectedLevel), "expected_level in ({})"),
        (get_args(CompetencyStatus), "status in ({})"),
    ):
        assert column_sql.format(", ".join(f"'{v}'" for v in literal)) in sql


def test_the_record_has_exactly_the_columns_of_the_table() -> None:
    sql = _normalized(_migration("create_competencies"))
    table = sql.split("create table public.competencies (", 1)[1].split(" constraint ", 1)[0]
    columns = {part.split()[0] for part in table.split(",") if part.strip()}

    assert columns == set(CompetencyRecord.model_fields)


# --- CompetencyUpdate (PATCH) ------------------------------------------------------------------

NOT_NULLABLE = ["name", "knowledge_type", "importance", "expected_level", "display_order"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "Kubernetes"),
        ("description", "Orchestration."),
        ("rationale", "Common."),
        ("category", "Containers"),
        ("knowledge_type", "memory"),
        ("importance", "low"),
        ("expected_level", "advanced"),
        ("display_order", 0),
    ],
)
def test_update_changes_contain_only_the_field_that_was_sent(field: str, value: Any) -> None:
    assert CompetencyUpdate.model_validate({field: value}).changes() == {field: value}


@pytest.mark.parametrize("field", TEXT_FIELDS)
@pytest.mark.parametrize("value", [None, "", "  \n "], ids=["null", "empty", "blank"])
def test_update_clears_an_optional_text_with_null_or_blank(field: str, value: Any) -> None:
    assert CompetencyUpdate.model_validate({field: value}).changes() == {field: None}


@pytest.mark.parametrize("field", NOT_NULLABLE)
def test_update_rejects_null_for_a_required_column(field: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CompetencyUpdate.model_validate({field: None})

    assert errors_of(exc_info)[0][0] == (field,)


def test_update_requires_at_least_one_field() -> None:
    with pytest.raises(ValidationError, match="at least one field"):
        CompetencyUpdate.model_validate({})


@pytest.mark.parametrize(
    "field",
    ["id", "career_goal_id", "user_id", "status", "created_at", "updated_at", "unknown"],
)
def test_update_rejects_fields_the_client_may_not_send(field: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CompetencyUpdate.model_validate({"name": "Git", field: "x"})

    assert errors_of(exc_info)[0][0] == (field,)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "   "),
        ("name", "x" * (COMPETENCY_NAME_MAX_LENGTH + 1)),
        ("name", "a\x00b"),
        ("description", "d" * (COMPETENCY_TEXT_MAX_LENGTH + 1)),
        ("rationale", "r" * (COMPETENCY_TEXT_MAX_LENGTH + 1)),
        ("category", "c" * (COMPETENCY_CATEGORY_MAX_LENGTH + 1)),
        ("knowledge_type", "magic"),
        ("importance", "urgent"),
        ("expected_level", "not_assessed"),
        ("display_order", -1),
        ("display_order", DISPLAY_ORDER_MAX + 1),
        ("display_order", "5"),
        ("display_order", 5.0),
        ("display_order", True),
    ],
)
def test_update_applies_the_same_value_rules_as_create(field: str, value: Any) -> None:
    with pytest.raises(ValidationError) as exc_info:
        CompetencyUpdate.model_validate({field: value})

    assert errors_of(exc_info)[0][0][0] == field


def test_update_strips_surrounding_whitespace() -> None:
    update = CompetencyUpdate.model_validate({"name": "  Git \n", "category": " VCS "})

    assert update.changes() == {"name": "Git", "category": "VCS"}


def test_update_offers_the_same_fields_as_create() -> None:
    assert list(CompetencyUpdate.model_fields) == list(CompetencyCreate.model_fields)
