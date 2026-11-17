import logging
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import errors as pg_errors

from app.api.errors import register_error_handlers
from app.core.errors import (
    ActiveGoalExistsError,
    AppError,
    GoalArchivedError,
    GoalNotFoundError,
    NoActiveGoalError,
    ProfileNotFoundError,
)
from app.db.errors import DatabaseUnavailableError

SECRET = "SELECT secret_column FROM t WHERE key = 'secret-value'"


class _Diag:
    def __init__(self, constraint_name: str | None) -> None:
        self.constraint_name = constraint_name


def _db_error(sqlstate: str, constraint: str | None = None) -> psycopg.Error:
    """A psycopg error with the given SQLSTATE and constraint name, whose message holds
    text that must never reach the client or the logs."""
    base = pg_errors.lookup(sqlstate)

    class _Error(base):  # type: ignore[misc, valid-type]
        @property
        def diag(self) -> Any:  # psycopg builds diag from a server result; fake it
            return _Diag(constraint)

    return _Error(f'{SECRET} DETAIL: Key (user_id)=(secret-value) violates "{constraint}"')


@pytest.fixture
def raising_client() -> Any:
    """Client of an app whose only route raises whatever `raising_client.exc` is set to."""
    app = FastAPI()
    register_error_handlers(app)
    holder: dict[str, BaseException] = {}

    @app.get("/boom")
    def _boom() -> None:
        raise holder["exc"]

    client = TestClient(app, raise_server_exceptions=False)

    def call(exc: BaseException) -> Any:
        holder["exc"] = exc
        return client.get("/boom")

    return call


def _assert_d11(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert set(error) == {"code", "message", "request_id"}
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    UUID(error["request_id"])  # raises if it is not a UUID
    return error


# --- AppError ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (ProfileNotFoundError(), 404, "PROFILE_NOT_FOUND"),
        (GoalNotFoundError(), 404, "GOAL_NOT_FOUND"),
        (NoActiveGoalError(), 404, "NO_ACTIVE_GOAL"),
        (GoalArchivedError(), 409, "GOAL_ARCHIVED"),
        (ActiveGoalExistsError(), 409, "ACTIVE_GOAL_EXISTS"),
    ],
)
def test_app_errors_keep_their_status_and_code(
    raising_client: Any, exc: AppError, status: int, code: str
) -> None:
    error = _assert_d11(raising_client(exc), status, code)

    assert error["message"] == exc.message


def test_every_request_gets_its_own_request_id(raising_client: Any) -> None:
    first = raising_client(ProfileNotFoundError()).json()["error"]["request_id"]
    second = raising_client(ProfileNotFoundError()).json()["error"]["request_id"]

    assert first != second


# --- database errors: mapping ------------------------------------------------------------


@pytest.mark.parametrize(
    ("sqlstate", "constraint", "status", "code"),
    [
        # 23505 is always 500, including the constraint create_goal() maps itself.
        ("23505", "one_active_goal_per_user", 500, "INTERNAL_ERROR"),
        ("23505", "some_other_unique", 500, "INTERNAL_ERROR"),
        ("23505", None, 500, "INTERNAL_ERROR"),
        ("23503", "career_goals_user_id_fkey", 401, "UNAUTHORIZED"),
        ("23503", "some_other_fkey", 409, "CONFLICT"),
        ("23503", None, 409, "CONFLICT"),
        ("23514", "career_goals_status_check", 500, "INTERNAL_ERROR"),
        ("23502", None, 500, "INTERNAL_ERROR"),
        ("42501", None, 500, "INTERNAL_ERROR"),
        ("22001", None, 422, "VALIDATION_ERROR"),
        ("22003", None, 422, "VALIDATION_ERROR"),
        ("22021", None, 422, "VALIDATION_ERROR"),
        ("22012", None, 500, "INTERNAL_ERROR"),  # division by zero: our bug
        ("22P02", None, 500, "INTERNAL_ERROR"),
        ("57014", None, 504, "DATABASE_TIMEOUT"),
        ("42601", None, 500, "INTERNAL_ERROR"),  # syntax error: our bug
        ("40001", None, 500, "INTERNAL_ERROR"),
    ],
)
def test_database_error_mapping(
    raising_client: Any, sqlstate: str, constraint: str | None, status: int, code: str
) -> None:
    _assert_d11(raising_client(_db_error(sqlstate, constraint)), status, code)


