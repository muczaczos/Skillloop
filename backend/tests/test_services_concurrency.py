"""Concurrency of the career goal services against a REAL local PostgreSQL / Supavisor.

The guards under test live in the database (unique index one_active_goal_per_user, row locks,
the `status = 'active'` condition of the UPDATEs); the services only give their outcome a
meaning. Needs TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL (see db_integration_helpers).

How the tests are coordinated (no sleep() decides any ordering):
- A service commits when it returns, so it cannot be paused halfway. To keep a transaction
  open on purpose, `held_transaction` runs one on its own thread with the real
  user_transaction() and repository functions, and commits it only when told to.
- "The second operation is waiting for the first" is proven, not assumed: the test asks
  PostgreSQL (pg_stat_activity + pg_blocking_pids, on the admin connection) until some backend
  is blocked by the holder's backend pid with wait_event_type = 'Lock'. The polling loop only
  paces the queries; its deadline turns a missing lock wait into a failing test.
- Operations that must start together wait on a threading.Barrier. A barrier does not order
  the statements, so those tests assert only outcomes that hold in EVERY interleaving.
- Every wait has a timeout; the holder is always released and joined, in `finally`.
"""

import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from app.core.errors import (
    ActiveGoalExistsError,
    GoalArchivedError,
    GoalNotFoundError,
)
from app.db.session import user_transaction
from app.models.auth import AuthenticatedUser
from app.models.career_goal import CareerGoalRecord
from app.repositories import career_goals
from app.schemas.career_goal import CareerGoalCreate, CareerGoalUpdate
from app.schemas.profile import ProfileUpdate
from app.services import career_goal_service, profile_service
from tests.db_integration_helpers import (
    TEST_DATABASE_ADMIN_URL,
    TEST_DATABASE_URL,
    open_api_pool,
)

pytestmark = pytest.mark.skipif(
    not (TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL),
    reason="TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL are not both set",
)

CREATE = CareerGoalCreate(target_role="Backend Engineer", seniority="mid", description="API")
TIMEOUT = 20.0  # seconds; every wait in this module is bounded by it


@pytest.fixture
def pool4() -> Iterator[ConnectionPool]:
    """Several connections: a holder, a waiter and the others must be able to run at once."""
    pool = open_api_pool(max_size=4, timeout=TIMEOUT)
    try:
        yield pool
    finally:
        pool.close()


# --- coordination helpers ----------------------------------------------------------------


@dataclass
class Held:
    """A transaction that stays open until release() is called."""

    pid: int
    _release: threading.Event

    def release(self) -> None:
        self._release.set()


@contextmanager
def held_transaction(
    pool: ConnectionPool, user: AuthenticatedUser, action: Callable[[psycopg.Connection], Any]
) -> Iterator[Held]:
    """Run `action` in a real user_transaction on its own thread and keep that transaction
    open (locks held) until the block ends or release() is called. Then it COMMITS."""
    ready, release = threading.Event(), threading.Event()
    state: dict[str, Any] = {}

    def run() -> None:
        try:
            with user_transaction(pool, user) as conn:
                state["pid"] = conn.execute("select pg_backend_pid()").fetchone()[0]  # type: ignore[index]
                action(conn)
                ready.set()
                release.wait(TIMEOUT)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the test thread
            state["error"] = exc
        finally:
            ready.set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        assert ready.wait(TIMEOUT), "the holder transaction did not start"
        if "error" in state:
            raise state["error"]
        yield Held(state["pid"], release)
    finally:
        release.set()
        thread.join(TIMEOUT)
    assert not thread.is_alive(), "the holder transaction did not finish"
    if "error" in state:
        raise state["error"]


def wait_until_blocked_by(admin: psycopg.Connection, holder_pid: int) -> None:
    """Return once some backend is waiting for a lock that `holder_pid` holds."""
    deadline = time.monotonic() + TIMEOUT
    pacing = threading.Event()  # never set: wait(0.02) is only a pause between queries
    while time.monotonic() < deadline:
        row = admin.execute(
            "select count(*) from pg_stat_activity "
            "where wait_event_type = 'Lock' and %s = any(pg_blocking_pids(pid))",
            [holder_pid],
        ).fetchone()
        if row and row[0] >= 1:
            return
        pacing.wait(0.02)
    pytest.fail("the second operation never started waiting for the holder's lock")


def start_waiter(call: Callable[[], Any]) -> tuple[ThreadPoolExecutor, Future[Any]]:
    executor = ThreadPoolExecutor(max_workers=1)
    return executor, executor.submit(call)


