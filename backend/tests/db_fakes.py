"""Fakes for the database layer: they let unit tests run without any database."""

from types import SimpleNamespace
from typing import Any

from psycopg.pq import TransactionStatus


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
