"""Unit tests of the competency service: no database, no network.

The REAL user_transaction() and run_user_transaction() run on the doubles of db_fakes.py, and the
repositories are mocks that write into the same event list. So the tests can check ORDER: that
the goal is classified only after the failed transaction was rolled back and its connection
released, and in a new transaction.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from app.core.errors import (
    AppError,
    CompetencyArchivedError,
    CompetencyLimitReachedError,
    CompetencyNameExistsError,
    CompetencyNotFoundError,
    GoalArchivedError,
    GoalNotFoundError,
)
from app.db import session
from app.db.errors import DatabaseUnavailableError
from app.models.auth import AuthenticatedUser
from app.models.career_goal import CareerGoalRecord
from app.models.competency import CompetencyRecord
from app.schemas.competency import CompetencyCreate, CompetencyUpdate
from app.services import competency_service
from tests.db_fakes import FakeUserConnection, FakeUserPool
from tests.test_competency_schemas import make_record
from tests.test_db_session_retry import START, deadlock, serialization

USER = AuthenticatedUser(id=UUID("11111111-1111-1111-1111-111111111111"))
GOAL_ID = UUID("22222222-2222-2222-2222-222222222222")
T0 = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)

FULL = CompetencyCreate(
    name="Terraform",
    description="IaC.",
    rationale="Infrastructure is code.",
    category="IaC",
    knowledge_type="application",
    importance="high",
    expected_level="intermediate",
    display_order=4,
)
MINIMAL = CompetencyCreate(
    name="Git", knowledge_type="memory", importance="low", expected_level="beginner"
)


class _Diag:
    def __init__(self, constraint_name: str | None) -> None:
        self.constraint_name = constraint_name


def violation(kind: type[psycopg.Error], constraint: str | None) -> psycopg.Error:
    """A database error carrying a constraint name; its message must never matter."""

    class _Violation(kind):  # type: ignore[misc, valid-type]
        @property
        def diag(self) -> Any:  # psycopg builds diag from a server result; fake it
            return _Diag(constraint)

    return _Violation("a message that no code may parse")


def goal(status: str = "active") -> CareerGoalRecord:
    return CareerGoalRecord(
        id=GOAL_ID,
        user_id=USER.id,
        target_role="Backend Engineer",
        seniority="mid",
        description=None,
        status=status,  # type: ignore[arg-type]
        created_at=T0,
        updated_at=T0,
    )


class Repos:
    """The two repository modules as mocks. Every call is written into the transaction events;
    a function with no script fails the test when it is called."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.competencies = MagicMock()
        self.career_goals = MagicMock()
        for module in (self.competencies, self.career_goals):
            for name in (
                "insert",
                "list_by_goal",
                "get_by_id",
                "update_active",
                "archive_active",
            ):
                getattr(module, name).side_effect = self._unexpected(name)

    def _unexpected(self, name: str) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(f"unexpected call of {name}")

        return call

    def script(self, module: MagicMock, name: str, *results: Any) -> None:
        """The n-th call gives the n-th result (the last one repeats); an exception is raised."""
        queue = list(results)

        def call(*args: Any, **kwargs: Any) -> Any:
            self.events.append(f"repo.{name}")
            result = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(result, BaseException):
                raise result
            return result

        getattr(module, name).side_effect = call


@pytest.fixture
def pool() -> FakeUserPool:
    events: list[str] = []
    return FakeUserPool(FakeUserConnection(events, uid=USER.id))


@pytest.fixture
def repos(pool: FakeUserPool, monkeypatch: pytest.MonkeyPatch) -> Repos:
    fake = Repos(pool.events)
    monkeypatch.setattr(competency_service, "competencies", fake.competencies)
    monkeypatch.setattr(competency_service, "career_goals", fake.career_goals)
    return fake


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []
    monkeypatch.setattr(session.time, "sleep", recorded.append)
    return recorded


def create(pool: FakeUserPool, data: CompetencyCreate = FULL) -> CompetencyRecord:
    return competency_service.create_competency(pool, USER, GOAL_ID, data)  # type: ignore[arg-type]


def listing(pool: FakeUserPool) -> list[CompetencyRecord]:
    return competency_service.list_competencies(pool, USER, GOAL_ID)  # type: ignore[arg-type]


# --- create_competency: the happy path --------------------------------------------------------


def test_create_returns_the_record_from_one_transaction(pool: FakeUserPool, repos: Repos) -> None:
    record = make_record(name="Terraform")
    repos.script(repos.competencies, "insert", record)

    assert create(pool) is record

    assert pool.events == [*START, "repo.insert", "COMMIT", "pool.release"]


