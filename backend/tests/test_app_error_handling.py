"""The central error handlers as wired on the real application (app.main.app).

The handlers themselves are tested in test_api_errors.py on a bare FastAPI app. These tests
make sure that the application actually uses them and no second, older error handling is
left. The real app has no route that fails on demand, so a small test app is given a copy of
`main.app.exception_handlers`: if main.py did not register a handler, it is missing there.
"""

import logging
import time
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fastapi import FastAPI
from fastapi import HTTPException as FastAPIHTTPException
from fastapi.testclient import TestClient
from psycopg import errors as pg_errors
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import main
from app.api import errors as api_errors
from app.api.deps import CurrentUser, get_token_verifier
from app.core.errors import AppError, GoalNotFoundError
from app.db.errors import DatabaseUnavailableError
from tests.auth_helpers import (
    JWKSServer,
    SigningKey,
    default_claims,
    make_token,
    make_verifier,
    running_jwks_server,
)


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


# --- 6.4b: HTTPException ------------------------------------------------------------------


def _assert_d11(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}, "the old {'detail': ...} shape must be gone"
    assert set(body["error"]) == {"code", "message", "request_id"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    UUID(body["error"]["request_id"])
    return body["error"]


def test_http_exception_handler_is_registered_for_starlette_and_fastapi_exceptions() -> None:
    assert main.app.exception_handlers[StarletteHTTPException] is api_errors.http_exception_handler
    assert issubclass(FastAPIHTTPException, StarletteHTTPException)


@pytest.mark.parametrize("exception_class", [FastAPIHTTPException, StarletteHTTPException])
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "UNAUTHORIZED"),
        (403, "FORBIDDEN"),
        (404, "NOT_FOUND"),
        (405, "METHOD_NOT_ALLOWED"),
        (503, "SERVICE_UNAVAILABLE"),
        (400, "CLIENT_ERROR"),
        (409, "CLIENT_ERROR"),
        (418, "CLIENT_ERROR"),
        (422, "CLIENT_ERROR"),
        (429, "CLIENT_ERROR"),
        (500, "INTERNAL_ERROR"),
        (502, "INTERNAL_ERROR"),
        (504, "INTERNAL_ERROR"),
    ],
)
def test_status_is_kept_and_code_comes_from_the_table(
    exception_class: type[Exception], status: int, code: str
) -> None:
    response = _client_with_main_handlers(exception_class(status_code=status)).get("/boom")

    _assert_d11(response, status, code)


@pytest.mark.parametrize("exception_class", [FastAPIHTTPException, StarletteHTTPException])
@pytest.mark.parametrize("status", [400, 401, 403, 404, 418, 500, 503])
@pytest.mark.parametrize(
    "detail",
    [
        "SECRET internal detail: password=hunter2 host=db.internal",
        {"secret": "SECRET value"},
        ["SECRET", "list"],
        None,
    ],
)
def test_the_exception_detail_never_reaches_the_client(
    exception_class: type[Exception], status: int, detail: Any
) -> None:
    response = _client_with_main_handlers(exception_class(status_code=status, detail=detail)).get(
        "/boom"
    )

    assert response.status_code == status
    assert "SECRET" not in response.text and "hunter2" not in response.text
    # The message is one of the fixed texts of the table, whatever `detail` was.
    assert response.json()["error"]["message"] in {
        api_errors.UNAUTHORIZED_MESSAGE,
        api_errors.FORBIDDEN_MESSAGE,
        api_errors.NOT_FOUND_MESSAGE,
        api_errors.METHOD_NOT_ALLOWED_MESSAGE,
        api_errors.SERVICE_UNAVAILABLE_MESSAGE,
        api_errors.CLIENT_ERROR_MESSAGE,
        api_errors.INTERNAL_ERROR_MESSAGE,
    }


def test_only_the_listed_headers_are_passed_on() -> None:
    exc = FastAPIHTTPException(
        status_code=401,
        detail="x",
        headers={
            "WWW-Authenticate": "Bearer",
            "Retry-After": "30",
            "Allow": "GET",
            "X-Internal-Debug": "secret-value",
            "Set-Cookie": "session=abc",
        },
    )

    response = _client_with_main_handlers(exc).get("/boom")

    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["retry-after"] == "30"
    assert response.headers["allow"] == "GET"
    assert "x-internal-debug" not in response.headers
    assert "set-cookie" not in response.headers


def test_no_header_is_invented() -> None:
    response = _client_with_main_handlers(FastAPIHTTPException(status_code=403)).get("/boom")

    assert "www-authenticate" not in response.headers
    assert "allow" not in response.headers


def test_every_http_error_gets_its_own_request_id() -> None:
    client = _client_with_main_handlers(FastAPIHTTPException(status_code=404))

    ids = {client.get("/boom").json()["error"]["request_id"] for _ in range(5)}

    assert len(ids) == 5


