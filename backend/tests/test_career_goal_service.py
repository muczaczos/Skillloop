from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from psycopg.errors import UniqueViolation

from app.core.errors import (
    ActiveGoalExistsError,
    GoalArchivedError,
    GoalNotFoundError,
    NoActiveGoalError,
)
from app.models.auth import AuthenticatedUser
from app.models.career_goal import CareerGoalRecord
from app.schemas.career_goal import CareerGoalCreate, CareerGoalUpdate
from app.services import career_goal_service

USER = AuthenticatedUser(id=uuid4())
GOAL_ID = uuid4()
POOL: Any = object()
CONN: Any = object()


def _goal(**overrides: Any) -> CareerGoalRecord:
    values: dict[str, Any] = {
        "id": GOAL_ID,
        "user_id": USER.id,
        "target_role": "Backend Engineer",
        "seniority": "mid",
        "description": None,
        "status": "active",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    return CareerGoalRecord(**{**values, **overrides})


class _Diag:
    def __init__(self, constraint_name: str | None) -> None:
        self.constraint_name = constraint_name


def _unique_violation(constraint: str | None) -> UniqueViolation:
    class _Violation(UniqueViolation):
        @property
        def diag(self) -> Any:  # psycopg builds diag from a server result; fake it
            return _Diag(constraint)

    return _Violation("duplicate key value violates unique constraint")


class Recorder:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.transaction_args: tuple[Any, Any] | None = None
        # The exception that tried to leave the transaction block, if any.
        self.escaping: BaseException | None = None


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    rec = Recorder()

    @contextmanager
    def fake_user_transaction(pool: Any, user: Any) -> Iterator[Any]:
        rec.transaction_args = (pool, user)
        rec.events.append("begin")
        try:
            yield CONN
        except BaseException as exc:
            rec.escaping = exc
            rec.events.append("rollback")
            raise
        else:
            rec.events.append("commit")

    monkeypatch.setattr(career_goal_service, "user_transaction", fake_user_transaction)
    return rec


def _recording(mock: MagicMock, recorder: Recorder, name: str) -> Any:
    def call(*args: Any, **kwargs: Any) -> Any:
        recorder.events.append(name)
        result = getattr(mock, name).result
        if isinstance(result, BaseException):
            raise result
        return result

    return call


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch, recorder: Recorder) -> MagicMock:
    """Mock of the repository module. Set `repo.<function>.result` to a return value, or to
    an exception instance to have it raised."""
    mock = MagicMock()
    for name in ("insert", "get_active", "get_by_id", "update_active", "archive_active"):
        getattr(mock, name).side_effect = _recording(mock, recorder, name)
    monkeypatch.setattr(career_goal_service, "career_goals", mock)
    return mock


CREATE = CareerGoalCreate(target_role="Backend Engineer", seniority="mid", description="API")


# --- create_goal -------------------------------------------------------------------------


def test_create_goal_inserts_and_returns_record(repo: MagicMock, recorder: Recorder) -> None:
    goal = _goal(description="API")
    repo.insert.result = goal

    assert career_goal_service.create_goal(POOL, USER, CREATE) is goal

    repo.insert.assert_called_once_with(
        CONN, USER.id, target_role="Backend Engineer", seniority="mid", description="API"
    )
    assert recorder.transaction_args == (POOL, USER)
    assert recorder.events == ["begin", "insert", "commit"]


def test_create_goal_does_not_check_for_existing_goal_first(repo: MagicMock) -> None:
    repo.insert.result = _goal()

    career_goal_service.create_goal(POOL, USER, CREATE)

    repo.get_active.assert_not_called()
    repo.get_by_id.assert_not_called()


def test_active_goal_constraint_becomes_active_goal_exists(
    repo: MagicMock, recorder: Recorder
) -> None:
    violation = _unique_violation("one_active_goal_per_user")
    repo.insert.result = violation

    with pytest.raises(ActiveGoalExistsError) as exc_info:
        career_goal_service.create_goal(POOL, USER, CREATE)

    assert exc_info.value.__cause__ is violation
    assert recorder.events == ["begin", "insert", "rollback"]


def test_original_unique_violation_leaves_transaction_untranslated(
    repo: MagicMock, recorder: Recorder
) -> None:
    """The mapping happens after the block: the transaction saw the raw UniqueViolation
    and rolled back before ActiveGoalExistsError existed."""
    violation = _unique_violation("one_active_goal_per_user")
    repo.insert.result = violation

    with pytest.raises(ActiveGoalExistsError):
        career_goal_service.create_goal(POOL, USER, CREATE)

    assert recorder.escaping is violation
    assert not isinstance(recorder.escaping, ActiveGoalExistsError)