@pytest.mark.parametrize("data", [FULL, MINIMAL], ids=["full", "minimal"])
def test_create_maps_every_field_explicitly_and_takes_the_owner_from_the_token(
    pool: FakeUserPool, repos: Repos, data: CompetencyCreate
) -> None:
    repos.script(repos.competencies, "insert", make_record())

    create(pool, data)

    repos.competencies.insert.assert_called_once_with(
        pool.conn,
        USER.id,
        GOAL_ID,
        name=data.name,
        description=data.description,
        rationale=data.rationale,
        category=data.category,
        knowledge_type=data.knowledge_type,
        importance=data.importance,
        expected_level=data.expected_level,
        display_order=data.display_order,
    )
    # a field added to the schema must be mapped on purpose, not silently dropped
    assert set(repos.competencies.insert.call_args.kwargs) == set(CompetencyCreate.model_fields)


def test_create_does_not_read_or_check_anything_before_the_insert(
    pool: FakeUserPool, repos: Repos
) -> None:
    repos.script(repos.competencies, "insert", make_record())

    create(pool)

    repos.competencies.list_by_goal.assert_not_called()
    repos.career_goals.get_by_id.assert_not_called()
    assert pool.events.count("pool.acquire") == 1


def test_create_does_not_modify_its_input(pool: FakeUserPool, repos: Repos) -> None:
    repos.script(repos.competencies, "insert", make_record())
    before = FULL.model_dump()

    create(pool, FULL)

    assert FULL.model_dump() == before


# --- create_competency: the rules of the database ---------------------------------------------


def test_the_active_limit_becomes_competency_limit_reached(
    pool: FakeUserPool, repos: Repos
) -> None:
    error = violation(errors.CheckViolation, "competencies_active_limit")
    repos.script(repos.competencies, "insert", error)

    with pytest.raises(CompetencyLimitReachedError) as exc_info:
        create(pool)

    assert exc_info.value.__cause__ is error
    assert pool.events == [*START, "repo.insert", "ROLLBACK", "pool.release"]  # no follow-up query


def test_a_taken_name_becomes_competency_name_exists(pool: FakeUserPool, repos: Repos) -> None:
    error = violation(errors.UniqueViolation, "competencies_active_name_unique")
    repos.script(repos.competencies, "insert", error)

    with pytest.raises(CompetencyNameExistsError) as exc_info:
        create(pool)

    assert exc_info.value.__cause__ is error
    assert pool.events == [*START, "repo.insert", "ROLLBACK", "pool.release"]


@pytest.mark.parametrize(
    "error",
    [
        violation(errors.UniqueViolation, "some_other_unique_index"),
        violation(errors.UniqueViolation, None),
        violation(errors.CheckViolation, "competencies_name_check"),
        violation(errors.CheckViolation, None),
        # a constraint name of the right kind but on the wrong class of error is not translated
        violation(errors.UniqueViolation, "competencies_active_limit"),
        violation(errors.CheckViolation, "competencies_active_name_unique"),
    ],
    ids=lambda e: f"{type(e).__mro__[1].__name__}-{e.diag.constraint_name}",
)
def test_unknown_constraints_are_raised_unchanged(
    pool: FakeUserPool, repos: Repos, error: psycopg.Error
) -> None:
    repos.script(repos.competencies, "insert", error)

    with pytest.raises(type(error)) as exc_info:
        create(pool)

    assert exc_info.value is error  # the very same object
    assert not isinstance(exc_info.value, AppError)
    repos.career_goals.get_by_id.assert_not_called()  # no classification read
    assert pool.events == [*START, "repo.insert", "ROLLBACK", "pool.release"]


# --- create_competency: a goal that is not active ---------------------------------------------


def goal_not_active() -> psycopg.Error:
    return violation(errors.CheckViolation, "competencies_goal_not_active")


def test_an_archived_goal_is_classified_after_the_rollback_in_a_new_transaction(
    pool: FakeUserPool, repos: Repos
) -> None:
    error = goal_not_active()
    repos.script(repos.competencies, "insert", error)
    repos.script(repos.career_goals, "get_by_id", goal("archived"))

    with pytest.raises(GoalArchivedError) as exc_info:
        create(pool)

    assert exc_info.value.message == GoalArchivedError.default_message
    assert exc_info.value.__cause__ is error
    assert pool.events == [
        # the failed transaction: rolled back and released ...
        *START,
        "repo.insert",
        "ROLLBACK",
        "pool.release",
        # ... and only then a NEW transaction with its own role and claims for the read
        *START,
        "repo.get_by_id",
        "COMMIT",
        "pool.release",
    ]
    repos.career_goals.get_by_id.assert_called_once_with(pool.conn, USER.id, GOAL_ID)