def test_psycopg_error_without_sqlstate_is_500(raising_client: Any) -> None:
    _assert_d11(
        raising_client(psycopg.ProgrammingError("cannot adapt type")), 500, "INTERNAL_ERROR"
    )


def test_constraint_is_matched_by_exact_name(raising_client: Any) -> None:
    for near_miss in ("career_goals_user_id_fkey2", "CAREER_GOALS_USER_ID_FKEY", "user_id_fkey"):
        _assert_d11(raising_client(_db_error("23503", near_miss)), 409, "CONFLICT")


def test_database_unavailable_is_503(raising_client: Any) -> None:
    exc = DatabaseUnavailableError("database unavailable (PoolTimeout)")

    _assert_d11(raising_client(exc), 503, "SERVICE_UNAVAILABLE")


# --- headers -----------------------------------------------------------------------------


def test_401_keeps_www_authenticate_header(raising_client: Any) -> None:
    response = raising_client(_db_error("23503", "career_goals_user_id_fkey"))

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_other_responses_have_no_www_authenticate_header(raising_client: Any) -> None:
    assert "WWW-Authenticate" not in raising_client(_db_error("23503", None)).headers
    assert "WWW-Authenticate" not in raising_client(GoalNotFoundError()).headers


# --- safe messages -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "sqlstate", ["23505", "23503", "23514", "23502", "42501", "22001", "22012", "57014"]
)
def test_psycopg_details_never_reach_the_client(raising_client: Any, sqlstate: str) -> None:
    response = raising_client(_db_error(sqlstate, "some_constraint"))

    assert "secret" not in response.text
    assert "SELECT" not in response.text
    assert "some_constraint" not in response.text
    assert "psycopg" not in response.text.lower()


def test_internal_errors_use_a_fixed_message(raising_client: Any) -> None:
    error = _assert_d11(
        raising_client(_db_error("23514", "career_goals_status_check")), 500, "INTERNAL_ERROR"
    )

    assert error["message"] == "An internal error occurred."


# --- logging -----------------------------------------------------------------------------


def _error_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize(
    "exc",
    [
        ProfileNotFoundError(),
        GoalNotFoundError(),
        GoalArchivedError(),
        ActiveGoalExistsError(),
        _db_error("23503", "career_goals_user_id_fkey"),
        _db_error("23503", "other_fkey"),
        _db_error("22001"),
    ],
)
def test_client_errors_do_not_log_at_error_level(
    raising_client: Any, caplog: pytest.LogCaptureFixture, exc: BaseException
) -> None:
    with caplog.at_level(logging.DEBUG):
        raising_client(exc)

    assert _error_records(caplog) == []


@pytest.mark.parametrize(
    "exc",
    [
        _db_error("23505", "one_active_goal_per_user"),
        _db_error("23514", "career_goals_status_check"),
        _db_error("23502"),
        _db_error("42501"),
        _db_error("22012"),
        _db_error("57014"),
        _db_error("42601"),
        DatabaseUnavailableError("database unavailable (PoolTimeout)"),
    ],
)
def test_server_errors_log_at_error_level(
    raising_client: Any, caplog: pytest.LogCaptureFixture, exc: BaseException
) -> None:
    with caplog.at_level(logging.DEBUG):
        response = raising_client(exc)

    records = _error_records(caplog)
    assert len(records) == 1
    assert response.json()["error"]["request_id"] in records[0].getMessage()


def test_insufficient_privilege_is_logged_at_error_level_and_is_not_403(
    raising_client: Any, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        response = raising_client(_db_error("42501"))

    assert response.status_code == 500
    assert "SQLSTATE=42501" in _error_records(caplog)[0].getMessage()


def test_logs_hold_no_psycopg_message_or_traceback(
    raising_client: Any, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        raising_client(_db_error("23505", "some_constraint"))

    assert "secret" not in caplog.text
    assert "SELECT" not in caplog.text
    assert "Traceback" not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)
    # What is logged: class, SQLSTATE, constraint name.
    assert "SQLSTATE=23505" in caplog.text
    assert "constraint=some_constraint" in caplog.text


# --- scope -------------------------------------------------------------------------------


def test_programming_errors_are_not_handled(raising_client: Any) -> None:
    """Handlers cover only the agreed exception types: a RuntimeError is not turned into an
    API error body (the test client shows Starlette's bare 500 for an unhandled error)."""
    response = raising_client(RuntimeError("bug"))

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
