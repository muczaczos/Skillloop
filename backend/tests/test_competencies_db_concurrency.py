"""Write synchronisation of `public.competencies` against a REAL local PostgreSQL (Stage 9.1b).

What is tested is the mechanism of design V3 (docs/DATABASE-DESIGN.md section 4.8, scenarios
T1-T7 in docs/IMPLEMENTATION-PLAN.md, Stage 9.1b): the statement trigger `competencies_lock_goal`
and the row trigger `competencies_enforce_goal_rules` take a `FOR NO KEY UPDATE` lock on the GOAL
row before any competency row is touched. Nothing in the application is under test here; the SQL
is plain, and the users' transactions are the real `user_transaction()` (role `authenticated`,
RLS, JWT claims). Needs TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL (see db_integration_helpers)
and the migration 20261123113335_create_competencies.sql applied to that local database.

How the tests are coordinated (no sleep() decides any ordering):
- `Session` owns one transaction on its own thread and executes the steps the test gives it, so a
  test can keep a transaction open (locks held), start a second one that has to wait, and then
  end the first one. Every session uses its own database connection.
- "B waits for A" is PROVEN, not assumed: the test polls pg_stat_activity / pg_blocking_pids on
  the administration connection until B waits for a lock and A is among the sessions it waits
  for (directly or through a chain of waiting sessions). The polling loop only paces the queries;
  its deadline turns a missing wait into a failing test.
- Operations that must start together wait on a threading.Barrier. A barrier does not order the
  statements, so those tests assert only what holds in EVERY interleaving. No test depends on
  the order in which PostgreSQL wakes waiting transactions.
- Every wait has a timeout, every session is ended in the `sessions` fixture (a stuck backend is
  terminated), and a deadlock (`40P01`) is always a failure.
- Fixtures: throwaway users (the `users` fixture deletes them, and by cascade all their goals
  and competencies). Nothing else in the database is read or changed.

Privileged writes (the administration connection) follow contract L2: the goal is locked first.
"""

import itertools
import queue
import random
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from app.db.session import user_transaction
from app.models.auth import AuthenticatedUser
from tests.db_integration_helpers import (
    TEST_DATABASE_ADMIN_URL,
    TEST_DATABASE_URL,
    assert_local,
    open_api_pool,
)

pytestmark = pytest.mark.skipif(
    not (TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL),
    reason="TEST_DATABASE_URL and TEST_DATABASE_ADMIN_URL are not both set",
)

# Seconds. Every wait in this module is bounded by it. It is below the 15 s statement_timeout
# of the skillloop_api role, so a lock wait that never ends fails here first.
TIMEOUT = 12.0

Outcome = tuple[Any, ...]
REFUSED_GOAL: Outcome = ("error", "23514", "competencies_goal_not_active")
REFUSED_LIMIT: Outcome = ("error", "23514", "competencies_active_limit")
REFUSED_NAME: Outcome = ("error", "23505", "competencies_active_name_unique")

INSERT_SQL = (
    "insert into public.competencies "
    "(career_goal_id, user_id, name, knowledge_type, importance, expected_level) "
)
LOCK_GOAL_SQL = "select 1 from public.career_goals where id = %s for no key update"


@pytest.fixture
def pool10() -> Iterator[ConnectionPool]:
    pool = open_api_pool(max_size=10, timeout=TIMEOUT)
    try:
        yield pool
    finally:
        pool.close()


# --- sessions: one transaction on its own thread -------------------------------------------


class _Rollback(Exception):
    """Raised on the session thread to end the transaction with ROLLBACK."""


_COMMIT = object()
_ROLLBACK = object()


@contextmanager
def admin_transaction() -> Iterator[psycopg.Connection]:
    """A transaction of the local `postgres` login on its own connection (privileged writer)."""
    assert TEST_DATABASE_ADMIN_URL
    assert_local(TEST_DATABASE_ADMIN_URL)
    with (
        psycopg.connect(TEST_DATABASE_ADMIN_URL, autocommit=True, connect_timeout=5) as conn,
        conn.transaction(),
    ):
        yield conn


def _terminate_backend(pid: int) -> None:
    assert TEST_DATABASE_ADMIN_URL
    assert_local(TEST_DATABASE_ADMIN_URL)
    with psycopg.connect(TEST_DATABASE_ADMIN_URL, autocommit=True, connect_timeout=5) as conn:
        conn.execute("select pg_terminate_backend(%s)", [pid])


def _complete(future: Future[Any], value: Any, failure: BaseException | None) -> None:
    if failure is not None:
        future.set_exception(failure)
    else:
        future.set_result(value)


class Session:
    """A transaction that stays open until commit() / rollback() (or `then_commit`)."""

    def __init__(self, open_transaction: Callable[[], AbstractContextManager[psycopg.Connection]]):
        self._open = open_transaction
        self._tasks: queue.Queue[Any] = queue.Queue()
        self._ready = threading.Event()
        self.pid = 0
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._main, daemon=True)
        self._thread.start()
        assert self._ready.wait(TIMEOUT), "the session did not start"
        if self.error is not None:
            raise self.error

    def _main(self) -> None:
        # The future of a `then_commit` step is completed only AFTER the COMMIT, so a test that
        # reads the result may read the data afterwards.
        deferred: tuple[Future[Any], Any, BaseException | None] | None = None
        try:
            with self._open() as conn:
                row = conn.execute("select pg_backend_pid()").fetchone()
                assert row
                self.pid = int(row[0])
                self._ready.set()
                while True:
                    item = self._tasks.get()
                    if item is _COMMIT:
                        return
                    if item is _ROLLBACK:
                        raise _Rollback
                    fn, future, then_commit = item
                    value: Any = None
                    failure: BaseException | None = None
                    try:
                        value = fn(conn)
                    except BaseException as exc:  # noqa: BLE001 - handed to the test thread
                        failure = exc
                    if then_commit:
                        deferred = (future, value, failure)
                        return
                    _complete(future, value, failure)
        except _Rollback:
            pass
        except BaseException as exc:  # noqa: BLE001 - reported by commit()/rollback()
            self.error = exc
        finally:
            self._ready.set()
            if deferred is not None:
                future, value, failure = deferred
                _complete(future, value, self.error or failure)

    def submit(
        self, fn: Callable[[psycopg.Connection], Any], *, then_commit: bool = False
    ) -> Future[Any]:
        future: Future[Any] = Future()
        self._tasks.put((fn, future, then_commit))
        return future

    def run(self, fn: Callable[[psycopg.Connection], Any]) -> Any:
        return self.submit(fn).result(timeout=TIMEOUT)

    def commit(self) -> None:
        self._end(_COMMIT)

    def rollback(self) -> None:
        self._end(_ROLLBACK)

    def _end(self, item: object) -> None:
        if self._thread.is_alive():
            self._tasks.put(item)
        self._thread.join(TIMEOUT)
        assert not self._thread.is_alive(), "the session did not finish"
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        """Teardown: end the transaction no matter what; never raises."""
        if self._thread.is_alive():
            self._tasks.put(_ROLLBACK)
            self._thread.join(3)
            if self._thread.is_alive() and self.pid:
                _terminate_backend(self.pid)
                self._thread.join(TIMEOUT)