def run_in_parallel(*calls: Callable[[], Any]) -> list[Any]:
    """Start all calls at the same moment (barrier); return each result or the exception."""
    barrier = threading.Barrier(len(calls))

    def wrap(call: Callable[[], Any]) -> Any:
        barrier.wait(TIMEOUT)
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - returned for the test to inspect
            return exc

    with ThreadPoolExecutor(max_workers=len(calls)) as executor:
        futures = [executor.submit(wrap, call) for call in calls]
        return [future.result(timeout=TIMEOUT) for future in futures]


def admin_rows(admin: psycopg.Connection, user: AuthenticatedUser) -> list[tuple[Any, ...]]:
    return admin.execute(
        "select target_role, seniority, description, status from public.career_goals "
        "where user_id = %s order by created_at, status",
        [user.id],
    ).fetchall()


def admin_active_count(admin: psycopg.Connection, user: AuthenticatedUser) -> int:
    row = admin.execute(
        "select count(*) from public.career_goals where user_id = %s and status = 'active'",
        [user.id],
    ).fetchone()
    assert row
    return int(row[0])


def new_goal(pool: ConnectionPool, user: AuthenticatedUser) -> CareerGoalRecord:
    return career_goal_service.create_goal(pool, user, CREATE)


# --- 1. two parallel creates -------------------------------------------------------------


