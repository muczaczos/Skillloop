"""Unit tests of run_user_transaction() with doubles: no database, no network.

What is checked is the ORDER of events across attempts (the doubles record every step in one
list), not only how many times something was called: an attempt must be rolled back and its
connection released before the next one starts, and every attempt must bind the role and the
claims again.
"""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from app.core.errors import ActiveGoalExistsError
from app.db import session
from app.db.errors import DatabaseUnavailableError
from app.db.session import (
    RETRY_MAX_ATTEMPTS,
    RETRY_PAUSE_MAX_SECONDS,
    RETRY_PAUSE_MIN_SECONDS,
    NestedUserTransactionError,
    run_user_transaction,
    user_transaction,
)
from app.models.auth import AuthenticatedUser
from tests.db_fakes import FakeUserConnection, FakeUserPool

USER = AuthenticatedUser(id=UUID("11111111-1111-1111-1111-111111111111"))
CANARY = "CANARY-7d3f1"  # stands for SQL text or row values inside a database error message

# What user_transaction() does before the work of every attempt, in this order.
START = [
    "pool.acquire",
    "BEGIN",
    "SET LOCAL ROLE authenticated",
    "SELECT set_config",
    "select auth.uid(), current_user",
]


def deadlock() -> errors.DeadlockDetected:
    return errors.DeadlockDetected(f"deadlock detected. DETAIL: {CANARY}")


def serialization() -> errors.SerializationFailure:
    return errors.SerializationFailure(f"could not serialize access. DETAIL: {CANARY}")


class CommitFailingConnection(FakeUserConnection):
    """Like FakeUserConnection, but the COMMIT of the n-th transaction raises what is scripted.

    A failed COMMIT leaves nothing committed, which is what PostgreSQL does for 40001."""

    def __init__(self, events: list[str], commit_errors: list[BaseException | None], **kw: Any):
        super().__init__(events, **kw)
        self.commit_errors = commit_errors
        self.transactions = 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.events.append("BEGIN")
        index = self.transactions
        self.transactions += 1
        try:
            yield
        except BaseException:
            self.events.append("ROLLBACK")
            raise
        error = self.commit_errors[index] if index < len(self.commit_errors) else None
        if error is not None:
            self.events.append("COMMIT failed")
            raise error
        self.events.append("COMMIT")


class Work:
    """The operation of a transaction: records its calls in the event list and does, in turn,
    what the script says (raise an exception, or return a value)."""

    def __init__(self, pool: FakeUserPool, script: list[Any]) -> None:
        self.pool = pool
        self.script = script
        self.connections: list[Any] = []

    def __call__(self, conn: Any) -> Any:
        self.connections.append(conn)
        step = self.script[len(self.connections) - 1]
        self.pool.events.append(f"work#{len(self.connections)}")
        if isinstance(step, BaseException):
            raise step
        return step


def make_pool(commit_errors: list[BaseException | None] | None = None) -> FakeUserPool:
    events: list[str] = []
    if commit_errors is None:
        return FakeUserPool(FakeUserConnection(events, uid=USER.id))
    return FakeUserPool(CommitFailingConnection(events, commit_errors, uid=USER.id))


def run(pool: FakeUserPool, work: Work, **kwargs: Any) -> Any:
    return run_user_transaction(pool, USER, work, **kwargs)  # type: ignore[arg-type]


def failed_attempt(number: int) -> list[str]:
    """Events of an attempt whose work raised: rolled back, connection released."""
    return [*START, f"work#{number}", "ROLLBACK", "pool.release"]


def successful_attempt(number: int) -> list[str]:
    return [*START, f"work#{number}", "COMMIT", "pool.release"]


