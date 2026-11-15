"""Fakes for the database layer: they let unit tests run without any database."""

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from psycopg import OperationalError
from psycopg.pq import TransactionStatus
from psycopg_pool import PoolTimeout


class FakeCursor:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row


class FakeConnection:
    """Stands in for a psycopg connection and records what was executed on it."""

    def __init__(
        self,
        *,
        role: str = "skillloop_api",
        claims: str = "",
        claim_sub: str = "",
        status: TransactionStatus = TransactionStatus.IDLE,
        row_missing: bool = False,
    ) -> None:
        self.role = role
        self.claims = claims
        self.claim_sub = claim_sub
        self.row_missing = row_missing
        self.info = SimpleNamespace(transaction_status=status)
        self.executed: list[str] = []
        self.closed = False

    def execute(self, sql: str) -> FakeCursor:
        self.executed.append(sql)
        if self.row_missing and sql.lower().startswith("select"):
            return FakeCursor(None)
        if sql.startswith("RESET"):
            return FakeCursor(None)
        if sql == "select current_user":
            return FakeCursor((self.role,))
        return FakeCursor((self.role, self.claims, self.claim_sub))

    def close(self) -> None:
        self.closed = True


class FakePool:
    """Stands in for ConnectionPool in the lifespan tests."""

    def __init__(self, *, available: int = 1) -> None:
        self.available = available
        self.opened_with: dict[str, Any] | None = None
        self.close_calls = 0

    def open(self, wait: bool = False, timeout: float = 30.0) -> None:
        self.opened_with = {"wait": wait, "timeout": timeout}

    def get_stats(self) -> dict[str, int]:
        return {"pool_available": self.available}

    def close(self, timeout: float = 5.0) -> None:
        self.close_calls += 1


class FakeUserConnection:
    """Connection double for user_transaction(): records every step in a shared event list."""

    def __init__(
        self,
        events: list[str],
        *,
        uid: Any = None,
        role: str = "authenticated",
        fail_on: str | None = None,
    ) -> None:
        self.events = events
        self.uid = uid
        self.role = role
        self.fail_on = fail_on  # "begin" | "commit" | "rollback" | "set_role"
        self.params: list[Any] = []
        self.sqls: list[str] = []

    def execute(self, sql: str, params: list[Any] | None = None) -> FakeCursor:
        self.sqls.append(sql)
        if self.fail_on == "set_role" and sql.startswith("SET LOCAL ROLE"):
            raise OperationalError("set role failed")
        self.events.append(sql.split("(")[0].strip() if "set_config" in sql else sql)
        if params is not None:
            self.params.append(params)
        if sql.startswith("select auth.uid()"):
            return FakeCursor((self.uid, self.role))
        return FakeCursor(None)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self.fail_on == "begin":
            raise OperationalError("begin failed")
        self.events.append("BEGIN")
        try:
            yield
        except BaseException:
            self.events.append("ROLLBACK")
            if self.fail_on == "rollback":
                raise OperationalError("rollback failed") from None
            raise
        if self.fail_on == "commit":
            raise OperationalError("commit failed")
        self.events.append("COMMIT")


class FakeUserPool:
    """Pool double whose connection() yields a FakeUserConnection."""

    def __init__(
        self, conn: FakeUserConnection | None = None, *, fail_acquire: bool = False
    ) -> None:
        self.events: list[str] = conn.events if conn else []
        self.conn = conn or FakeUserConnection(self.events)
        self.fail_acquire = fail_acquire

    @contextmanager
    def connection(self) -> Iterator[FakeUserConnection]:
        if self.fail_acquire:
            raise PoolTimeout("couldn't get a connection after 5.00 sec")
        self.events.append("pool.acquire")
        try:
            yield self.conn
        finally:
            self.events.append("pool.release")