class Sessions:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool
        self._opened: list[Session] = []

    def user(self, user: AuthenticatedUser) -> Session:
        session = Session(lambda: user_transaction(self._pool, user))
        self._opened.append(session)
        return session

    def admin(self) -> Session:
        session = Session(admin_transaction)
        self._opened.append(session)
        return session

    def close_all(self) -> None:
        for session in reversed(self._opened):
            session.close()
        self._opened.clear()


@pytest.fixture
def sessions(pool10: ConnectionPool, users: list[AuthenticatedUser]) -> Iterator[Sessions]:
    """Depends on `users`, so every session is ended BEFORE the users are deleted."""
    factory = Sessions(pool10)
    try:
        yield factory
    finally:
        factory.close_all()


# --- proof that one session waits for another ------------------------------------------------

_BLOCKED_SQL = """
with recursive chain(pid, depth) as (
  select unnest(pg_blocking_pids(%(w)s::int)), 1
  union
  select unnest(pg_blocking_pids(c.pid)), c.depth + 1 from chain c where c.depth < 6
)
select
  coalesce((select wait_event_type = 'Lock' from pg_stat_activity where pid = %(w)s::int), false),
  exists (select 1 from chain where pid = %(h)s::int),
  %(h)s::int = any (pg_blocking_pids(%(w)s::int))
"""


def wait_blocked(admin: psycopg.Connection, waiter: Session, holder: Session) -> bool:
    """Return once `waiter` waits for a lock and `holder` is among what it waits for.

    True when `holder` blocks `waiter` directly, False when only through another waiting session
    (two sessions queue for the same row lock: the second waits for the first, not for the
    holder). Fails the test when the wait never starts.
    """
    deadline = time.monotonic() + TIMEOUT
    pacing = threading.Event()  # never set: wait(0.02) is only a pause between queries
    while time.monotonic() < deadline:
        row = admin.execute(_BLOCKED_SQL, {"w": waiter.pid, "h": holder.pid}).fetchone()
        assert row
        if row[0] and row[1]:
            return bool(row[2])
        pacing.wait(0.02)
    pytest.fail("the second session never started waiting for the first session's lock")


# --- data helpers (administration connection, autocommit) ------------------------------------


@dataclass(frozen=True)
class Setup:
    user: AuthenticatedUser
    goal: UUID


def make_goal(admin: psycopg.Connection, user_id: UUID, status: str = "active") -> UUID:
    row = admin.execute(
        "insert into public.career_goals (user_id, target_role, seniority, status) "
        "values (%s, 'Stage 9.1b', 'mid', %s) returning id",
        [user_id, status],
    ).fetchone()
    assert row
    return row[0]


def lock_goal(conn: psycopg.Connection, goal: UUID) -> None:
    conn.execute(LOCK_GOAL_SQL, [goal]).fetchall()


def fill(admin: psycopg.Connection, user_id: UUID, goal: UUID, names: Iterable[str]) -> None:
    """Privileged INSERT that follows contract L2: the goal is locked first."""
    with admin.transaction():
        lock_goal(admin, goal)
        for name in names:
            admin.execute(
                INSERT_SQL + "values (%s, %s, %s, 'memory', 'high', 'beginner')",
                [goal, user_id, name],
            )


def reset_to(admin: psycopg.Connection, user_id: UUID, goal: UUID, count: int) -> None:
    """Replace all competencies of the goal by `count` active ones (L2: goal locked first)."""
    with admin.transaction():
        lock_goal(admin, goal)
        admin.execute("delete from public.competencies where career_goal_id = %s", [goal])
        admin.execute(
            INSERT_SQL + "select %s, %s, 'f' || g, 'memory', 'high', 'beginner' "
            "from generate_series(1, %s) g",
            [goal, user_id, count],
        )


def prepare(
    admin: psycopg.Connection, user: AuthenticatedUser, names: Iterable[str] = ("X", "Y")
) -> Setup:
    goal = make_goal(admin, user.id)
    fill(admin, user.id, goal, names)
    return Setup(user, goal)


def state(admin: psycopg.Connection, goal: UUID) -> tuple[str | None, list[tuple[Any, ...]]]:
    """(goal status or None when the goal does not exist, [(name, status, description)])."""
    row = admin.execute("select status from public.career_goals where id = %s", [goal]).fetchone()
    rows = admin.execute(
        "select name, status, description from public.competencies "
        "where career_goal_id = %s order by name, status",
        [goal],
    ).fetchall()
    return (row[0] if row else None, rows)


def frozen_rows(conn: psycopg.Connection, goal: UUID) -> list[tuple[Any, ...]]:
    """Every row of the goal with its row version (xmin): any later write changes this."""
    return conn.execute(
        "select id, xmin::text, name, status, description from public.competencies "
        "where career_goal_id = %s order by id",
        [goal],
    ).fetchall()


def active_count(admin: psycopg.Connection, goal: UUID) -> int:
    row = admin.execute(
        "select count(*) from public.competencies where career_goal_id = %s and status = 'active'",
        [goal],
    ).fetchone()
    assert row
    return int(row[0])


# --- operations of a user (plain SQL inside the user's transaction) ----------------------------