@pytest.mark.parametrize("script", [[None], [None, None]], ids=["missing-or-foreign", "again"])
def test_a_missing_or_foreign_goal_becomes_goal_not_found(
    pool: FakeUserPool, repos: Repos, script: list[Any]
) -> None:
    error = goal_not_active()
    repos.script(repos.competencies, "insert", error)
    repos.script(repos.career_goals, "get_by_id", *script)

    with pytest.raises(GoalNotFoundError) as exc_info:
        create(pool)

    assert exc_info.value.__cause__ is error


def test_an_active_goal_after_the_refusal_is_an_internal_error(
    pool: FakeUserPool, repos: Repos
) -> None:
    error = goal_not_active()
    repos.script(repos.competencies, "insert", error)
    repos.script(repos.career_goals, "get_by_id", goal("active"))

    with pytest.raises(RuntimeError) as exc_info:
        create(pool)

    assert not isinstance(exc_info.value, AppError)  # the API answers 500, not a client error
    assert exc_info.value.__cause__ is error


def test_the_classification_read_is_not_repeated_or_retried(
    pool: FakeUserPool, repos: Repos
) -> None:
    repos.script(repos.competencies, "insert", goal_not_active())
    repos.script(repos.career_goals, "get_by_id", deadlock())

    with pytest.raises(errors.DeadlockDetected):
        create(pool)

    assert repos.career_goals.get_by_id.call_count == 1


# --- create_competency: retry (implemented by run_user_transaction) ---------------------------


@pytest.mark.parametrize("error", [deadlock, serialization], ids=["40P01", "40001"])
def test_one_retryable_failure_then_success(
    pool: FakeUserPool, repos: Repos, error: Callable[[], psycopg.Error]
) -> None:
    record = make_record()
    repos.script(repos.competencies, "insert", error(), record)

    assert create(pool) is record

    assert pool.events == [
        *START,
        "repo.insert",
        "ROLLBACK",
        "pool.release",
        *START,
        "repo.insert",
        "COMMIT",
        "pool.release",
    ]
    assert repos.competencies.insert.call_count == 2
    repos.career_goals.get_by_id.assert_not_called()


def test_each_retry_inserts_the_same_data_for_the_same_user_and_goal(
    pool: FakeUserPool, repos: Repos
) -> None:
    repos.script(repos.competencies, "insert", deadlock(), serialization(), make_record())

    create(pool)

    first, second, third = repos.competencies.insert.call_args_list
    assert first == second == third


def test_three_failed_attempts_end_in_database_unavailable_without_a_classification_read(
    pool: FakeUserPool, repos: Repos
) -> None:
    repos.script(repos.competencies, "insert", deadlock(), serialization(), deadlock())

    with pytest.raises(DatabaseUnavailableError):
        create(pool)

    assert repos.competencies.insert.call_count == 3
    repos.career_goals.get_by_id.assert_not_called()
    assert pool.events.count("pool.acquire") == pool.events.count("pool.release") == 3