def test_1_two_parallel_creates_one_wins(
    pool4: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    user = users[0]

    results = run_in_parallel(
        lambda: career_goal_service.create_goal(pool4, user, CREATE),
        lambda: career_goal_service.create_goal(pool4, user, CREATE),
    )

    created = [r for r in results if isinstance(r, CareerGoalRecord)]
    refused = [r for r in results if isinstance(r, ActiveGoalExistsError)]
    assert len(created) == 1 and len(refused) == 1, results
    assert admin_active_count(admin, user) == 1
    assert career_goal_service.get_active_goal(pool4, user) == created[0]


# --- 1b. create while another create transaction is open ---------------------------------


def test_1b_create_waits_for_open_create_then_active_goal_exists(
    pool4: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    user = users[0]

    with held_transaction(
        pool4,
        user,
        lambda conn: career_goals.insert(
            conn, user.id, target_role="Backend Engineer", seniority="mid", description="API"
        ),
    ) as held:
        executor, future = start_waiter(
            lambda: career_goal_service.create_goal(pool4, user, CREATE)
        )
        with executor:
            wait_until_blocked_by(admin, held.pid)
            assert not future.done()
            held.release()
            with pytest.raises(ActiveGoalExistsError) as exc_info:
                future.result(timeout=TIMEOUT)

    # The loser was stopped by the database constraint, not by a check made beforehand.
    cause = exc_info.value.__cause__
    assert isinstance(cause, psycopg.errors.UniqueViolation)
    assert cause.diag.constraint_name == "one_active_goal_per_user"
    assert admin_active_count(admin, user) == 1


# --- 2. update while an archive transaction is open --------------------------------------


def test_2_update_waits_for_archive_then_sees_archived(
    pool4: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    user = users[0]
    goal = new_goal(pool4, user)

    with held_transaction(
        pool4, user, lambda conn: career_goals.archive_active(conn, user.id, goal.id)
    ) as held:
        executor, future = start_waiter(
            lambda: career_goal_service.update_goal(
                pool4, user, goal.id, CareerGoalUpdate(seniority="senior")
            )
        )
        with executor:
            wait_until_blocked_by(admin, held.pid)
            assert not future.done()
            held.release()
            with pytest.raises(GoalArchivedError):
                future.result(timeout=TIMEOUT)

    assert admin_rows(admin, user) == [("Backend Engineer", "mid", "API", "archived")]


# --- 3. two parallel archives ------------------------------------------------------------


def test_3_two_parallel_archives_one_wins(
    pool4: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    user = users[0]
    goal = new_goal(pool4, user)

    results = run_in_parallel(
        lambda: career_goal_service.archive_goal(pool4, user, goal.id),
        lambda: career_goal_service.archive_goal(pool4, user, goal.id),
    )

    archived = [r for r in results if isinstance(r, CareerGoalRecord)]
    refused = [r for r in results if isinstance(r, GoalArchivedError)]
    assert len(archived) == 1 and len(refused) == 1, results
    assert archived[0].status == "archived"
    assert admin_active_count(admin, user) == 0


# --- 3b. archive while another archive transaction is open -------------------------------


def test_3b_archive_waits_for_open_archive_then_goal_archived(
    pool4: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    user = users[0]
    goal = new_goal(pool4, user)

    with held_transaction(
        pool4, user, lambda conn: career_goals.archive_active(conn, user.id, goal.id)
    ) as held:
        executor, future = start_waiter(
            lambda: career_goal_service.archive_goal(pool4, user, goal.id)
        )
        with executor:
            wait_until_blocked_by(admin, held.pid)
            assert not future.done()
            held.release()
            with pytest.raises(GoalArchivedError):
                future.result(timeout=TIMEOUT)

    assert admin_rows(admin, user) == [("Backend Engineer", "mid", "API", "archived")]
    assert admin_active_count(admin, user) == 0


# --- 4. create while an archive transaction is open --------------------------------------


def test_4_create_waits_for_archive_then_succeeds(
    pool4: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    user = users[0]
    old = new_goal(pool4, user)

    with held_transaction(
        pool4, user, lambda conn: career_goals.archive_active(conn, user.id, old.id)
    ) as held:
        executor, future = start_waiter(
            lambda: career_goal_service.create_goal(pool4, user, CREATE)
        )
        with executor:
            wait_until_blocked_by(admin, held.pid)
            assert not future.done()
            held.release()
            created = future.result(timeout=TIMEOUT)

    assert created.id != old.id and created.status == "active"
    assert sorted(row[3] for row in admin_rows(admin, user)) == ["active", "archived"]
    assert career_goal_service.get_active_goal(pool4, user) == created


# --- 5. two updates of different fields --------------------------------------------------


def test_5a_update_waits_for_open_update_and_keeps_both_changes(
    pool4: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    user = users[0]
    goal = new_goal(pool4, user)

    with held_transaction(
        pool4,
        user,
        lambda conn: career_goals.update_active(
            conn, user.id, goal.id, {"target_role": "Data Engineer"}
        ),
    ) as held:
        executor, future = start_waiter(
            lambda: career_goal_service.update_goal(
                pool4, user, goal.id, CareerGoalUpdate(seniority="senior")
            )
        )
        with executor:
            wait_until_blocked_by(admin, held.pid)
            assert not future.done()
            held.release()
            result = future.result(timeout=TIMEOUT)

    # The waiting UPDATE re-reads the committed row, so its result already has both changes.
    assert (result.target_role, result.seniority) == ("Data Engineer", "senior")
    assert admin_rows(admin, user) == [("Data Engineer", "senior", "API", "active")]


def test_5b_parallel_updates_of_different_fields_lose_nothing(
    pool4: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    user = users[0]
    goal = new_goal(pool4, user)
    seniorities = ["junior", "senior", "mid", "junior", "senior"]

    for round_number, seniority in enumerate(seniorities):
        role = f"Role {round_number}"
        results = run_in_parallel(
            lambda role=role: career_goal_service.update_goal(
                pool4, user, goal.id, CareerGoalUpdate(target_role=role)
            ),
            lambda seniority=seniority: career_goal_service.update_goal(
                pool4,
                user,
                goal.id,
                CareerGoalUpdate(seniority=seniority),  # type: ignore[arg-type]
            ),
        )

        assert all(isinstance(r, CareerGoalRecord) for r in results), results
        assert admin_rows(admin, user) == [(role, seniority, "API", "active")]


# --- 6. two users on one pool ------------------------------------------------------------


@pytest.mark.parametrize("pool_size", [1, 2])
def test_6_two_users_sharing_a_pool_never_mix_context(
    users: list[AuthenticatedUser], admin: psycopg.Connection, pool_size: int
) -> None:
    """pool_size=1: both users run on the very same database session, one after the other."""
    pool = open_api_pool(max_size=pool_size, timeout=TIMEOUT)
    try:
        goals: dict[UUID, CareerGoalRecord] = {}
        for user in users[:2]:
            goals[user.id] = career_goal_service.create_goal(
                pool,
                user,
                CareerGoalCreate(target_role=f"Role of {user.id}", seniority="mid"),
            )
        rounds = 15

        def worker(me: AuthenticatedUser, other: AuthenticatedUser) -> None:
            for round_number in range(rounds):
                name = f"{me.id}-{round_number}"
                updated = profile_service.update_profile(pool, me, ProfileUpdate(display_name=name))
                assert updated.id == me.id and updated.display_name == name
                profile = profile_service.get_profile(pool, me)
                assert profile.id == me.id and profile.display_name == name
                active = career_goal_service.get_active_goal(pool, me)
                assert active == goals[me.id] and active.user_id == me.id
                # The other user's goal does not exist for me, and stays untouched.
                with pytest.raises(GoalNotFoundError):
                    career_goal_service.update_goal(
                        pool, me, goals[other.id].id, CareerGoalUpdate(seniority="senior")
                    )
                with pytest.raises(GoalNotFoundError):
                    career_goal_service.archive_goal(pool, me, goals[other.id].id)

        first, second = users[0], users[1]
        results = run_in_parallel(lambda: worker(first, second), lambda: worker(second, first))

        assert results == [None, None], results
    finally:
        pool.close()

    for user in users[:2]:
        assert admin_rows(admin, user) == [(f"Role of {user.id}", "mid", None, "active")]