@pytest.fixture(autouse=True)
def pauses(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """No real sleeping in the tests; the requested pauses are recorded."""
    recorded: list[float] = []
    monkeypatch.setattr(session.time, "sleep", recorded.append)
    return recorded


# --- the attempts -----------------------------------------------------------------------------


def test_success_at_the_first_attempt_is_a_single_transaction_and_no_pause(
    pauses: list[float],
) -> None:
    pool = make_pool()
    work = Work(pool, ["result"])

    assert run(pool, work) == "result"

    assert pool.events == successful_attempt(1)
    assert pauses == []


@pytest.mark.parametrize("error", [deadlock, serialization], ids=["40P01", "40001"])
def test_a_retryable_error_is_followed_by_a_second_transaction(error: Any) -> None:
    pool = make_pool()
    work = Work(pool, [error(), "result"])

    assert run(pool, work) == "result"

    assert pool.events == [*failed_attempt(1), *successful_attempt(2)]


def test_two_failures_then_success_at_the_third_attempt() -> None:
    pool = make_pool()
    work = Work(pool, [deadlock(), serialization(), "result"])

    assert run(pool, work) == "result"

    assert pool.events == [*failed_attempt(1), *failed_attempt(2), *successful_attempt(3)]


@pytest.mark.parametrize("error", [deadlock, serialization], ids=["40P01", "40001"])
def test_three_failures_end_in_database_unavailable(error: Any, pauses: list[float]) -> None:
    pool = make_pool()
    work = Work(pool, [error(), error(), error()])

    with pytest.raises(DatabaseUnavailableError) as exc_info:
        run(pool, work)

    assert pool.events == [*failed_attempt(1), *failed_attempt(2), *failed_attempt(3)]
    assert len(pauses) == 2  # between the attempts, not after the last one
    assert isinstance(
        exc_info.value.__cause__, errors.DeadlockDetected | errors.SerializationFailure
    )


def test_the_result_is_returned_unchanged_after_a_retry() -> None:
    pool = make_pool()
    result = object()
    work = Work(pool, [deadlock(), result])

    assert run(pool, work) is result


# --- failures that are not retried ------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        errors.UniqueViolation("duplicate"),
        errors.CheckViolation("check"),
        errors.QueryCanceled("canceling statement due to statement timeout"),  # 57014
        errors.InsufficientPrivilege("denied"),
        errors.LockNotAvailable("lock"),  # 55P03: another error of the same class family
        psycopg.errors.InFailedSqlTransaction("aborted"),  # 25P02
    ],
    ids=lambda e: type(e).__name__,
)
def test_any_other_database_error_is_raised_at_once_and_unchanged(error: BaseException) -> None:
    pool = make_pool()
    work = Work(pool, [error, "never reached"])

    with pytest.raises(type(error)) as exc_info:
        run(pool, work)

    assert exc_info.value is error
    assert pool.events == failed_attempt(1)


def test_a_lost_connection_is_translated_as_before_and_not_retried() -> None:
    pool = make_pool()
    work = Work(pool, [psycopg.OperationalError("connection lost"), "never reached"])

    with pytest.raises(DatabaseUnavailableError):
        run(pool, work)

    assert pool.events == failed_attempt(1)


@pytest.mark.parametrize(
    "error",
    [ValueError("boom"), ActiveGoalExistsError(), KeyError("x"), RuntimeError("bug")],
    ids=lambda e: type(e).__name__,
)
def test_an_error_of_the_application_is_raised_at_once_and_unchanged(error: Exception) -> None:
    pool = make_pool()
    work = Work(pool, [error, "never reached"])

    with pytest.raises(type(error)) as exc_info:
        run(pool, work)

    assert exc_info.value is error
    assert pool.events == failed_attempt(1)


def test_a_failure_to_get_a_connection_is_not_retried() -> None:
    pool = FakeUserPool(fail_acquire=True)
    work = Work(pool, ["never reached"])

    with pytest.raises(DatabaseUnavailableError):
        run(pool, work)

    assert work.connections == []  # the work never ran
    assert pool.events == []


# --- rollback, a fresh transaction and a fresh identity for every attempt ----------------------


def test_every_attempt_is_rolled_back_and_released_before_the_next_one_starts() -> None:
    pool = make_pool()
    work = Work(pool, [deadlock(), serialization(), "result"])

    run(pool, work)

    events = pool.events
    acquires = [i for i, e in enumerate(events) if e == "pool.acquire"]
    assert len(acquires) == 3
    for later in acquires[1:]:
        assert events[later - 2 : later] == ["ROLLBACK", "pool.release"]


def test_every_attempt_sets_the_role_and_the_claims_again() -> None:
    pool = make_pool()
    work = Work(pool, [deadlock(), serialization(), "result"])

    run(pool, work)

    assert pool.events.count("SET LOCAL ROLE authenticated") == 3
    assert pool.events.count("SELECT set_config") == 3
    assert len(pool.conn.params) == 3
    for (claims,) in pool.conn.params:
        assert json.loads(claims) == {"sub": str(USER.id), "role": "authenticated"}
    # the role and the claims are set BEFORE the work of the same attempt
    for number in (1, 2, 3):
        work_index = pool.events.index(f"work#{number}")
        assert pool.events[work_index - 3 : work_index] == START[2:]