@pytest.mark.parametrize(
    "error",
    [
        errors.QueryCanceled("canceling statement due to statement timeout"),  # 57014
        errors.InsufficientPrivilege("permission denied"),  # 42501
        errors.ForeignKeyViolation("fk"),
        ValueError("a bug"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_other_errors_are_not_retried_and_are_raised_unchanged(
    pool: FakeUserPool, repos: Repos, error: Exception
) -> None:
    repos.script(repos.competencies, "insert", error)

    with pytest.raises(type(error)) as exc_info:
        create(pool)

    assert exc_info.value is error
    assert repos.competencies.insert.call_count == 1
    repos.career_goals.get_by_id.assert_not_called()


# --- list_competencies ------------------------------------------------------------------------


def test_list_returns_active_and_archived_competencies_in_the_order_of_the_repository(
    pool: FakeUserPool, repos: Repos
) -> None:
    records = [
        make_record(name="Docker"),
        make_record(name="Git", status="archived", display_order=1),
    ]
    repos.script(repos.career_goals, "get_by_id", goal())
    repos.script(repos.competencies, "list_by_goal", records)

    result = listing(pool)

    assert result is records
    assert [r.status for r in result] == ["active", "archived"]
    assert pool.events == [*START, "repo.get_by_id", "repo.list_by_goal", "COMMIT", "pool.release"]


def test_list_passes_the_user_from_the_token_and_the_goal_from_the_path(
    pool: FakeUserPool, repos: Repos
) -> None:
    repos.script(repos.career_goals, "get_by_id", goal())
    repos.script(repos.competencies, "list_by_goal", [])

    listing(pool)

    repos.career_goals.get_by_id.assert_called_once_with(pool.conn, USER.id, GOAL_ID)
    repos.competencies.list_by_goal.assert_called_once_with(pool.conn, USER.id, GOAL_ID)


def test_a_goal_without_competencies_gives_an_empty_list(pool: FakeUserPool, repos: Repos) -> None:
    repos.script(repos.career_goals, "get_by_id", goal())
    repos.script(repos.competencies, "list_by_goal", [])

    assert listing(pool) == []


@pytest.mark.parametrize("status", ["active", "archived"])
def test_list_works_for_a_goal_in_any_status(pool: FakeUserPool, repos: Repos, status: str) -> None:
    records = [make_record(status="archived")]
    repos.script(repos.career_goals, "get_by_id", goal(status))
    repos.script(repos.competencies, "list_by_goal", records)

    assert listing(pool) is records


def test_a_missing_or_foreign_goal_is_not_found_and_its_competencies_are_never_read(
    pool: FakeUserPool, repos: Repos
) -> None:
    repos.script(repos.career_goals, "get_by_id", None)

    with pytest.raises(GoalNotFoundError):
        listing(pool)

    repos.competencies.list_by_goal.assert_not_called()
    # the error is raised after the transaction ended, not inside it
    assert pool.events == [*START, "repo.get_by_id", "COMMIT", "pool.release"]


@pytest.mark.parametrize("error", [deadlock, serialization], ids=["40P01", "40001"])
def test_list_is_a_plain_transaction_and_is_never_retried(
    pool: FakeUserPool, repos: Repos, error: Callable[[], psycopg.Error]
) -> None:
    failure = error()
    repos.script(repos.career_goals, "get_by_id", failure)

    with pytest.raises(type(failure)) as exc_info:
        listing(pool)

    assert exc_info.value is failure
    assert repos.career_goals.get_by_id.call_count == 1
    assert pool.events == [*START, "repo.get_by_id", "ROLLBACK", "pool.release"]


# --- architecture ------------------------------------------------------------------------------


def test_the_service_does_not_depend_on_fastapi_or_http() -> None:
    source = Path(competency_service.__file__).read_text(encoding="utf-8")

    for forbidden in ("fastapi", "starlette", "HTTPException", "status_code"):
        assert forbidden not in source


# --- update_competency and archive_competency ---------------------------------------------------

COMPETENCY_ID = UUID("33333333-3333-3333-3333-333333333333")
PATCH = CompetencyUpdate(name="Kubernetes", category=None)


def update(pool: FakeUserPool) -> CompetencyRecord:
    return competency_service.update_competency(pool, USER, COMPETENCY_ID, PATCH)  # type: ignore[arg-type]


def archive(pool: FakeUserPool) -> CompetencyRecord:
    return competency_service.archive_competency(pool, USER, COMPETENCY_ID)  # type: ignore[arg-type]


CHANGES = pytest.mark.parametrize(
    ("call", "statement"),
    [(update, "update_active"), (archive, "archive_active")],
    ids=["update", "archive"],
)


@CHANGES
def test_a_change_that_applies_uses_one_transaction_and_reads_nothing(
    pool: FakeUserPool, repos: Repos, call: Callable[..., Any], statement: str
) -> None:
    record = make_record()
    repos.script(repos.competencies, statement, record)

    assert call(pool) is record

    assert pool.events == [*START, f"repo.{statement}", "COMMIT", "pool.release"]
    repos.competencies.get_by_id.assert_not_called()
    repos.career_goals.get_by_id.assert_not_called()


def test_update_sends_exactly_the_fields_of_the_request(pool: FakeUserPool, repos: Repos) -> None:
    repos.script(repos.competencies, "update_active", make_record())

    update(pool)

    repos.competencies.update_active.assert_called_once_with(
        pool.conn, USER.id, COMPETENCY_ID, {"name": "Kubernetes", "category": None}
    )


def test_archive_takes_the_owner_from_the_token(pool: FakeUserPool, repos: Repos) -> None:
    repos.script(repos.competencies, "archive_active", make_record(status="archived"))

    archive(pool)

    repos.competencies.archive_active.assert_called_once_with(pool.conn, USER.id, COMPETENCY_ID)


REASONS = pytest.mark.parametrize(
    ("competency_status", "goal_status", "expected"),
    [
        (None, None, CompetencyNotFoundError),
        ("archived", "active", CompetencyArchivedError),
        ("archived", "archived", CompetencyArchivedError),  # the resource before its parent
        ("active", "archived", GoalArchivedError),
    ],
    ids=["missing", "archived", "both-archived", "goal-archived"],
)


def script_state(repos: Repos, competency_status: str | None, goal_status: str | None) -> None:
    found = None if competency_status is None else make_record(status=competency_status)
    repos.script(repos.competencies, "get_by_id", found)
    if competency_status == "active":
        repos.script(repos.career_goals, "get_by_id", goal(goal_status or "active"))


@CHANGES
@REASONS
def test_a_change_that_matches_no_row_is_explained(
    pool: FakeUserPool,
    repos: Repos,
    call: Callable[..., Any],
    statement: str,
    competency_status: str | None,
    goal_status: str | None,
    expected: type[AppError],
) -> None:
    repos.script(repos.competencies, statement, None)
    script_state(repos, competency_status, goal_status)

    with pytest.raises(expected):
        call(pool)

    assert pool.events.count("pool.acquire") == 1
    assert "ROLLBACK" in pool.events and "COMMIT" not in pool.events


@CHANGES
@pytest.mark.parametrize("found_goal", [goal("active"), None], ids=["active-goal", "no-goal"])
def test_an_unexplained_unchanged_row_is_a_bug_and_not_a_client_error(
    pool: FakeUserPool,
    repos: Repos,
    call: Callable[..., Any],
    statement: str,
    found_goal: CareerGoalRecord | None,
) -> None:
    repos.script(repos.competencies, statement, None)
    repos.script(repos.competencies, "get_by_id", make_record())
    repos.script(repos.career_goals, "get_by_id", found_goal)

    with pytest.raises(RuntimeError, match="was not changed"):
        call(pool)


@CHANGES
@REASONS
def test_a_trigger_refusal_is_explained_in_a_new_transaction(
    pool: FakeUserPool,
    repos: Repos,
    call: Callable[..., Any],
    statement: str,
    competency_status: str | None,
    goal_status: str | None,
    expected: type[AppError],
) -> None:
    refusal = violation(errors.CheckViolation, "competencies_goal_not_active")
    repos.script(repos.competencies, statement, refusal)
    script_state(repos, competency_status, goal_status)

    with pytest.raises(expected):
        call(pool)

    assert pool.events.count("pool.acquire") == 2
    # the failed transaction is rolled back and released before anything is read
    first_release = pool.events.index("pool.release")
    assert pool.events.index("ROLLBACK") < first_release
    assert first_release < pool.events.index("repo.get_by_id")


@pytest.mark.parametrize("constraint", ["competencies_name_check", None])
@CHANGES
def test_another_check_violation_of_a_change_is_not_translated(
    pool: FakeUserPool,
    repos: Repos,
    call: Callable[..., Any],
    statement: str,
    constraint: str | None,
) -> None:
    refusal = violation(errors.CheckViolation, constraint)
    repos.script(repos.competencies, statement, refusal)

    with pytest.raises(errors.CheckViolation) as exc_info:
        call(pool)

    assert exc_info.value is refusal


def test_a_rename_to_a_taken_name_is_a_name_conflict(pool: FakeUserPool, repos: Repos) -> None:
    taken = violation(errors.UniqueViolation, "competencies_active_name_unique")
    repos.script(repos.competencies, "update_active", taken)

    with pytest.raises(CompetencyNameExistsError):
        update(pool)

    assert pool.events.count("pool.acquire") == 1


def test_another_unique_violation_of_an_update_is_not_translated(
    pool: FakeUserPool, repos: Repos
) -> None:
    other = violation(errors.UniqueViolation, "competencies_pkey")
    repos.script(repos.competencies, "update_active", other)

    with pytest.raises(errors.UniqueViolation):
        update(pool)


@CHANGES
@pytest.mark.parametrize("failure", [deadlock, serialization], ids=["deadlock", "serialization"])
def test_a_change_is_retried_as_a_whole_transaction(
    pool: FakeUserPool,
    repos: Repos,
    call: Callable[..., Any],
    statement: str,
    failure: Callable[[], BaseException],
) -> None:
    record = make_record()
    repos.script(repos.competencies, statement, failure(), record)

    assert call(pool) is record
    assert pool.events.count("pool.acquire") == 2
    assert pool.events.count("COMMIT") == 1


@CHANGES
def test_a_change_that_keeps_failing_is_reported_as_unavailable(
    pool: FakeUserPool, repos: Repos, call: Callable[..., Any], statement: str
) -> None:
    repos.script(repos.competencies, statement, deadlock())

    with pytest.raises(DatabaseUnavailableError):
        call(pool)

    assert pool.events.count("pool.acquire") == session.RETRY_MAX_ATTEMPTS
