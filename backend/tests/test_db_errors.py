import logging
from uuid import UUID

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import errors as pg_errors
from psycopg_pool import PoolClosed, PoolTimeout

from app.db.errors import DatabaseUnavailableError, translate_db_errors
from app.main import app


def _raise(exc: BaseException) -> None:
    with translate_db_errors():
        raise exc


@pytest.mark.parametrize(
    "exc",
    [
        psycopg.OperationalError("could not connect to server: host=secret-host password=pw"),
        PoolTimeout("couldn't get a connection after 5.00 sec"),
        PoolClosed("the pool is closed"),
        pg_errors.AdminShutdown("terminating connection due to administrator command"),
        pg_errors.ConnectionFailure("connection lost"),
        pg_errors.TooManyConnections("too many connections"),
    ],
)
def test_connection_problems_become_database_unavailable(exc: BaseException) -> None:
    with pytest.raises(DatabaseUnavailableError) as exc_info:
        _raise(exc)

    message = str(exc_info.value)
    assert type(exc).__name__ in message
    assert "secret-host" not in message
    assert "pw" not in message.replace("PoolClosed", "")
    assert exc_info.value.__cause__ is exc


@pytest.mark.parametrize(
    "exc",
    [
        pg_errors.UniqueViolation("duplicate key"),
        pg_errors.QueryCanceled("canceling statement due to statement timeout"),
        pg_errors.InsufficientPrivilege("permission denied"),
        ValueError("unrelated"),
    ],
)
def test_other_errors_are_not_translated(exc: BaseException) -> None:
    with pytest.raises(type(exc)):
        _raise(exc)


def test_handler_returns_503_with_a_fixed_body_and_logs_no_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    test_app = FastAPI()
    test_app.exception_handlers.update(app.exception_handlers)

    @test_app.get("/db")
    def _db() -> None:
        with translate_db_errors():
            raise psycopg.OperationalError("host=secret-host user=skillloop_api password=pw")

    with caplog.at_level(logging.INFO):
        response = TestClient(test_app).get("/db")

    assert response.status_code == 503
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "SERVICE_UNAVAILABLE"
    UUID(body["error"]["request_id"])
    log_text = caplog.text
    assert "OperationalError" in log_text
    assert "secret-host" not in log_text
    assert "password" not in log_text