@pytest.mark.parametrize("constraint", ["some_other_unique", None])
def test_other_unique_violations_pass_through(
    repo: MagicMock, recorder: Recorder, constraint: str | None
) -> None:
    violation = _unique_violation(constraint)
    repo.insert.result = violation

    with pytest.raises(UniqueViolation) as exc_info:
        career_goal_service.create_goal(POOL, USER, CREATE)

    assert exc_info.value is violation
    assert not isinstance(exc_info.value, ActiveGoalExistsError)
    assert recorder.events == ["begin", "insert", "rollback"]


# --- get_active_goal ---------------------------------------------------------------------


def test_get_active_goal_returns_record(repo: MagicMock, recorder: Recorder) -> None:
    goal = _goal()
    repo.get_active.result = goal

    assert career_goal_service.get_active_goal(POOL, USER) is goal

    repo.get_active.assert_called_once_with(CONN, USER.id)
    assert recorder.transaction_args == (POOL, USER)
    assert recorder.events == ["begin", "get_active", "commit"]


def test_get_active_goal_missing_raises_no_active_goal(repo: MagicMock) -> None:
    repo.get_active.result = None

    with pytest.raises(NoActiveGoalError):
        career_goal_service.get_active_goal(POOL, USER)


# --- update_goal -------------------------------------------------------------------------


def test_update_goal_returns_updated_record(repo: MagicMock, recorder: Recorder) -> None:
    updated = _goal(target_role="Data Engineer")
    repo.update_active.result = updated
    data = CareerGoalUpdate(target_role="Data Engineer", description=None)

    assert career_goal_service.update_goal(POOL, USER, GOAL_ID, data) is updated

    # An explicit null is a change; omitted fields are not sent.
    repo.update_active.assert_called_once_with(
        CONN, USER.id, GOAL_ID, {"target_role": "Data Engineer", "description": None}
    )
    repo.get_by_id.assert_not_called()
    assert recorder.transaction_args == (POOL, USER)
    assert recorder.events == ["begin", "update_active", "commit"]


def test_update_goal_missing_raises_not_found(repo: MagicMock, recorder: Recorder) -> None:
    repo.update_active.result = None
    repo.get_by_id.result = None

    with pytest.raises(GoalNotFoundError):
        career_goal_service.update_goal(POOL, USER, GOAL_ID, CareerGoalUpdate(seniority="senior"))

    repo.get_by_id.assert_called_once_with(CONN, USER.id, GOAL_ID)
    assert recorder.events == ["begin", "update_active", "get_by_id", "rollback"]


def test_update_goal_archived_raises_goal_archived(repo: MagicMock) -> None:
    repo.update_active.result = None
    repo.get_by_id.result = _goal(status="archived")

    with pytest.raises(GoalArchivedError):
        career_goal_service.update_goal(POOL, USER, GOAL_ID, CareerGoalUpdate(seniority="senior"))


def test_update_goal_still_active_is_an_internal_error(repo: MagicMock) -> None:
    repo.update_active.result = None
    repo.get_by_id.result = _goal(status="active")

    with pytest.raises(RuntimeError):
        career_goal_service.update_goal(POOL, USER, GOAL_ID, CareerGoalUpdate(seniority="senior"))


# --- archive_goal ------------------------------------------------------------------------


def test_archive_goal_returns_archived_record(repo: MagicMock, recorder: Recorder) -> None:
    archived = _goal(status="archived")
    repo.archive_active.result = archived

    assert career_goal_service.archive_goal(POOL, USER, GOAL_ID) is archived

    repo.archive_active.assert_called_once_with(CONN, USER.id, GOAL_ID)
    repo.get_by_id.assert_not_called()
    assert recorder.transaction_args == (POOL, USER)
    assert recorder.events == ["begin", "archive_active", "commit"]


def test_archive_goal_missing_raises_not_found(repo: MagicMock) -> None:
    repo.archive_active.result = None
    repo.get_by_id.result = None

    with pytest.raises(GoalNotFoundError):
        career_goal_service.archive_goal(POOL, USER, GOAL_ID)

    repo.get_by_id.assert_called_once_with(CONN, USER.id, GOAL_ID)


def test_archive_goal_already_archived_raises_goal_archived(repo: MagicMock) -> None:
    repo.archive_active.result = None
    repo.get_by_id.result = _goal(status="archived")

    with pytest.raises(GoalArchivedError):
        career_goal_service.archive_goal(POOL, USER, GOAL_ID)


def test_archive_goal_still_active_is_an_internal_error(repo: MagicMock) -> None:
    repo.archive_active.result = None
    repo.get_by_id.result = _goal(status="active")

    with pytest.raises(RuntimeError):
        career_goal_service.archive_goal(POOL, USER, GOAL_ID)