def test_a_failed_commit_is_retried_and_the_work_is_not_committed_twice() -> None:
    pool = make_pool(commit_errors=[serialization(), None])
    work = Work(pool, ["first", "second"])

    assert run(pool, work) == "second"

    assert pool.events == [
        *START,
        "work#1",
        "COMMIT failed",
        "pool.release",
        *START,
        "work#2",
        "COMMIT",
        "pool.release",
    ]
    assert pool.events.count("COMMIT") == 1


def test_a_failure_in_the_work_then_a_failed_commit_then_success() -> None:
    pool = make_pool(commit_errors=[None, deadlock(), None])
    work = Work(pool, [deadlock(), "second", "third"])

    assert run(pool, work) == "third"

    assert [e for e in pool.events if e in ("COMMIT", "COMMIT failed", "ROLLBACK")] == [
        "ROLLBACK",
        "COMMIT failed",
        "COMMIT",
    ]
    assert pool.events.count("pool.acquire") == pool.events.count("pool.release") == 3


def test_when_the_attempts_run_out_every_connection_has_been_cleaned_up() -> None:
    pool = make_pool()
    work = Work(pool, [deadlock(), serialization(), deadlock()])

    with pytest.raises(DatabaseUnavailableError):
        run(pool, work)

    assert pool.events.count("pool.acquire") == pool.events.count("pool.release") == 3
    assert pool.events.count("ROLLBACK") == 3
    assert "COMMIT" not in pool.events
    assert pool.events[-2:] == ["ROLLBACK", "pool.release"]
    # the guard against nested transactions is released as well
    with user_transaction(pool, USER):  # type: ignore[arg-type]
        pass


def test_the_work_gets_the_connection_of_its_own_attempt() -> None:
    pool = make_pool()
    work = Work(pool, [deadlock(), "result"])

    run(pool, work)

    assert work.connections == [pool.conn, pool.conn]


# --- the pause, the limits and what is left alone ---------------------------------------------


def test_the_pause_between_attempts_is_short_and_random(pauses: list[float]) -> None:
    pool = make_pool()
    work = Work(pool, [deadlock(), deadlock(), "result"])

    run(pool, work)

    assert len(pauses) == 2
    assert all(RETRY_PAUSE_MIN_SECONDS <= p <= RETRY_PAUSE_MAX_SECONDS for p in pauses)
    assert RETRY_PAUSE_MAX_SECONDS <= 0.1  # a retry must stay cheap


def test_the_default_is_three_attempts_in_total() -> None:
    assert RETRY_MAX_ATTEMPTS == 3


@pytest.mark.parametrize(("max_attempts", "expected_attempts"), [(1, 1), (2, 2), (5, 5)])
def test_max_attempts_is_the_total_number_of_attempts(
    max_attempts: int, expected_attempts: int
) -> None:
    pool = make_pool()
    work = Work(pool, [deadlock() for _ in range(max_attempts)])

    with pytest.raises(DatabaseUnavailableError):
        run(pool, work, max_attempts=max_attempts)

    assert pool.events.count("pool.acquire") == expected_attempts


@pytest.mark.parametrize("bad", [0, -1])
def test_fewer_than_one_attempt_is_a_programming_error(bad: int) -> None:
    pool = make_pool()

    with pytest.raises(ValueError, match="max_attempts"):
        run(pool, Work(pool, ["unused"]), max_attempts=bad)

    assert pool.events == []


def test_a_nested_call_is_refused_and_not_retried() -> None:
    pool = make_pool()
    work = Work(pool, ["never reached"])

    with (
        pytest.raises(NestedUserTransactionError),
        user_transaction(pool, USER),  # type: ignore[arg-type]
    ):
        run(pool, work)

    assert work.connections == []


# --- nothing from the failed attempt leaks ----------------------------------------------------


def test_the_error_message_and_the_logs_hold_only_the_sqlstate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pool = make_pool()
    work = Work(pool, [deadlock(), serialization(), deadlock()])

    with (
        caplog.at_level(logging.DEBUG, logger="app.db.session"),
        pytest.raises(DatabaseUnavailableError) as exc_info,
    ):
        run(pool, work)

    assert CANARY not in str(exc_info.value)
    assert "40P01" in str(exc_info.value)
    assert CANARY not in caplog.text
    assert "40001" in caplog.text and "40P01" in caplog.text
    assert [r.levelno for r in caplog.records] == [logging.WARNING, logging.WARNING, logging.ERROR]


def test_existing_services_do_not_use_the_retry() -> None:
    services = Path(session.__file__).resolve().parents[1] / "services"

    for module in ("profile_service.py", "career_goal_service.py"):
        assert "run_user_transaction" not in (services / module).read_text(encoding="utf-8")