def insert_rows(conn: psycopg.Connection, setup: Setup, names: list[str]) -> int:
    values = ", ".join(["(%s, %s, %s, 'memory', 'high', 'beginner')"] * len(names))
    params = [value for name in names for value in (setup.goal, setup.user.id, name)]
    return conn.execute(INSERT_SQL + "values " + values, params).rowcount


def op_insert(conn: psycopg.Connection, s: Setup) -> int:
    return insert_rows(conn, s, ["N1"])


def op_insert_dup(conn: psycopg.Connection, s: Setup) -> int:
    return insert_rows(conn, s, ["n1"])


def op_multi_insert(conn: psycopg.Connection, s: Setup) -> int:
    return insert_rows(conn, s, ["M1", "M2"])


def op_edit(conn: psycopg.Connection, s: Setup) -> int:
    return conn.execute(
        "update public.competencies set description = 'edited' "
        "where career_goal_id = %s and name = 'X'",
        [s.goal],
    ).rowcount


def op_archive(conn: psycopg.Connection, s: Setup) -> int:
    return conn.execute(
        "update public.competencies set status = 'archived' "
        "where career_goal_id = %s and name = 'X'",
        [s.goal],
    ).rowcount


def op_multi_update(conn: psycopg.Connection, s: Setup) -> int:
    return conn.execute(
        "update public.competencies set description = 'multi' "
        "where career_goal_id = %s and name in ('X', 'Y')",
        [s.goal],
    ).rowcount


def op_goal_archive(conn: psycopg.Connection, s: Setup) -> int:
    return conn.execute(
        "update public.career_goals set status = 'archived' where id = %s", [s.goal]
    ).rowcount


def op_zero_row_update(conn: psycopg.Connection, s: Setup) -> int:
    return conn.execute("update public.competencies set description = 'x' where false").rowcount


def op_zero_row_insert(conn: psycopg.Connection, s: Setup) -> int:
    return conn.execute(
        INSERT_SQL + "select %s, %s, 'none', 'memory', 'high', 'beginner' where false",
        [s.goal, s.user.id],
    ).rowcount


OPS: dict[str, Callable[[psycopg.Connection, Setup], int]] = {
    "insert": op_insert,
    "insert_dup": op_insert_dup,
    "multi_insert": op_multi_insert,
    "edit": op_edit,
    "archive": op_archive,
    "multi_update": op_multi_update,
    "goal_archive": op_goal_archive,
}
COMPETENCY_WRITES = ["insert", "multi_insert", "edit", "archive", "multi_update"]
UPDATE_OPS = {"edit", "archive", "multi_update"}


def outcomed(
    op: Callable[[psycopg.Connection, Setup], int], setup: Setup
) -> Callable[[psycopg.Connection], Outcome]:
    """Run `op` and return ("rows", n) or ("error", sqlstate, constraint) instead of raising.

    No savepoint: after an error the transaction is aborted but its locks stay until the
    session ends, exactly like in a request that fails.
    """

    def run(conn: psycopg.Connection) -> Outcome:
        try:
            return ("rows", op(conn, setup))
        except psycopg.errors.Error as exc:
            return ("error", exc.sqlstate, exc.diag.constraint_name or "")

    return run


def effect_visible(kind: str, rows: list[tuple[Any, ...]]) -> bool:
    """True when the rows show that operation `kind` was applied."""
    if kind == "insert":
        return ("N1", "active", None) in rows
    if kind == "multi_insert":
        return ("M1", "active", None) in rows and ("M2", "active", None) in rows
    if kind == "edit":
        return ("X", "active", "edited") in rows
    if kind == "archive":
        return ("X", "archived", None) in rows
    if kind == "multi_update":
        return ("X", "active", "multi") in rows and ("Y", "active", "multi") in rows
    raise AssertionError(kind)


def try_lock_nowait(conn: psycopg.Connection, goal: UUID, mode: str) -> str:
    """'ok' when the row lock could be taken at once, else the SQLSTATE (55P03)."""
    clauses = {
        "share": "for share nowait",
        "key share": "for key share nowait",
        "no key update": "for no key update nowait",
        "update": "for update nowait",
    }
    try:
        with conn.transaction():  # a savepoint: the session stays usable after a failure
            conn.execute(
                "select 1 from public.career_goals where id = %s " + clauses[mode], [goal]
            ).fetchall()
    except psycopg.errors.LockNotAvailable as exc:
        return str(exc.sqlstate)
    return "ok"


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


def assert_no_deadlock(*results: Any) -> None:
    for result in results:
        assert not isinstance(result, psycopg.errors.DeadlockDetected), "deadlock (40P01)"
        if isinstance(result, tuple) and len(result) > 1 and result[0] == "error":
            assert result[1] != "40P01", "deadlock (40P01)"


# --- C0: the sessions use READ COMMITTED ---------------------------------------------------------


def test_c0_sessions_use_read_committed(sessions: Sessions, users: list[AuthenticatedUser]) -> None:
    def isolation(conn: psycopg.Connection) -> str:
        row = conn.execute("show transaction_isolation").fetchone()
        assert row
        return str(row[0])

    assert sessions.user(users[0]).run(isolation) == "read committed"
    assert sessions.admin().run(isolation) == "read committed"


# --- L1: the real strength of the goal lock (FOR NO KEY UPDATE) ----------------------------------