def test_http_errors_are_logged_without_detail_or_path(caplog: pytest.LogCaptureFixture) -> None:
    exc = FastAPIHTTPException(status_code=500, detail="SECRET detail with row values")
    client = _client_with_main_handlers(exc)

    with caplog.at_level(logging.INFO, logger="app.api.errors"):
        response = client.get("/boom?token=SECRET-QUERY")

    # Only the records of the handler: the test client logs the request URL on its own.
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name == "app.api.errors")
    request_id = response.json()["error"]["request_id"]
    assert f"HTTP 500 INTERNAL_ERROR request_id={request_id}" in ours
    assert "SECRET" not in ours


def test_client_errors_are_logged_below_error_level(caplog: pytest.LogCaptureFixture) -> None:
    client = _client_with_main_handlers(FastAPIHTTPException(status_code=404))

    with caplog.at_level(logging.DEBUG):
        client.get("/boom")

    records = [r for r in caplog.records if "NOT_FOUND" in r.getMessage()]
    assert records and all(r.levelno < logging.ERROR for r in records)


# --- unknown paths and methods on the real application --------------------------------------


def test_unknown_path_answers_404_in_the_d11_format() -> None:
    response = TestClient(main.app).get("/api/v1/does-not-exist")

    error = _assert_d11(response, 404, "NOT_FOUND")
    assert error["message"] != "Not Found"  # the fixed message, not Starlette's text


def test_wrong_method_answers_405_with_the_allow_header() -> None:
    response = TestClient(main.app).post("/api/v1/health")

    _assert_d11(response, 405, "METHOD_NOT_ALLOWED")
    assert "GET" in response.headers["allow"]


def test_health_is_still_healthy_next_to_the_error_handling() -> None:
    assert TestClient(main.app).get("/api/v1/health").json() == {"status": "ok"}


# --- CORS on error responses -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"), [("get", "/api/v1/missing"), ("post", "/api/v1/health")]
)
def test_error_responses_carry_cors_headers_for_an_allowed_origin(method: str, path: str) -> None:
    origin = main.settings.cors_origins[0]

    response = getattr(TestClient(main.app), method)(path, headers={"Origin": origin})

    assert response.status_code in (404, 405)
    assert response.headers["access-control-allow-origin"] == origin


def test_error_responses_carry_no_cors_headers_for_other_origins() -> None:
    response = TestClient(main.app).get(
        "/api/v1/missing", headers={"Origin": "https://evil.example"}
    )

    assert response.status_code == 404
    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_is_not_turned_into_an_error() -> None:
    origin = main.settings.cors_origins[0]

    response = TestClient(main.app).options(
        "/api/v1/health",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


# --- authentication with the handlers of the real application -----------------------------------


def _auth_app(jwks_url: str) -> FastAPI:
    test_app = FastAPI()
    test_app.exception_handlers.update(main.app.exception_handlers)

    @test_app.get("/protected")
    def protected(user: CurrentUser) -> dict[str, str]:
        return {"user_id": str(user.id)}

    verifier = make_verifier(jwks_url)
    test_app.dependency_overrides[get_token_verifier] = lambda: verifier
    return test_app


def test_authenticated_request_is_unaffected(
    jwks_server: JWKSServer, signing_key: SigningKey
) -> None:
    token = make_token(signing_key)

    with TestClient(_auth_app(jwks_server.url)) as client:
        response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert set(response.json()) == {"user_id"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": "Bearer "},
    ],
    ids=["no-header", "not-bearer", "malformed-token", "empty-token"],
)
def test_missing_or_bad_credentials_answer_401_in_d11_with_www_authenticate(
    jwks_server: JWKSServer, headers: dict[str, str]
) -> None:
    with TestClient(_auth_app(jwks_server.url)) as client:
        response = client.get("/protected", headers=headers)

    error = _assert_d11(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert error["message"] == api_errors.UNAUTHORIZED_MESSAGE


def test_an_expired_token_answers_401_and_the_token_is_not_echoed(
    jwks_server: JWKSServer, signing_key: SigningKey, caplog: pytest.LogCaptureFixture
) -> None:
    token = make_token(signing_key, default_claims(exp=int(time.time()) - 3600))

    with caplog.at_level(logging.DEBUG), TestClient(_auth_app(jwks_server.url)) as client:
        response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    _assert_d11(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    header, payload, signature = token.split(".")
    for fragment in (header, payload, signature):
        assert fragment not in response.text
        assert fragment not in caplog.text


def test_unreachable_signing_keys_answer_503_in_d11_without_www_authenticate(
    signing_key: SigningKey,
) -> None:
    with running_jwks_server() as server:
        url = server.url  # the server is stopped when the block ends: nothing listens there

    token = make_token(signing_key)
    with TestClient(_auth_app(url)) as client:
        response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    error = _assert_d11(response, 503, "SERVICE_UNAVAILABLE")
    assert "www-authenticate" not in response.headers
    assert error["message"] == api_errors.SERVICE_UNAVAILABLE_MESSAGE
