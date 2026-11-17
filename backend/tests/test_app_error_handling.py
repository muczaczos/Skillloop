"""The central error handlers as wired on the real application (app.main.app).

The handlers themselves are tested in test_api_errors.py on a bare FastAPI app. These tests
make sure that the application actually uses them and no second, older error handling is
left. The real app has no route that fails on demand, so a small test app is given a copy of
`main.app.exception_handlers`: if main.py did not register a handler, it is missing there.
"""

from uuid import UUID

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import errors as pg_errors

from app import main
from app.api import errors as api_errors
from app.core.errors import AppError, GoalNotFoundError
from app.db.errors import DatabaseUnavailableError


def _client_with_main_handlers(exc: BaseException) -> TestClient:
    test_app = FastAPI()
    test_app.exception_handlers.update(main.app.exception_handlers)

    @test_app.get("/boom")
    def _boom() -> None:
        raise exc

    return TestClient(test_app, raise_server_exceptions=False)


def test_central_handlers_are_registered_on_the_application() -> None:
    handlers = main.app.exception_handlers

    assert handlers[AppError] is api_errors.app_error_handler
    assert handlers[DatabaseUnavailableError] is api_errors.database_unavailable_handler
    assert handlers[psycopg.Error] is api_errors.database_error_handler


def test_main_module_keeps_no_error_handling_of_its_own() -> None:
    assert not hasattr(main, "database_unavailable_handler")
    assert not hasattr(main, "DATABASE_UNAVAILABLE_DETAIL")


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (
            DatabaseUnavailableError("database unavailable (PoolTimeout)"),
            503,
            "SERVICE_UNAVAILABLE",
        ),
        (GoalNotFoundError(), 404, "GOAL_NOT_FOUND"),
        (pg_errors.UniqueViolation("duplicate key"), 500, "INTERNAL_ERROR"),
        (pg_errors.QueryCanceled("statement timeout"), 504, "DATABASE_TIMEOUT"),
    ],
)
def test_errors_of_the_application_use_the_d11_format(
    exc: BaseException, status: int, code: str
) -> None:
    response = _client_with_main_handlers(exc).get("/boom")

    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}, "the old {'detail': ...} shape must be gone"
    assert set(body["error"]) == {"code", "message", "request_id"}
    assert body["error"]["code"] == code
    UUID(body["error"]["request_id"])


def test_health_is_unchanged() -> None:
    response = TestClient(main.app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
