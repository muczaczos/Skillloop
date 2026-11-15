from typing import Any

import psycopg
import pytest
from psycopg.pq import TransactionStatus
from psycopg_pool import ConnectionPool

import app.db.pool as pool_module
from app.core.config import Settings
from app.db.pool import (
    ConnectionStateError,
    configure_connection,
    create_pool,
    reset_connection,
    wait_until_ready,
)
from tests.db_fakes import FakeConnection, FakePool
from tests.test_config import make_settings

# --- create_pool ---------------------------------------------------------------------------


class RecordingPool:
    """Replaces ConnectionPool to capture how create_pool builds it."""

    def __init__(self, conninfo: str, **kwargs: Any) -> None:
        self.conninfo = conninfo
        self.kwargs = kwargs

    check_connection = staticmethod(ConnectionPool.check_connection)


def test_create_pool_passes_the_agreed_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pool_module, "ConnectionPool", RecordingPool)
    settings = make_settings()

    pool = create_pool(settings)

    assert isinstance(pool, RecordingPool)
    assert pool.conninfo == settings.database_url.get_secret_value()
    kwargs = pool.kwargs
    assert kwargs["open"] is False
    assert kwargs["min_size"] == 1
    assert kwargs["max_size"] == 5
    assert kwargs["timeout"] == 5
    assert kwargs["max_lifetime"] == 1800
    assert kwargs["kwargs"] == {
        "autocommit": True,
        "prepare_threshold": None,
        "connect_timeout": 5,
        "application_name": "skillloop-api",
    }
    assert kwargs["configure"] is configure_connection
    assert kwargs["reset"] is reset_connection
    assert kwargs["check"] is ConnectionPool.check_connection


def test_create_pool_uses_the_settings_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pool_module, "ConnectionPool", RecordingPool)
    settings = make_settings(
        db_pool_min_size=2,
        db_pool_max_size=7,
        db_pool_timeout_seconds=1.5,
        db_connect_timeout_seconds=3,
    )

    kwargs = create_pool(settings).kwargs  # type: ignore[attr-defined]

    assert (kwargs["min_size"], kwargs["max_size"], kwargs["timeout"]) == (2, 7, 1.5)
    assert kwargs["kwargs"]["connect_timeout"] == 3


def test_real_pool_is_created_closed_without_connecting() -> None:
    # Accepts the constructor arguments of the installed psycopg_pool and makes no network
    # call: the host does not resolve and the pool would otherwise log connection errors.
    settings: Settings = make_settings(
        database_url="postgresql://skillloop_api.test:pw@db.invalid:5432/postgres"
    )

    pool = create_pool(settings)

    assert isinstance(pool, ConnectionPool)
    assert pool.closed
    assert (pool.min_size, pool.max_size, pool.timeout, pool.max_lifetime) == (1, 5, 5, 1800)


# --- configure_connection --------------------------------------------------------------------


def test_configure_accepts_the_api_role() -> None:
    conn = FakeConnection(role="skillloop_api")

    configure_connection(conn)  # type: ignore[arg-type]

    assert not conn.closed


@pytest.mark.parametrize("role", ["postgres", "authenticated", "service_role", "anon"])
def test_configure_rejects_and_closes_any_other_role(role: str) -> None:
    conn = FakeConnection(role=role)

    with pytest.raises(ConnectionStateError) as exc_info:
        configure_connection(conn)  # type: ignore[arg-type]

    assert conn.closed
    assert role not in str(exc_info.value)


# --- reset_connection -----------------------------------------------------------------------


def test_reset_resets_role_and_settings_in_that_order_and_then_verifies() -> None:
    conn = FakeConnection()

    reset_connection(conn)  # type: ignore[arg-type]

    assert conn.executed[0] == "RESET ROLE; RESET ALL"
    assert conn.executed[1].lower().startswith("select current_user")
    assert "request.jwt.claims" in conn.executed[1]
    assert "request.jwt.claim.sub" in conn.executed[1]
    assert not conn.closed


@pytest.mark.parametrize(
    "state",
    [
        {"role": "authenticated"},
        {"role": "postgres"},
        {"claims": '{"sub": "11111111-1111-1111-1111-111111111111"}'},
        {"claim_sub": "11111111-1111-1111-1111-111111111111"},
        {"status": TransactionStatus.INTRANS},
        {"status": TransactionStatus.INERROR},
        {"row_missing": True},
    ],
)
def test_reset_closes_a_connection_it_cannot_prove_clean(state: dict[str, Any]) -> None:
    conn = FakeConnection(**state)

    with pytest.raises(ConnectionStateError) as exc_info:
        reset_connection(conn)  # type: ignore[arg-type]

    assert conn.closed
    # The message never carries user identity or claims.
    assert "11111111" not in str(exc_info.value)


def test_connection_state_error_is_a_psycopg_error() -> None:
    # The pool only discards connections for errors it knows; this must be one of them.
    assert issubclass(ConnectionStateError, psycopg.Error)


# --- wait_until_ready -------------------------------------------------------------------------


def test_wait_until_ready_returns_true_when_a_connection_is_available() -> None:
    assert wait_until_ready(FakePool(available=1), timeout=1)  # type: ignore[arg-type]


def test_wait_until_ready_returns_false_and_does_not_close_the_pool() -> None:
    pool = FakePool(available=0)

    assert not wait_until_ready(pool, timeout=0.3)  # type: ignore[arg-type]
    assert pool.close_calls == 0  # the pool must stay open and keep reconnecting