@pytest.mark.parametrize("kind", ["insert", "edit", "zero_row_update", "zero_row_insert"])
def test_l1_goal_lock_is_for_no_key_update(
    kind: str, sessions: Sessions, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    """A holds a competency write open; B (another connection) probes the goal row with NOWAIT.

    FOR SHARE, FOR NO KEY UPDATE and FOR UPDATE conflict with a NO KEY UPDATE lock and must
    fail with 55P03; FOR KEY SHARE does not conflict and must succeed (this is what the
    foreign-key checks of other writers need). The zero-row statements show that the statement
    trigger alone takes the lock. xmax is not used.
    """
    setup = prepare(admin, users[0])
    ops = {
        "insert": op_insert,
        "edit": op_edit,
        "zero_row_update": op_zero_row_update,
        "zero_row_insert": op_zero_row_insert,
    }
    writer = sessions.user(users[0])
    probe = sessions.admin()

    assert writer.run(outcomed(ops[kind], setup))[0] == "rows"

    assert probe.run(lambda c: try_lock_nowait(c, setup.goal, "share")) == "55P03"
    assert probe.run(lambda c: try_lock_nowait(c, setup.goal, "no key update")) == "55P03"
    assert probe.run(lambda c: try_lock_nowait(c, setup.goal, "update")) == "55P03"
    assert probe.run(lambda c: try_lock_nowait(c, setup.goal, "key share")) == "ok"

    writer.commit()  # the lock is released with the transaction
    assert probe.run(lambda c: try_lock_nowait(c, setup.goal, "share")) == "ok"
    probe.rollback()


# --- T1: the suspected cycle ---------------------------------------------------------------------


def test_t1_colliding_insert_does_not_wait_for_the_blocked_edit(
    sessions: Sessions, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    """H holds the goal; A edits R and waits for the goal BEFORE touching R; H inserts the name
    of R and gets 23505 at once (it would wait for A if A held R). Then A succeeds."""
    setup = prepare(admin, users[0], names=["R"])
    holder, editor = sessions.user(users[0]), sessions.user(users[0])
    holder.run(lambda c: lock_goal(c, setup.goal))

    edit = editor.submit(outcomed(op_edit_r, setup), then_commit=True)
    wait_blocked(admin, editor, holder)

    collision = holder.run(outcomed(lambda c, s: insert_rows(c, s, ["R"]), setup))
    assert collision == REFUSED_NAME  # at once: H did not wait for A
    # The failed statement aborted H's transaction, which released the goal lock: A may already
    # be running, so nothing is asserted about `edit.done()` here.
    holder.rollback()

    result = edit.result(timeout=TIMEOUT)
    assert_no_deadlock(collision, result)
    assert result == ("rows", 1)
    assert state(admin, setup.goal) == ("active", [("R", "active", "edited")])


def op_edit_r(conn: psycopg.Connection, s: Setup) -> int:
    return conn.execute(
        "update public.competencies set description = 'edited' "
        "where career_goal_id = %s and name = 'R'",
        [s.goal],
    ).rowcount


def test_t1b_other_name_insert_then_edit_waits_and_succeeds(
    sessions: Sessions, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    setup = prepare(admin, users[0], names=["R"])
    holder, editor = sessions.user(users[0]), sessions.user(users[0])
    holder.run(lambda c: lock_goal(c, setup.goal))

    edit = editor.submit(outcomed(op_edit_r, setup), then_commit=True)
    wait_blocked(admin, editor, holder)

    assert holder.run(outcomed(lambda c, s: insert_rows(c, s, ["R2"]), setup)) == ("rows", 1)
    assert not edit.done()
    holder.commit()

    result = edit.result(timeout=TIMEOUT)
    assert_no_deadlock(result)
    assert result == ("rows", 1)
    assert state(admin, setup.goal) == (
        "active",
        [("R", "active", "edited"), ("R2", "active", None)],
    )


# --- T2: two edits that would swap names ---------------------------------------------------------


def test_t2_swap_of_names_is_refused_without_deadlock(
    sessions: Sessions, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    setup = prepare(admin, users[0], names=["a", "b", "c"])

    def rename(old: str, new: str) -> Callable[[psycopg.Connection, Setup], int]:
        def op(conn: psycopg.Connection, s: Setup) -> int:
            return conn.execute(
                "update public.competencies set name = %s where career_goal_id = %s and name = %s",
                [new, s.goal, old],
            ).rowcount

        return op

    first, second = sessions.user(users[0]), sessions.user(users[0])
    assert first.run(outcomed(rename("a", "z"), setup)) == ("rows", 1)  # holds goal and row a

    later = second.submit(outcomed(rename("b", "a"), setup), then_commit=True)
    wait_blocked(admin, second, first)  # T2 waits for the goal lock

    clash = first.run(outcomed(rename("c", "b"), setup))
    assert clash == REFUSED_NAME  # `b` still exists: T2 has not touched it
    # The failed statement aborted T1's transaction (its rename of `a` is undone and the goal
    # lock released), so T2 may already be running: nothing is asserted about `later.done()`.
    first.rollback()

    result = later.result(timeout=TIMEOUT)
    assert_no_deadlock(clash, result)
    assert result == REFUSED_NAME  # `a` exists again
    assert state(admin, setup.goal) == (
        "active",
        [("a", "active", None), ("b", "active", None), ("c", "active", None)],
    )


# --- T3: archiving the goal against writes, both orders --------------------------------------------


@pytest.mark.parametrize("kind", COMPETENCY_WRITES)
def test_t3_goal_archiving_held_then_write_is_refused(
    kind: str, sessions: Sessions, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    setup = prepare(admin, users[0])
    archiver, writer = sessions.user(users[0]), sessions.user(users[0])
    assert archiver.run(outcomed(op_goal_archive, setup)) == ("rows", 1)

    write = writer.submit(outcomed(OPS[kind], setup), then_commit=True)
    wait_blocked(admin, writer, archiver)
    assert not write.done()
    archiver.commit()

    result = write.result(timeout=TIMEOUT)
    assert_no_deadlock(result)
    # The statement was planned while the goal still looked active; the row trigger decides
    # again after the wait and refuses.
    assert result == REFUSED_GOAL
    assert state(admin, setup.goal) == (
        "archived",
        [("X", "active", None), ("Y", "active", None)],
    )


@pytest.mark.parametrize("kind", COMPETENCY_WRITES)
def test_t3_write_held_then_goal_archiving_waits_and_no_write_is_possible_afterwards(
    kind: str, sessions: Sessions, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    setup = prepare(admin, users[0])
    writer, archiver = sessions.user(users[0]), sessions.user(users[0])
    first = writer.run(outcomed(OPS[kind], setup))
    assert first[0] == "rows" and first[1] >= 1

    archive = archiver.submit(outcomed(op_goal_archive, setup), then_commit=True)
    wait_blocked(admin, archiver, writer)
    assert not archive.done()
    writer.commit()

    result = archive.result(timeout=TIMEOUT)
    assert_no_deadlock(first, result)
    assert result == ("rows", 1)
    goal_status, rows = state(admin, setup.goal)
    assert goal_status == "archived"
    assert effect_visible(kind, rows)  # the write that came first is kept

    # Afterwards no write is possible.
    after = sessions.user(users[0])
    assert after.run(outcomed(op_insert, setup)) == REFUSED_GOAL
    after.rollback()
    for later_op in (op_multi_insert,):
        again = sessions.user(users[0])
        assert again.run(outcomed(later_op, setup)) == REFUSED_GOAL
        again.rollback()
    before = state(admin, setup.goal)
    for later_op in (op_edit, op_archive, op_multi_update):
        again = sessions.user(users[0])
        assert again.run(outcomed(later_op, setup)) == ("rows", 0)
        again.commit()
    assert state(admin, setup.goal) == before


# --- T4: multi-row UPDATE against a single-row edit, no assumption about wake-up order -----------------


def test_t4_multi_row_update_and_single_row_edit_behind_one_holder(
    sessions: Sessions, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    """Z (updates R1-R3) and X (edits R2) are both started while H holds the goal. Whichever
    PostgreSQL wakes first, both must finish without a deadlock and the data must equal the
    result of Z-then-X or of X-then-Z. 24 rounds, both start orders."""
    user = users[0]
    setup = prepare(admin, user, names=["R1", "R2", "R3"])
    direct: Counter[bool] = Counter()

    def multi(conn: psycopg.Connection, s: Setup) -> int:
        return conn.execute(
            "update public.competencies set description = coalesce(description, '') || 'Z' "
            "where career_goal_id = %s and name in ('R1', 'R2', 'R3')",
            [s.goal],
        ).rowcount

    def single(conn: psycopg.Connection, s: Setup) -> int:
        return conn.execute(
            "update public.competencies set description = coalesce(description, '') || 'X' "
            "where career_goal_id = %s and name = 'R2'",
            [s.goal],
        ).rowcount

    for round_number in range(24):
        if round_number:
            with admin.transaction():  # privileged write: the goal is locked first (L2)
                lock_goal(admin, setup.goal)
                admin.execute(
                    "update public.competencies set description = null where career_goal_id = %s",
                    [setup.goal],
                )
        holder, z_session, x_session = (sessions.user(user) for _ in range(3))
        holder.run(lambda c: lock_goal(c, setup.goal))

        order = [(z_session, multi), (x_session, single)]
        if round_number % 2:
            order.reverse()
        futures = []
        for session, op in order:
            futures.append(session.submit(outcomed(op, setup), then_commit=True))
            direct[wait_blocked(admin, session, holder)] += 1
        assert not any(f.done() for f in futures)
        holder.commit()

        results = [f.result(timeout=TIMEOUT) for f in futures]
        assert_no_deadlock(*results)
        assert all(r[0] == "rows" for r in results), results
        rows = {name: description for name, _, description in state(admin, setup.goal)[1]}
        assert rows["R1"] == "Z" and rows["R3"] == "Z", rows
        assert rows["R2"] in {"ZX", "XZ"}, rows  # either serial order is accepted
    assert sum(direct.values()) == 48  # every waiter was proven to be blocked behind H


# --- T5a / T5b: deletion that follows L2, and the real cascade --------------------------------------------


def test_t5a_explicit_delete_after_locking_the_goal(
    sessions: Sessions, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    setup = prepare(admin, users[0], names=["X", "Y", "Z"])
    helper = sessions.admin()
    helper.run(lambda c: lock_goal(c, setup.goal))  # L2: the goal first
    deleted = helper.run(
        lambda c: (
            c.execute(
                "delete from public.competencies where career_goal_id = %s", [setup.goal]
            ).rowcount
        )
    )
    assert deleted == 3

    editor = sessions.user(users[0])
    edit = editor.submit(outcomed(op_edit, setup), then_commit=True)
    wait_blocked(admin, editor, helper)  # U is blocked before it touches any competency row
    assert not edit.done()
    helper.commit()

    result = edit.result(timeout=TIMEOUT)
    assert_no_deadlock(result)
    assert result == ("rows", 0)  # the competency is gone
    assert state(admin, setup.goal) == ("active", [])


def _delete_goal(conn: psycopg.Connection, goal: UUID) -> int:
    """A real DELETE of the goal; PostgreSQL removes the competencies by ON DELETE CASCADE."""
    return conn.execute("delete from public.career_goals where id = %s", [goal]).rowcount


def test_t5b_cascade_part1_delete_first_then_edit_waits(
    sessions: Sessions, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    setup = prepare(admin, users[0], names=["X", "Y"])
    deleter = sessions.admin()
    assert deleter.run(lambda c: _delete_goal(c, setup.goal)) == 1  # the cascade has run

    editor = sessions.user(users[0])
    edit = editor.submit(outcomed(op_edit, setup), then_commit=True)
    wait_blocked(admin, editor, deleter)
    assert not edit.done()
    deleter.commit()

    result = edit.result(timeout=TIMEOUT)
    assert_no_deadlock(result)
    assert result == ("rows", 0)
    assert state(admin, setup.goal) == (None, [])


def test_t5b_cascade_part2_edit_first_then_delete_waits(
    sessions: Sessions, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    setup = prepare(admin, users[0], names=["X", "Y"])
    editor = sessions.user(users[0])
    assert editor.run(outcomed(op_edit, setup)) == ("rows", 1)

    deleter = sessions.admin()
    delete = deleter.submit(lambda c: _delete_goal(c, setup.goal), then_commit=True)
    wait_blocked(admin, deleter, editor)
    assert not delete.done()
    editor.commit()

    assert delete.result(timeout=TIMEOUT) == 1
    # The cascade also removed the competency that the editor changed.
    assert state(admin, setup.goal) == (None, [])


def test_t5b_cascade_part3_simultaneous_best_effort(
    pool10: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    """Best effort at the moment between the deletion of the goal row and the cascade: the
    interval is not required to be hit; both 0 and 1 changed rows are accepted."""
    user = users[0]
    seen: Counter[int] = Counter()
    for _ in range(15):
        setup = prepare(admin, user, names=["X", "Y"])

        def edit(setup: Setup = setup) -> int:
            with user_transaction(pool10, user) as conn:
                return op_edit(conn, setup)

        def delete(setup: Setup = setup) -> int:
            with admin_transaction() as conn:
                return _delete_goal(conn, setup.goal)

        edited, deleted = run_in_parallel(edit, delete)
        assert_no_deadlock(edited, deleted)
        assert deleted == 1, deleted
        assert isinstance(edited, int) and edited in (0, 1), edited
        seen[edited] += 1
        assert state(admin, setup.goal) == (None, [])
    assert sum(seen.values()) == 15


# --- T6: pairs of operations, both orders; the limit ------------------------------------------------


def comparable(first: str, second: str, outcome: Outcome) -> Outcome:
    """After the goal was archived, a waiting UPDATE is refused by the row trigger while a
    later one changes zero rows: both mean "refused", the same final data."""
    if first == "goal_archive" and second in UPDATE_OPS and outcome == REFUSED_GOAL:
        return ("rows", 0)
    return outcome


@pytest.mark.parametrize(
    ("first", "second"), list(itertools.permutations(OPS, 2)), ids=lambda value: str(value)
)
def test_t6_pair_waits_and_equals_the_serial_result(
    first: str,
    second: str,
    sessions: Sessions,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    concurrent = prepare(admin, users[0])
    serial = prepare(admin, users[1])

    # Concurrent: `first` is held open, `second` has to wait for it.
    holder, waiter = sessions.user(users[0]), sessions.user(users[0])
    first_outcome = holder.run(outcomed(OPS[first], concurrent))
    pending = waiter.submit(outcomed(OPS[second], concurrent), then_commit=True)
    wait_blocked(admin, waiter, holder)
    assert not pending.done()
    holder.commit()
    second_outcome = pending.result(timeout=TIMEOUT)

    # Serial reference: the same two operations one after the other, each committed.
    one, two = sessions.user(users[1]), sessions.user(users[1])
    serial_first = one.run(outcomed(OPS[first], serial))
    one.commit()
    serial_second = two.run(outcomed(OPS[second], serial))
    two.commit()

    assert_no_deadlock(first_outcome, second_outcome)
    assert first_outcome == serial_first
    assert comparable(first, second, second_outcome) == serial_second
    assert state(admin, concurrent.goal) == state(admin, serial.goal)


@pytest.mark.parametrize(("filled", "threads", "wins"), [(49, 5, 1), (48, 4, 2), (45, 5, 5)])
def test_t6_limit_parallel_inserts_never_exceed_50(
    filled: int,
    threads: int,
    wins: int,
    pool10: ConnectionPool,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    """Several transactions try to take the last free slots at the same moment (barrier)."""
    user = users[0]
    goal = make_goal(admin, user.id)
    setup = Setup(user, goal)
    for round_number in range(8):
        reset_to(admin, user.id, goal, filled)

        def insert(index: int, round_number: int = round_number) -> int:
            with user_transaction(pool10, user) as conn:
                return insert_rows(conn, setup, [f"new{round_number}-{index}"])

        results = run_in_parallel(*[lambda i=i: insert(i) for i in range(threads)])

        assert_no_deadlock(*results)
        won = [r for r in results if r == 1]
        refused = [r for r in results if isinstance(r, psycopg.errors.CheckViolation)]
        assert len(won) == wins and len(won) + len(refused) == threads, results
        assert all(r.diag.constraint_name == "competencies_active_limit" for r in refused)
        assert active_count(admin, goal) == min(filled + threads, 50)


@pytest.mark.parametrize("first_ends", ["commit", "rollback"])
def test_t6_limit_held_50th_insert_then_second_insert(
    first_ends: str, sessions: Sessions, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    user = users[0]
    goal = make_goal(admin, user.id)
    setup = Setup(user, goal)
    reset_to(admin, user.id, goal, 49)

    holder, waiter = sessions.user(user), sessions.user(user)
    held = lambda c, s: insert_rows(c, s, ["held50"])
    late = lambda c, s: insert_rows(c, s, ["late"])
    assert holder.run(outcomed(held, setup)) == ("rows", 1)
    pending = waiter.submit(outcomed(late, setup), then_commit=True)
    wait_blocked(admin, waiter, holder)
    assert not pending.done()
    getattr(holder, first_ends)()

    result = pending.result(timeout=TIMEOUT)
    assert_no_deadlock(result)
    names = {name for name, _, _ in state(admin, goal)[1]}
    if first_ends == "commit":
        assert result == REFUSED_LIMIT
        assert "held50" in names and "late" not in names
    else:
        assert result == ("rows", 1)
        assert "late" in names and "held50" not in names
    assert active_count(admin, goal) == 50


@pytest.mark.parametrize("archiving_ends", ["commit", "rollback"])
def test_t6_limit_archiving_one_of_50_while_an_insert_waits(
    archiving_ends: str,
    sessions: Sessions,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    user = users[0]
    goal = make_goal(admin, user.id)
    setup = Setup(user, goal)
    reset_to(admin, user.id, goal, 50)

    def archive_first(conn: psycopg.Connection, s: Setup) -> int:
        return conn.execute(
            "update public.competencies set status = 'archived' "
            "where career_goal_id = %s and name = 'f1'",
            [s.goal],
        ).rowcount

    archiver, inserter = sessions.user(user), sessions.user(user)
    assert archiver.run(outcomed(archive_first, setup)) == ("rows", 1)
    pending = inserter.submit(outcomed(op_insert, setup), then_commit=True)
    wait_blocked(admin, inserter, archiver)
    assert not pending.done()
    getattr(archiver, archiving_ends)()

    result = pending.result(timeout=TIMEOUT)
    assert_no_deadlock(result)
    if archiving_ends == "commit":
        assert result == ("rows", 1)  # the freed slot is used
        assert active_count(admin, goal) == 50
        assert ("N1", "active", None) in state(admin, goal)[1]
    else:
        assert result == REFUSED_LIMIT  # nothing was freed
        assert active_count(admin, goal) == 50
        assert ("N1", "active", None) not in state(admin, goal)[1]


# --- T7: bounded stress ----------------------------------------------------------------------------------


def test_t7_bounded_stress_keeps_the_invariants(
    pool10: ConnectionPool, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    """Six threads mix INSERT, multi-row INSERT, edit, rename, archive and multi-row UPDATE of
    competencies for a few seconds; one transaction archives the goal about 60 % of the way.
    Seeded random choices; the assertions hold for every interleaving."""
    user = users[0]
    setup = prepare(admin, user, names=[f"s{i}" for i in range(1, 41)])
    names = [f"s{i}" for i in range(1, 71)]
    run_seconds, workers = 4.0, 6

    log: list[tuple[str, float, Outcome]] = []  # list.append is atomic
    unexpected: list[BaseException] = []
    archived_done: list[float] = []
    frozen: list[list[tuple[Any, ...]]] = []  # rows right after the archiving committed
    max_active: list[int] = []
    stop = threading.Event()
    start_barrier = threading.Barrier(workers + 2)

    def operation(kind: str, rng: random.Random, conn: psycopg.Connection) -> int:
        one, two = rng.sample(names, 2)
        if kind == "insert":
            return insert_rows(conn, setup, [one])
        if kind == "multi_insert":
            return insert_rows(conn, setup, [one, two])
        if kind == "edit":
            return conn.execute(
                "update public.competencies set description = 'e' "
                "where career_goal_id = %s and name = %s",
                [setup.goal, one],
            ).rowcount
        if kind == "rename":
            return conn.execute(
                "update public.competencies set name = %s where career_goal_id = %s and name = %s",
                [two, setup.goal, one],
            ).rowcount
        if kind == "archive":
            return conn.execute(
                "update public.competencies set status = 'archived' "
                "where career_goal_id = %s and name = %s",
                [setup.goal, one],
            ).rowcount
        return conn.execute(
            "update public.competencies set description = 'm' "
            "where career_goal_id = %s and name in (%s, %s)",
            [setup.goal, one, two],
        ).rowcount

    kinds = ["insert", "insert", "multi_insert", "edit", "rename", "archive", "multi_update"]

    def worker(seed: int) -> None:
        rng = random.Random(seed)
        start_barrier.wait(TIMEOUT)
        deadline = time.monotonic() + run_seconds
        while time.monotonic() < deadline:
            kind = rng.choice(kinds)
            started = time.monotonic()
            try:
                with user_transaction(pool10, user) as conn:
                    result: Outcome = ("rows", operation(kind, rng, conn))
            except psycopg.errors.Error as exc:
                result = ("error", exc.sqlstate, exc.diag.constraint_name or "")
            except BaseException as exc:  # noqa: BLE001 - reported below
                unexpected.append(exc)
                return
            log.append((kind, started, result))

    def archiver() -> None:
        start_barrier.wait(TIMEOUT)
        threading.Event().wait(run_seconds * 0.6)  # pacing only
        try:
            with user_transaction(pool10, user) as conn:
                assert op_goal_archive(conn, setup) == 1
            archived_done.append(time.monotonic())
            # Committed: from now on the map of this goal must never change again.
            with psycopg.connect(TEST_DATABASE_ADMIN_URL, autocommit=True) as snapshot_conn:
                frozen.append(frozen_rows(snapshot_conn, setup.goal))
        except BaseException as exc:  # noqa: BLE001 - reported below
            unexpected.append(exc)

    def sampler() -> None:
        assert TEST_DATABASE_ADMIN_URL
        start_barrier.wait(TIMEOUT)
        with psycopg.connect(TEST_DATABASE_ADMIN_URL, autocommit=True, connect_timeout=5) as conn:
            while not stop.is_set():
                row = conn.execute(
                    "select count(*) from public.competencies "
                    "where career_goal_id = %s and status = 'active'",
                    [setup.goal],
                ).fetchone()
                if row is None:
                    unexpected.append(AssertionError("count returned no row"))
                    return
                max_active.append(int(row[0]))
                stop.wait(0.05)

    threads = [
        threading.Thread(target=worker, args=(seed,), daemon=True) for seed in range(workers)
    ]
    threads += [threading.Thread(target=archiver, daemon=True)]
    sample_thread = threading.Thread(target=sampler, daemon=True)
    for thread in [*threads, sample_thread]:
        thread.start()
    for thread in threads:
        thread.join(run_seconds + 2 * TIMEOUT)
        assert not thread.is_alive(), "a stress thread did not finish"
    stop.set()
    sample_thread.join(TIMEOUT)

    assert not unexpected, unexpected
    allowed = {
        ("23505", "competencies_active_name_unique"),
        ("23514", "competencies_active_limit"),
        ("23514", "competencies_goal_not_active"),
    }
    errors = Counter((r[1], r[2]) for _, _, r in log if r[0] == "error")
    assert set(errors) <= allowed, errors  # in particular no 40P01
    assert len(log) >= 20, f"the stress run did too little work: {len(log)} operations"
    assert archived_done, "the goal was not archived"

    # Invariants of the committed data.
    assert max_active and max(max_active) <= 50, max(max_active, default=None)
    goal_status, _ = state(admin, setup.goal)
    assert goal_status == "archived"
    assert active_count(admin, setup.goal) <= 50
    duplicates = admin.execute(
        "select lower(btrim(name)), count(*) from public.competencies "
        "where career_goal_id = %s and status = 'active' "
        "group by 1 having count(*) > 1",
        [setup.goal],
    ).fetchall()
    assert duplicates == []
    # No operation that STARTED after the archiving had committed changed anything ...
    late = [
        (k, r)
        for k, started, r in log
        if started > archived_done[0] and r[0] == "rows" and r[1] > 0
    ]
    assert late == [], late
    # ... and the map right after the archiving committed equals the final map: no row was
    # inserted, changed or archived afterwards. xmin changes with every new row version, so a
    # write that stores the same values is detected too. (Database timestamps are not used: the
    # clock of the local database container is not reliable under load, see the report.)
    assert frozen, "the snapshot after the archiving is missing"
    assert frozen_rows(admin, setup.goal) == frozen[0]


# --- D5: multi-row INSERT spanning a usable and an unusable goal -------------------------------------


@pytest.mark.parametrize("bad_first", [True, False], ids=["bad-row-first", "bad-row-last"])
@pytest.mark.parametrize("bad_kind", ["own_archived_goal", "foreign_active_goal"])
def test_d5_multi_row_insert_across_goals_is_atomic(
    bad_kind: str,
    bad_first: bool,
    sessions: Sessions,
    users: list[AuthenticatedUser],
    admin: psycopg.Connection,
) -> None:
    """One statement inserts a row into the user's active goal and a row into a goal that must
    refuse it. The whole statement fails and no row remains.

    A failed statement aborts the transaction and releases its locks, so the lock cannot be
    observed afterwards. It is observed BEFORE the failure instead: another session holds the
    active goal while the statement starts. With the bad row first, the row trigger would fail
    at once if nothing made the statement wait (it never needs the active goal for that row),
    so the proven wait comes from the STATEMENT trigger, which locks the active goal before any
    row is processed."""
    user, other = users[0], users[1]
    active_goal = make_goal(admin, user.id)
    own_archived = make_goal(admin, user.id, "archived")
    foreign_goal = make_goal(admin, other.id)
    bad_goal = own_archived if bad_kind == "own_archived_goal" else foreign_goal
    rows = [(bad_goal, "D5 bad"), (active_goal, "D5 ok")]
    if not bad_first:
        rows.reverse()

    def multi_goal_insert(conn: psycopg.Connection, s: Setup) -> int:
        values = ", ".join(["(%s, %s, %s, 'memory', 'high', 'beginner')"] * len(rows))
        params = [value for goal, name in rows for value in (goal, user.id, name)]
        return conn.execute(INSERT_SQL + "values " + values, params).rowcount

    holder, writer, probe = sessions.admin(), sessions.user(user), sessions.admin()
    holder.run(lambda c: lock_goal(c, active_goal))
    pending = writer.submit(outcomed(multi_goal_insert, Setup(user, active_goal)), then_commit=True)
    wait_blocked(admin, writer, holder)  # the statement waits for the ACTIVE goal first
    assert not pending.done()
    holder.commit()

    outcome = pending.result(timeout=TIMEOUT)
    assert_no_deadlock(outcome)
    assert outcome == REFUSED_GOAL
    # The failed transaction has ended and holds nothing.
    assert probe.run(lambda c: try_lock_nowait(c, active_goal, "share")) == "ok"
    probe.rollback()

    leftover = admin.execute(
        "select count(*) from public.competencies where name like 'D5 %%'"
    ).fetchone()
    assert leftover and leftover[0] == 0
    assert state(admin, active_goal) == ("active", [])
    assert state(admin, bad_goal)[1] == []


# --- L2: privileged roles ---------------------------------------------------------------------------


def test_l2_privileged_zero_row_statement_takes_no_goal_lock(
    sessions: Sessions, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    """Without auth.uid() the statement trigger does nothing: the same zero-row statement that
    locks the goal for a user (L1) leaves it free for a privileged role."""
    setup = prepare(admin, users[0])
    privileged, probe = sessions.admin(), sessions.admin()
    assert (
        privileged.run(
            lambda c: (
                c.execute("update public.competencies set description = 'x' where false").rowcount
            )
        )
        == 0
    )
    assert probe.run(lambda c: try_lock_nowait(c, setup.goal, "share")) == "ok"
    probe.rollback()
    privileged.rollback()


def test_l2_privileged_write_that_locks_the_goal_first(
    sessions: Sessions, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    """P follows the contract: lock the goal, then INSERT, UPDATE and DELETE competencies. A
    user's edit started meanwhile waits behind P (proven) and then succeeds; no deadlock."""
    setup = prepare(admin, users[0], names=["A", "B", "C"])
    privileged = sessions.admin()

    def privileged_writes(conn: psycopg.Connection) -> tuple[int, int, int]:
        lock_goal(conn, setup.goal)
        inserted = conn.execute(
            INSERT_SQL + "values (%s, %s, 'P1', 'memory', 'high', 'beginner')",
            [setup.goal, users[0].id],
        ).rowcount
        updated = conn.execute(
            "update public.competencies set description = 'p' "
            "where career_goal_id = %s and name = 'A'",
            [setup.goal],
        ).rowcount
        deleted = conn.execute(
            "delete from public.competencies where career_goal_id = %s and name = 'B'", [setup.goal]
        ).rowcount
        return inserted, updated, deleted

    assert privileged.run(privileged_writes) == (1, 1, 1)

    def edit_c(conn: psycopg.Connection, s: Setup) -> int:
        return conn.execute(
            "update public.competencies set description = 'u' "
            "where career_goal_id = %s and name = 'C'",
            [s.goal],
        ).rowcount

    editor = sessions.user(users[0])
    pending = editor.submit(outcomed(edit_c, setup), then_commit=True)
    wait_blocked(admin, editor, privileged)
    assert not pending.done()
    privileged.commit()

    result = pending.result(timeout=TIMEOUT)
    assert_no_deadlock(result)
    assert result == ("rows", 1)
    assert state(admin, setup.goal) == (
        "active",
        [("A", "active", "p"), ("C", "active", "u"), ("P1", "active", None)],
    )


def test_l2_privileged_insert_without_the_contract_still_waits_in_the_row_trigger(
    sessions: Sessions, users: list[AuthenticatedUser], admin: psycopg.Connection
) -> None:
    """Documents what is NOT guaranteed without the contract: the row trigger still locks the
    goal for an INSERT, so this INSERT waits for a user's open write and then succeeds. (For an
    UPDATE or DELETE the competency row would be locked before the goal; that is the cycle that
    rule L2 prevents, and it is deliberately not reproduced here.)"""
    setup = prepare(admin, users[0])
    editor, privileged = sessions.user(users[0]), sessions.admin()
    assert editor.run(outcomed(op_edit, setup)) == ("rows", 1)

    def unguarded_insert(conn: psycopg.Connection) -> int:
        return conn.execute(
            INSERT_SQL + "values (%s, %s, 'P2', 'memory', 'high', 'beginner')",
            [setup.goal, users[0].id],
        ).rowcount

    pending = privileged.submit(unguarded_insert, then_commit=True)
    wait_blocked(admin, privileged, editor)
    assert not pending.done()
    editor.commit()

    assert pending.result(timeout=TIMEOUT) == 1
    assert state(admin, setup.goal) == (
        "active",
        [("P2", "active", None), ("X", "active", "edited"), ("Y", "active", None)],
    )
