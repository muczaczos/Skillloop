"""HTTP tests of GET / PATCH /api/v1/profile WITHOUT a database.

Requests go through the real application (`app.main.app`): routing, authentication
dependency, request validation, response model and the central error handlers. Only the
service layer is replaced, so what is under test is the HTTP behaviour of the endpoints.
"""

import inspect
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from psycopg import errors as pg_errors

import app.api.profile as profile_api
from app import main
from app.api.deps import get_current_user, get_db_pool
from app.core.errors import ProfileNotFoundError
from app.db.errors import DatabaseUnavailableError
from app.models.auth import AuthenticatedUser
from app.models.profile import ProfileRecord
from app.schemas.profile import ProfileUpdate

USER = AuthenticatedUser(id=UUID("11111111-1111-1111-1111-111111111111"))
POOL = object()  # stands in for the connection pool; the fake services never use it
T0 = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
CANARY = "CANARY-5e1c2"
URL = "/api/v1/profile"


def record(**overrides: Any) -> ProfileRecord:
    values: dict[str, Any] = {
        "id": USER.id,
        "display_name": "Ann",
        "preferred_language": "pl",
        "created_at": T0,
        "updated_at": T0,
    }
    values.update(overrides)
    return ProfileRecord(**values)


class FakeService:
    """Records the calls of the endpoints and answers (or raises) what the test sets."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, Any, Any]] = []
        self.result: ProfileRecord | BaseException = record()

    def _answer(self) -> ProfileRecord:
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    def get_profile(self, pool: Any, user: Any) -> ProfileRecord:
        self.calls.append(("get", pool, user, None))
        return self._answer()

    def update_profile(self, pool: Any, user: Any, data: ProfileUpdate) -> ProfileRecord:
        self.calls.append(("update", pool, user, data))
        return self._answer()


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> FakeService:
    fake = FakeService()
    monkeypatch.setattr(profile_api.profile_service, "get_profile", fake.get_profile)
    monkeypatch.setattr(profile_api.profile_service, "update_profile", fake.update_profile)
    return fake


@pytest.fixture
def client(service: FakeService) -> Iterator[TestClient]:
    """Authentication and the pool are replaced; everything else is the real application."""
    main.app.dependency_overrides[get_current_user] = lambda: USER
    main.app.dependency_overrides[get_db_pool] = lambda: POOL
    try:
        yield TestClient(main.app, raise_server_exceptions=False)
    finally:
        main.app.dependency_overrides.pop(get_current_user, None)
        main.app.dependency_overrides.pop(get_db_pool, None)


def assert_error(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}, "the unified D11 shape is expected, not {'detail': ...}"
    error = body["error"]
    assert error["code"] == code
    UUID(error["request_id"])
    return error


# --- routes and declaration ---------------------------------------------------------------


def test_the_profile_resource_offers_exactly_get_and_patch() -> None:
    paths = main.app.openapi()["paths"]

    assert sorted(paths[URL]) == ["get", "patch"]


def test_the_endpoints_declare_their_response_model_and_status() -> None:
    paths = main.app.openapi()["paths"]

    for method in ("get", "patch"):
        success = paths[URL][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert success == {"$ref": "#/components/schemas/ProfileResponse"}
    patch_body = paths[URL]["patch"]["requestBody"]["content"]["application/json"]["schema"]
    assert patch_body == {"$ref": "#/components/schemas/ProfileUpdate"}
    assert paths[URL]["patch"]["security"] == [{"HTTPBearer": []}]


def test_the_handlers_are_plain_functions_so_they_run_in_the_thread_pool() -> None:
    assert not inspect.iscoroutinefunction(profile_api.get_profile)
    assert not inspect.iscoroutinefunction(profile_api.update_profile)


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_other_methods_answer_405_in_the_unified_format(client: TestClient, method: str) -> None:
    response = getattr(client, method)(URL)

    assert_error(response, 405, "METHOD_NOT_ALLOWED")
    assert response.headers["allow"] == "GET, PATCH"


def test_cors_preflight_allows_patch(client: TestClient) -> None:
    origin = main.settings.cors_origins[0]

    response = client.options(
        URL, headers={"Origin": origin, "Access-Control-Request-Method": "PATCH"}
    )

    assert response.status_code == 200
    assert "PATCH" in response.headers["access-control-allow-methods"]


# --- GET ----------------------------------------------------------------------------------


def test_get_returns_the_full_profile(client: TestClient, service: FakeService) -> None:
    response = client.get(URL)

    assert response.status_code == 200
    assert response.json() == {
        "id": str(USER.id),
        "display_name": "Ann",
        "preferred_language": "pl",
        "created_at": "2026-11-18T09:30:00Z",
        "updated_at": "2026-11-18T09:30:00Z",
    }


def test_get_calls_the_service_with_the_user_from_the_token_and_the_pool(
    client: TestClient, service: FakeService
) -> None:
    client.get(URL)

    assert service.calls == [("get", POOL, USER, None)]


def test_get_cannot_be_pointed_at_another_user_by_the_request(
    client: TestClient, service: FakeService
) -> None:
    other = "22222222-2222-2222-2222-222222222222"

    client.get(URL, params={"user_id": other, "id": other}, headers={"X-User-Id": other})

    assert service.calls[0][2] == USER  # nothing in the request can change the identity


def test_get_answers_404_when_the_profile_does_not_exist(
    client: TestClient, service: FakeService
) -> None:
    service.result = ProfileNotFoundError()

    assert_error(client.get(URL), 404, "PROFILE_NOT_FOUND")


# --- PATCH --------------------------------------------------------------------------------


def test_patch_returns_the_updated_profile(client: TestClient, service: FakeService) -> None:
    service.result = record(display_name="Bea", preferred_language="en")

    response = client.patch(URL, json={"display_name": "Bea", "preferred_language": "en"})

    assert response.status_code == 200
    assert response.json()["display_name"] == "Bea"
    assert response.json()["preferred_language"] == "en"
    assert set(response.json()) == {
        "id",
        "display_name",
        "preferred_language",
        "created_at",
        "updated_at",
    }


@pytest.mark.parametrize(
    ("body", "changes"),
    [
        ({"display_name": " Ann "}, {"display_name": "Ann"}),
        ({"preferred_language": "pl"}, {"preferred_language": "pl"}),
        ({"display_name": None}, {"display_name": None}),
        (
            {"display_name": "Bea", "preferred_language": "en"},
            {"display_name": "Bea", "preferred_language": "en"},
        ),
    ],
)
def test_patch_forwards_exactly_the_fields_that_were_sent(
    client: TestClient, service: FakeService, body: dict[str, Any], changes: dict[str, Any]
) -> None:
    response = client.patch(URL, json=body)

    assert response.status_code == 200
    ((name, pool, user, data),) = service.calls
    assert (name, pool, user) == ("update", POOL, USER)
    assert data.changes() == changes  # an omitted field is not part of the change


def test_patch_answers_404_when_the_profile_does_not_exist(
    client: TestClient, service: FakeService
) -> None:
    service.result = ProfileNotFoundError()

    assert_error(client.patch(URL, json={"display_name": "Ann"}), 404, "PROFILE_NOT_FOUND")


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "body"),
        ({"preferred_language": None}, "preferred_language"),
        ({"preferred_language": "de"}, "preferred_language"),
        ({"display_name": ""}, "display_name"),
        ({"display_name": "   "}, "display_name"),
        ({"display_name": "x" * 101}, "display_name"),
        ({"display_name": 123}, "display_name"),
        ({"display_name": "a\u0000b"}, "display_name"),
        ({"user_id": CANARY}, "user_id"),
        ({"id": CANARY}, "id"),
        ({"created_at": CANARY}, "created_at"),
        ({"display_name": "Ann", "updated_at": CANARY}, "updated_at"),
    ],
)
def test_patch_validation_errors_are_422_and_never_reach_the_service(
    client: TestClient, service: FakeService, body: dict[str, Any], field: str
) -> None:
    response = client.patch(URL, json=body)

    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert field in {entry["field"] for entry in error["details"]}
    assert all(set(entry) == {"field", "message"} for entry in error["details"])
    assert CANARY not in response.text
    assert service.calls == []


@pytest.mark.parametrize("content", ["not json", '{"display_name": ', "[]", '"text"', "42"])
def test_patch_with_a_body_that_is_not_a_json_object_is_422(
    client: TestClient, service: FakeService, content: str
) -> None:
    response = client.patch(URL, content=content, headers={"Content-Type": "application/json"})

    assert_error(response, 422, "VALIDATION_ERROR")
    assert service.calls == []


def test_patch_without_a_body_is_422(client: TestClient, service: FakeService) -> None:
    assert_error(client.patch(URL), 422, "VALIDATION_ERROR")
    assert service.calls == []


# --- authentication -----------------------------------------------------------------------


@pytest.fixture
def unauthenticated(service: FakeService) -> Iterator[TestClient]:
    """The real authentication dependency (only the pool is replaced)."""
    main.app.dependency_overrides[get_db_pool] = lambda: POOL
    try:
        yield TestClient(main.app, raise_server_exceptions=False)
    finally:
        main.app.dependency_overrides.pop(get_db_pool, None)


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer not-a-jwt"},
    ],
    ids=["no-header", "not-bearer", "empty-token", "malformed-token"],
)
@pytest.mark.parametrize(
    ("method", "kwargs"),
    [("get", {}), ("patch", {"json": {"display_name": "Ann"}})],
    ids=["GET", "PATCH"],
)
def test_without_valid_credentials_both_endpoints_answer_401_and_do_not_call_the_service(
    unauthenticated: TestClient,
    service: FakeService,
    method: str,
    kwargs: dict[str, Any],
    headers: dict[str, str],
) -> None:
    response = getattr(unauthenticated, method)(URL, headers=headers, **kwargs)

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert service.calls == []


def test_authentication_comes_before_body_validation(
    unauthenticated: TestClient, service: FakeService
) -> None:
    response = unauthenticated.patch(URL, json={"user_id": CANARY})

    assert_error(response, 401, "UNAUTHORIZED")  # not 422: the caller is not identified yet
    assert CANARY not in response.text


# --- infrastructure failures --------------------------------------------------------------


def test_a_service_that_cannot_reach_the_database_answers_503(
    client: TestClient, service: FakeService
) -> None:
    service.result = DatabaseUnavailableError("database unavailable (PoolTimeout)")

    assert_error(client.get(URL), 503, "SERVICE_UNAVAILABLE")
    assert_error(client.patch(URL, json={"display_name": "Ann"}), 503, "SERVICE_UNAVAILABLE")


def test_a_query_timeout_answers_504(client: TestClient, service: FakeService) -> None:
    service.result = pg_errors.QueryCanceled("canceling statement due to statement timeout")

    assert_error(client.get(URL), 504, "DATABASE_TIMEOUT")


def test_an_unexpected_database_error_answers_500_without_details(
    client: TestClient, service: FakeService, caplog: pytest.LogCaptureFixture
) -> None:
    service.result = pg_errors.UniqueViolation(f"duplicate key value {CANARY}")

    with caplog.at_level(logging.DEBUG):
        response = client.patch(URL, json={"display_name": "Ann"})

    assert_error(response, 500, "INTERNAL_ERROR")
    assert CANARY not in response.text
    assert CANARY not in caplog.text


def test_without_a_pool_the_endpoints_answer_503(monkeypatch: pytest.MonkeyPatch) -> None:
    # The pool is created by the application lifespan; a client that does not run it has none.
    # Other tests run that lifespan on the same global app and leave their pool in app.state,
    # so it is removed explicitly here (and restored afterwards by monkeypatch).
    monkeypatch.delattr(main.app.state, "db_pool", raising=False)
    main.app.dependency_overrides[get_current_user] = lambda: USER
    try:
        response = TestClient(main.app, raise_server_exceptions=False).get(URL)
    finally:
        main.app.dependency_overrides.pop(get_current_user, None)

    assert_error(response, 503, "SERVICE_UNAVAILABLE")
