"""The central error handlers as wired on the real application (app.main.app).

The handlers themselves are tested in test_api_errors.py on a bare FastAPI app. These tests
make sure that the application actually uses them and no second, older error handling is
left. The real app has no route that fails on demand, so a small test app is given a copy of
`main.app.exception_handlers`: if main.py did not register a handler, it is missing there.
"""

import logging
import time
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
import pytest
from fastapi import FastAPI, Header
from fastapi import HTTPException as FastAPIHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from psycopg import errors as pg_errors
from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_core import PydanticCustomError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import main
from app.api import errors as api_errors
from app.api.deps import CurrentUser, get_token_verifier
from app.core.errors import AppError, GoalNotFoundError
from app.db.errors import DatabaseUnavailableError
from app.schemas.career_goal import CareerGoalCreate, CareerGoalUpdate
from app.schemas.profile import ProfileUpdate
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


# --- 6.4c: RequestValidationError -----------------------------------------------------------


class _Line(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qty: int


class _Address(BaseModel):
    zip: str


class _Order(BaseModel):
    lines: list[_Line]
    address: _Address
    kind: Literal["a", "b"] = "a"


class _Weird(BaseModel):
    """Validators whose own messages repeat the value: the handler must not pass them on."""

    custom: str | None = None
    asserted: str | None = None

    @field_validator("custom")
    @classmethod
    def _custom(cls, value: str | None) -> str | None:
        raise PydanticCustomError("weird_error", "leaks {value}", {"value": value})

    @field_validator("asserted")
    @classmethod
    def _asserted(cls, value: str | None) -> str | None:
        assert False, f"leaks {value}"


def _validation_client() -> TestClient:
    test_app = FastAPI()
    test_app.exception_handlers.update(main.app.exception_handlers)

    @test_app.post("/goals")
    def create_goal(body: CareerGoalCreate) -> dict[str, bool]:
        return {"ok": True}

    @test_app.patch("/goals")
    def update_goal(body: CareerGoalUpdate) -> dict[str, bool]:
        return {"ok": True}

    @test_app.patch("/profile")
    def update_profile(body: ProfileUpdate) -> dict[str, bool]:
        return {"ok": True}

    @test_app.post("/orders")
    def create_order(body: _Order) -> dict[str, bool]:
        return {"ok": True}

    @test_app.post("/weird")
    def weird(body: _Weird) -> dict[str, bool]:
        return {"ok": True}

    @test_app.get("/query")
    def query(limit: int, flag: bool = False) -> dict[str, bool]:
        return {"ok": True}

    @test_app.get("/items/{item_id}")
    def item(item_id: UUID) -> dict[str, bool]:
        return {"ok": True}

    @test_app.get("/header")
    def header(x_token: Annotated[str, Header()]) -> dict[str, bool]:
        return {"ok": True}

    return TestClient(test_app)


@pytest.fixture(scope="module")
def vclient() -> TestClient:
    return _validation_client()


CANARY = "CANARY-7f3a9"
GOAL = {"target_role": "DevOps Engineer", "seniority": "junior"}


def _details(response: Any) -> list[dict[str, str]]:
    """Check the whole D11 validation envelope and return `details`."""
    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert set(error) == {"code", "message", "details", "request_id"}
    assert error["code"] == "VALIDATION_ERROR"
    assert error["message"] == api_errors.VALIDATION_ERROR_MESSAGE
    UUID(error["request_id"])
    assert isinstance(error["details"], list) and error["details"]
    for entry in error["details"]:
        assert set(entry) == {"field", "message"}, "details hold field and message only"
    return error["details"]


def _assert_no_internals(response: Any) -> None:
    assert CANARY not in response.text
    for forbidden in (
        '"input"',
        '"ctx"',
        '"url"',
        '"loc"',
        '"msg"',
        '"type"',
        "errors.pydantic.dev",
    ):
        assert forbidden not in response.text


def test_validation_handler_is_registered_on_the_application() -> None:
    handler = main.app.exception_handlers[RequestValidationError]

    assert handler is api_errors.validation_error_handler


def test_an_unsupported_value_names_the_field_and_not_the_value(vclient: TestClient) -> None:
    response = vclient.post("/goals", json={**GOAL, "seniority": CANARY})

    assert _details(response) == [
        {"field": "seniority", "message": "The value is not one of the allowed options."}
    ]
    _assert_no_internals(response)


def test_missing_fields_are_reported_one_by_one(vclient: TestClient) -> None:
    response = vclient.post("/goals", json={})

    assert _details(response) == [
        {"field": "target_role", "message": "This field is required."},
        {"field": "seniority", "message": "This field is required."},
    ]


@pytest.mark.parametrize(
    ("payload", "field", "message"),
    [
        ({"target_role": "x" * 101}, "target_role", "The value is too long."),
        ({"target_role": ""}, "target_role", "The value is too short."),
        ({"target_role": "   "}, "target_role", "The value is too short."),
        ({"description": "y" * 2001}, "description", "The value is too long."),
        ({"target_role": 123}, "target_role", "The value must be text."),
    ],
)
def test_limits_and_types_have_fixed_messages(
    vclient: TestClient, payload: dict[str, Any], field: str, message: str
) -> None:
    response = vclient.post("/goals", json={**GOAL, **payload})

    assert _details(response) == [{"field": field, "message": message}]


def test_a_canary_in_every_submitted_value_never_comes_back(vclient: TestClient) -> None:
    response = vclient.post(
        "/goals",
        json={
            "target_role": CANARY * 30,  # too long
            "seniority": CANARY,  # not allowed
            "description": CANARY * 400,  # too long
            "user_id": CANARY,  # forbidden field with a canary VALUE
        },
    )

    fields = {entry["field"] for entry in _details(response)}
    assert fields == {"target_role", "seniority", "description", "user_id"}
    _assert_no_internals(response)


@pytest.mark.parametrize("extra", ["user_id", "status", "id", "created_at"])
def test_server_controlled_fields_are_reported_as_not_allowed(
    vclient: TestClient, extra: str
) -> None:
    response = vclient.post("/goals", json={**GOAL, extra: CANARY})

    assert _details(response) == [{"field": extra, "message": "This field is not allowed."}]
    _assert_no_internals(response)


def test_errors_raised_by_our_validators_keep_their_own_message(vclient: TestClient) -> None:
    nul = vclient.post("/goals", json={**GOAL, "target_role": "a\u0000b"})
    null = vclient.patch("/goals", json={"seniority": None})
    profile = vclient.patch("/profile", json={"preferred_language": None})

    assert _details(nul) == [{"field": "target_role", "message": "must not contain NUL characters"}]
    assert _details(null) == [{"field": "seniority", "message": "must not be null"}]
    assert _details(profile) == [{"field": "preferred_language", "message": "must not be null"}]


def test_an_error_about_the_whole_body_is_named_body(vclient: TestClient) -> None:
    response = vclient.patch("/goals", json={})

    assert _details(response) == [
        {"field": "body", "message": "at least one field must be provided"}
    ]


def test_a_body_of_the_wrong_shape_is_named_body(vclient: TestClient) -> None:
    for body in ('"just a string"', "[1, 2]", "42"):
        response = vclient.post(
            "/goals", content=body, headers={"Content-Type": "application/json"}
        )

        assert _details(response) == [
            {"field": "body", "message": "The value has the wrong structure."}
        ]


def test_malformed_json_is_reported_without_an_offset_or_the_text(vclient: TestClient) -> None:
    response = vclient.post(
        "/goals", content='{"target_role": ' + CANARY, headers={"Content-Type": "application/json"}
    )

    assert _details(response) == [
        {"field": "body", "message": "The request body is not valid JSON."}
    ]
    _assert_no_internals(response)


def test_nested_fields_use_dotted_paths_without_the_location_prefix(vclient: TestClient) -> None:
    response = vclient.post(
        "/orders",
        json={"lines": [{"qty": 1}, {"qty": CANARY}, {"qty": 3, "extra": 1}], "address": {}},
    )

    assert _details(response) == [
        {"field": "lines.1.qty", "message": "The value must be an integer."},
        {"field": "lines.2.extra", "message": "This field is not allowed."},
        {"field": "address.zip", "message": "This field is required."},
    ]
    _assert_no_internals(response)


def test_query_path_and_header_parameters(vclient: TestClient) -> None:
    query = vclient.get(f"/query?limit={CANARY}&flag={CANARY}")
    missing = vclient.get("/query")
    path = vclient.get(f"/items/{CANARY}")
    header = vclient.get("/header")

    assert _details(query) == [
        {"field": "limit", "message": "The value must be an integer."},
        {"field": "flag", "message": "The value must be true or false."},
    ]
    assert _details(missing) == [{"field": "limit", "message": "This field is required."}]
    assert _details(path) == [{"field": "item_id", "message": "The value must be a valid UUID."}]
    assert _details(header) == [{"field": "x-token", "message": "This field is required."}]
    for response in (query, path):
        _assert_no_internals(response)  # Pydantic's own UUID text would repeat a character


def test_unknown_error_types_and_foreign_messages_fall_back_to_a_generic_text(
    vclient: TestClient,
) -> None:
    response = vclient.post("/weird", json={"custom": CANARY, "asserted": CANARY})

    assert _details(response) == [
        {"field": "custom", "message": api_errors.GENERIC_FIELD_MESSAGE},
        {"field": "asserted", "message": api_errors.GENERIC_FIELD_MESSAGE},
    ]
    _assert_no_internals(response)


def test_the_number_of_details_is_capped(vclient: TestClient) -> None:
    lines = [{"qty": CANARY} for _ in range(50)]

    response = vclient.post("/orders", json={"lines": lines, "address": {"zip": "1"}})

    assert len(_details(response)) == api_errors._MAX_VALIDATION_DETAILS


def test_a_very_long_field_name_chosen_by_the_client_is_shortened(vclient: TestClient) -> None:
    response = vclient.post("/goals", json={**GOAL, "k" * 500: 1})

    (entry,) = _details(response)
    assert entry["message"] == "This field is not allowed."
    assert len(entry["field"]) == api_errors._MAX_FIELD_LENGTH


def test_every_validation_error_has_its_own_request_id(vclient: TestClient) -> None:
    ids = {vclient.post("/goals", json={}).json()["error"]["request_id"] for _ in range(5)}

    assert len(ids) == 5


def test_validation_errors_are_logged_by_count_only(
    vclient: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="app.api.errors"):
        response = vclient.post("/goals", json={"seniority": CANARY, CANARY: CANARY})

    ours = "\n".join(r.getMessage() for r in caplog.records if r.name == "app.api.errors")
    request_id = response.json()["error"]["request_id"]
    assert f"VALIDATION_ERROR: 3 problem(s) request_id={request_id}" in ours
    assert CANARY not in ours


def test_the_old_detail_shape_is_gone(vclient: TestClient) -> None:
    body = vclient.post("/goals", json={}).json()

    assert "detail" not in body and list(body) == ["error"]
