"""HTTP tests of POST /api/v1/career-goals and GET /api/v1/career-goals/active, no database.

Requests go through the real application (`app.main.app`): routing, authentication
dependency, request validation, response model and the central error handlers. Only the
service layer is replaced, so what is under test is the HTTP behaviour of the endpoints.
"""

import inspect
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from psycopg import errors as pg_errors

import app.api.career_goals as goals_api
from app import main
from app.api.deps import get_current_user, get_db_pool
from app.core.errors import (
    ActiveGoalExistsError,
    GoalArchivedError,
    GoalNotFoundError,
    NoActiveGoalError,
)
from app.db.errors import DatabaseUnavailableError
from app.models.auth import AuthenticatedUser
from app.models.career_goal import CareerGoalRecord
from app.schemas.career_goal import CareerGoalCreate, CareerGoalUpdate

USER = AuthenticatedUser(id=UUID("11111111-1111-1111-1111-111111111111"))
GOAL_ID = UUID("22222222-2222-2222-2222-222222222222")
POOL = object()  # stands in for the connection pool; the fake services never use it
T0 = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)
CANARY = "CANARY-7b3d9"
COLLECTION = "/api/v1/career-goals"
ACTIVE = "/api/v1/career-goals/active"
ITEM = f"/api/v1/career-goals/{GOAL_ID}"
ARCHIVE = f"/api/v1/career-goals/{GOAL_ID}/archive"
ITEM_TEMPLATE = "/api/v1/career-goals/{goal_id}"
ARCHIVE_TEMPLATE = "/api/v1/career-goals/{goal_id}/archive"
VALID = {"target_role": "DevOps Engineer", "seniority": "junior"}


def record(**overrides: Any) -> CareerGoalRecord:
    values: dict[str, Any] = {
        "id": GOAL_ID,
        "user_id": USER.id,
        "target_role": "DevOps Engineer",
        "seniority": "junior",
        "description": "Prepare for a Junior DevOps Engineer position.",
        "status": "active",
        "created_at": T0,
        "updated_at": T0,
    }
    values.update(overrides)
    return CareerGoalRecord(**values)


class FakeService:
    """Records the calls of the endpoints and answers (or raises) what the test sets."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, Any, Any]] = []
        self.result: CareerGoalRecord | BaseException = record()

    def _answer(self) -> CareerGoalRecord:
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    def create_goal(self, pool: Any, user: Any, data: CareerGoalCreate) -> CareerGoalRecord:
        self.calls.append(("create", pool, user, data))
        return self._answer()

    def get_active_goal(self, pool: Any, user: Any) -> CareerGoalRecord:
        self.calls.append(("get_active", pool, user, None))
        return self._answer()

    def update_goal(
        self, pool: Any, user: Any, goal_id: UUID, data: CareerGoalUpdate
    ) -> CareerGoalRecord:
        self.calls.append(("update", pool, user, (goal_id, data)))
        return self._answer()

    def archive_goal(self, pool: Any, user: Any, goal_id: UUID) -> CareerGoalRecord:
        self.calls.append(("archive", pool, user, goal_id))
        return self._answer()


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> FakeService:
    fake = FakeService()
    monkeypatch.setattr(goals_api.career_goal_service, "create_goal", fake.create_goal)
    monkeypatch.setattr(goals_api.career_goal_service, "get_active_goal", fake.get_active_goal)
    monkeypatch.setattr(goals_api.career_goal_service, "update_goal", fake.update_goal)
    monkeypatch.setattr(goals_api.career_goal_service, "archive_goal", fake.archive_goal)
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


def test_the_resources_offer_exactly_the_agreed_methods() -> None:
    paths = main.app.openapi()["paths"]

    assert sorted(paths[COLLECTION]) == ["post"]
    assert sorted(paths[ACTIVE]) == ["get"]
    assert sorted(paths[ITEM_TEMPLATE]) == ["patch"]
    assert sorted(paths[ARCHIVE_TEMPLATE]) == ["post"]


def test_the_endpoints_declare_their_response_model_and_status() -> None:
    paths = main.app.openapi()["paths"]

    created = paths[COLLECTION]["post"]["responses"]
    assert "201" in created and "200" not in created
    assert created["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CareerGoalResponse"
    }
    body = paths[COLLECTION]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert body == {"$ref": "#/components/schemas/CareerGoalCreate"}
    read = paths[ACTIVE]["get"]["responses"]
    assert "200" in read
    assert read["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CareerGoalResponse"
    }
    for url, method in (
        (COLLECTION, "post"),
        (ACTIVE, "get"),
        (ITEM_TEMPLATE, "patch"),
        (ARCHIVE_TEMPLATE, "post"),
    ):
        assert paths[url][method]["security"] == [{"HTTPBearer": []}]
    for url, method in ((ITEM_TEMPLATE, "patch"), (ARCHIVE_TEMPLATE, "post")):
        responses = paths[url][method]["responses"]
        assert "200" in responses and "201" not in responses
        assert responses["200"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/CareerGoalResponse"
        }
        parameter = paths[url][method]["parameters"][0]
        assert (parameter["name"], parameter["in"], parameter["schema"]["format"]) == (
            "goal_id",
            "path",
            "uuid",
        )
    update_body = paths[ITEM_TEMPLATE]["patch"]["requestBody"]["content"]["application/json"]
    assert update_body["schema"] == {"$ref": "#/components/schemas/CareerGoalUpdate"}
    assert "requestBody" not in paths[ARCHIVE_TEMPLATE]["post"]


def test_the_handlers_are_plain_functions_so_they_run_in_the_thread_pool() -> None:
    assert not inspect.iscoroutinefunction(goals_api.create_career_goal)
    assert not inspect.iscoroutinefunction(goals_api.get_active_career_goal)
    assert not inspect.iscoroutinefunction(goals_api.update_career_goal)
    assert not inspect.iscoroutinefunction(goals_api.archive_career_goal)


def test_the_routes_are_declared_in_the_agreed_order() -> None:
    # "/active" must come before "/{goal_id}" (the literal path wins by declaration order).
    declared = [(route.path, sorted(route.methods)) for route in goals_api.router.routes]

    assert declared == [
        ("/career-goals", ["POST"]),
        ("/career-goals/active", ["GET"]),
        ("/career-goals/{goal_id}", ["PATCH"]),
        ("/career-goals/{goal_id}/archive", ["POST"]),
    ]


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
def test_other_methods_on_the_collection_answer_405_with_the_exact_allow_header(
    client: TestClient, method: str
) -> None:
    response = getattr(client, method)(COLLECTION)

    assert_error(response, 405, "METHOD_NOT_ALLOWED")
    assert response.headers["allow"] == "POST"


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_other_methods_on_the_active_goal_answer_405_with_the_exact_allow_header(
    client: TestClient, method: str
) -> None:
    # "active" also matches the pattern of PATCH /{goal_id}, so that method is listed too,
    # although PATCH /active itself is a validation error (next test).
    response = getattr(client, method)(ACTIVE)

    assert_error(response, 405, "METHOD_NOT_ALLOWED")
    assert response.headers["allow"] == "GET, PATCH"


def test_patch_on_the_active_path_is_a_validation_error_not_a_405(
    client: TestClient, service: FakeService
) -> None:
    response = client.patch(ACTIVE, json={"description": "x"})

    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert "goal_id" in {entry["field"] for entry in error["details"]}
    assert service.calls == []


@pytest.mark.parametrize("method", ["get", "post", "put", "delete"])
def test_other_methods_on_a_goal_answer_405_with_the_exact_allow_header(
    client: TestClient, method: str
) -> None:
    response = getattr(client, method)(ITEM)

    assert_error(response, 405, "METHOD_NOT_ALLOWED")
    assert response.headers["allow"] == "PATCH"


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
def test_other_methods_on_the_archive_path_answer_405_with_the_exact_allow_header(
    client: TestClient, method: str
) -> None:
    response = getattr(client, method)(ARCHIVE)

    assert_error(response, 405, "METHOD_NOT_ALLOWED")
    assert response.headers["allow"] == "POST"


def test_the_active_path_is_not_taken_for_an_id(client: TestClient, service: FakeService) -> None:
    assert client.get(ACTIVE).status_code == 200
    assert [call[0] for call in service.calls] == ["get_active"]


def test_cors_preflight_allows_the_methods_of_both_endpoints(client: TestClient) -> None:
    origin = main.settings.cors_origins[0]

    for url, method in (
        (COLLECTION, "POST"),
        (ACTIVE, "GET"),
        (ITEM, "PATCH"),
        (ARCHIVE, "POST"),
    ):
        response = client.options(
            url, headers={"Origin": origin, "Access-Control-Request-Method": method}
        )
        assert response.status_code == 200
        assert method in response.headers["access-control-allow-methods"]


# --- POST /career-goals -------------------------------------------------------------------


def test_post_creates_the_goal_and_returns_the_full_resource_with_201(
    client: TestClient, service: FakeService
) -> None:
    body = {**VALID, "description": "Prepare for a Junior DevOps Engineer position."}

    response = client.post(COLLECTION, json=body)

    assert response.status_code == 201
    assert response.json() == {
        "id": str(GOAL_ID),
        "target_role": "DevOps Engineer",
        "seniority": "junior",
        "description": "Prepare for a Junior DevOps Engineer position.",
        "status": "active",
        "created_at": "2026-11-18T09:30:00Z",
        "updated_at": "2026-11-18T09:30:00Z",
    }
    assert "user_id" not in response.json()
    assert "location" not in response.headers


def test_post_calls_the_service_with_the_user_from_the_token_and_the_validated_data(
    client: TestClient, service: FakeService
) -> None:
    client.post(COLLECTION, json={**VALID, "seniority": "senior", "description": "Goal"})

    [(name, pool, user, data)] = service.calls
    assert (name, pool, user) == ("create", POOL, USER)
    assert data == CareerGoalCreate(
        target_role="DevOps Engineer", seniority="senior", description="Goal"
    )


def test_post_without_a_description_stores_null(client: TestClient, service: FakeService) -> None:
    service.result = record(description=None)

    response = client.post(COLLECTION, json=VALID)

    assert response.status_code == 201
    assert response.json()["description"] is None
    assert service.calls[0][3].description is None


def test_post_normalises_the_input_before_the_service_sees_it(
    client: TestClient, service: FakeService
) -> None:
    client.post(
        COLLECTION, json={"target_role": "  SRE  ", "seniority": "mid", "description": "   "}
    )

    data = service.calls[0][3]
    assert (data.target_role, data.description) == ("SRE", None)


@pytest.mark.parametrize("seniority", ["junior", "mid", "senior"])
def test_post_accepts_every_seniority(
    client: TestClient, service: FakeService, seniority: str
) -> None:
    assert client.post(COLLECTION, json={**VALID, "seniority": seniority}).status_code == 201


def test_post_accepts_the_boundary_lengths(client: TestClient, service: FakeService) -> None:
    for role, description in (("R", "D"), ("r" * 100, "d" * 2000), ("ł" * 100, "ż" * 2000)):
        body = {**VALID, "target_role": role, "description": description}
        assert client.post(COLLECTION, json=body).status_code == 201


def test_the_timestamps_are_rendered_in_utc(client: TestClient, service: FakeService) -> None:
    local = timezone(timedelta(hours=2))
    service.result = record(created_at=T0.astimezone(local), updated_at=T0.astimezone(local))

    body = client.post(COLLECTION, json=VALID).json()

    assert body["created_at"] == body["updated_at"] == "2026-11-18T09:30:00Z"


def test_post_answers_409_when_an_active_goal_exists(
    client: TestClient, service: FakeService
) -> None:
    service.result = ActiveGoalExistsError()

    error = assert_error(client.post(COLLECTION, json=VALID), 409, "ACTIVE_GOAL_EXISTS")

    assert "details" not in error


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "target_role"),
        ({"seniority": "junior"}, "target_role"),
        ({"target_role": "SRE"}, "seniority"),
        ({**VALID, "target_role": ""}, "target_role"),
        ({**VALID, "target_role": "   "}, "target_role"),
        ({**VALID, "target_role": "r" * 101}, "target_role"),
        ({**VALID, "target_role": None}, "target_role"),
        ({**VALID, "target_role": 5}, "target_role"),
        ({**VALID, "target_role": "a\u0000b"}, "target_role"),
        ({**VALID, "seniority": "lead"}, "seniority"),
        ({**VALID, "seniority": "Junior"}, "seniority"),
        ({**VALID, "seniority": None}, "seniority"),
        ({**VALID, "description": "d" * 2001}, "description"),
        ({**VALID, "description": 5}, "description"),
        ({**VALID, "description": "a\u0000b"}, "description"),
        ({**VALID, "user_id": CANARY}, "user_id"),
        ({**VALID, "id": CANARY}, "id"),
        ({**VALID, "status": CANARY}, "status"),
        ({**VALID, "status": "archived"}, "status"),
        ({**VALID, "created_at": CANARY}, "created_at"),
        ({**VALID, "unknown": CANARY}, "unknown"),
    ],
)
def test_post_validation_errors_are_422_and_never_reach_the_service(
    client: TestClient, service: FakeService, body: dict[str, Any], field: str
) -> None:
    response = client.post(COLLECTION, json=body)

    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert field in {entry["field"] for entry in error["details"]}
    assert all(set(entry) == {"field", "message"} for entry in error["details"])
    assert CANARY not in response.text
    assert service.calls == []


@pytest.mark.parametrize("content", ["not json", '{"target_role": ', "[]", '"text"', "42"])
def test_post_with_a_body_that_is_not_a_json_object_is_422(
    client: TestClient, service: FakeService, content: str
) -> None:
    response = client.post(
        COLLECTION, content=content, headers={"Content-Type": "application/json"}
    )

    assert_error(response, 422, "VALIDATION_ERROR")
    assert service.calls == []


def test_post_without_a_body_is_422(client: TestClient, service: FakeService) -> None:
    assert_error(client.post(COLLECTION), 422, "VALIDATION_ERROR")
    assert service.calls == []


# --- GET /career-goals/active -------------------------------------------------------------


def test_get_returns_the_full_active_goal(client: TestClient, service: FakeService) -> None:
    response = client.get(ACTIVE)

    assert response.status_code == 200
    assert response.json() == {
        "id": str(GOAL_ID),
        "target_role": "DevOps Engineer",
        "seniority": "junior",
        "description": "Prepare for a Junior DevOps Engineer position.",
        "status": "active",
        "created_at": "2026-11-18T09:30:00Z",
        "updated_at": "2026-11-18T09:30:00Z",
    }
    assert "user_id" not in response.json()


def test_get_calls_the_service_with_the_user_from_the_token_and_the_pool(
    client: TestClient, service: FakeService
) -> None:
    client.get(ACTIVE)

    assert service.calls == [("get_active", POOL, USER, None)]


def test_get_cannot_be_pointed_at_another_user_by_the_request(
    client: TestClient, service: FakeService
) -> None:
    other = "99999999-9999-9999-9999-999999999999"

    client.get(ACTIVE, params={"user_id": other}, headers={"X-User-Id": other})

    assert service.calls == [("get_active", POOL, USER, None)]


def test_get_answers_404_when_there_is_no_active_goal(
    client: TestClient, service: FakeService
) -> None:
    service.result = NoActiveGoalError()

    error = assert_error(client.get(ACTIVE), 404, "NO_ACTIVE_GOAL")

    assert "details" not in error


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
    ("method", "url", "kwargs"),
    [
        ("post", COLLECTION, {"json": VALID}),
        ("get", ACTIVE, {}),
        ("patch", ITEM, {"json": {"description": "x"}}),
        ("post", ARCHIVE, {}),
    ],
    ids=["POST", "GET", "PATCH", "ARCHIVE"],
)
def test_without_valid_credentials_both_endpoints_answer_401_and_do_not_call_the_service(
    unauthenticated: TestClient,
    service: FakeService,
    method: str,
    url: str,
    kwargs: dict[str, Any],
    headers: dict[str, str],
) -> None:
    response = getattr(unauthenticated, method)(url, headers=headers, **kwargs)

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert service.calls == []


def test_authentication_comes_before_body_validation(
    unauthenticated: TestClient, service: FakeService
) -> None:
    response = unauthenticated.post(COLLECTION, json={"user_id": CANARY})

    assert_error(response, 401, "UNAUTHORIZED")  # not 422: the caller is not identified yet
    assert CANARY not in response.text
    assert service.calls == []


# --- infrastructure failures --------------------------------------------------------------


def test_a_service_that_cannot_reach_the_database_answers_503(
    client: TestClient, service: FakeService
) -> None:
    service.result = DatabaseUnavailableError("database unavailable (PoolTimeout)")

    assert_error(client.get(ACTIVE), 503, "SERVICE_UNAVAILABLE")
    assert_error(client.post(COLLECTION, json=VALID), 503, "SERVICE_UNAVAILABLE")
    assert_error(client.patch(ITEM, json={"description": "x"}), 503, "SERVICE_UNAVAILABLE")
    assert_error(client.post(ARCHIVE), 503, "SERVICE_UNAVAILABLE")


def test_a_query_timeout_answers_504(client: TestClient, service: FakeService) -> None:
    service.result = pg_errors.QueryCanceled("canceling statement due to statement timeout")

    assert_error(client.get(ACTIVE), 504, "DATABASE_TIMEOUT")
    assert_error(client.patch(ITEM, json={"description": "x"}), 504, "DATABASE_TIMEOUT")
    assert_error(client.post(ARCHIVE), 504, "DATABASE_TIMEOUT")


def test_a_user_that_no_longer_exists_answers_401(client: TestClient, service: FakeService) -> None:
    # career_goals.user_id -> auth.users(id) fails when the user of a valid token was deleted.
    class _Diag:
        constraint_name = "career_goals_user_id_fkey"

    class _ForeignKeyViolation(pg_errors.ForeignKeyViolation):
        @property
        def diag(self) -> Any:  # psycopg builds diag from a server result; fake it
            return _Diag()

    service.result = _ForeignKeyViolation(f"violates foreign key {CANARY}")

    response = client.post(COLLECTION, json=VALID)

    assert_error(response, 401, "UNAUTHORIZED")
    assert response.headers["www-authenticate"] == "Bearer"
    assert CANARY not in response.text


def test_an_unexpected_database_error_answers_500_without_details(
    client: TestClient, service: FakeService, caplog: pytest.LogCaptureFixture
) -> None:
    service.result = pg_errors.UniqueViolation(f"duplicate key value {CANARY}")

    with caplog.at_level(logging.DEBUG):
        response = client.post(COLLECTION, json=VALID)

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
        client = TestClient(main.app, raise_server_exceptions=False)
        created = client.post(COLLECTION, json=VALID)
        read = client.get(ACTIVE)
        patched = client.patch(ITEM, json={"description": "x"})
        archived = client.post(ARCHIVE)
    finally:
        main.app.dependency_overrides.pop(get_current_user, None)

    assert_error(created, 503, "SERVICE_UNAVAILABLE")
    assert_error(read, 503, "SERVICE_UNAVAILABLE")
    assert_error(patched, 503, "SERVICE_UNAVAILABLE")
    assert_error(archived, 503, "SERVICE_UNAVAILABLE")


# --- PATCH /career-goals/{goal_id} ----------------------------------------------------------


def test_patch_returns_the_updated_goal_with_200(client: TestClient, service: FakeService) -> None:
    service.result = record(target_role="SRE", seniority="senior", description=None)

    response = client.patch(ITEM, json={"target_role": "SRE"})

    assert response.status_code == 200
    assert response.json() == {
        "id": str(GOAL_ID),
        "target_role": "SRE",
        "seniority": "senior",
        "description": None,
        "status": "active",
        "created_at": "2026-11-18T09:30:00Z",
        "updated_at": "2026-11-18T09:30:00Z",
    }
    assert "user_id" not in response.json()
    assert "location" not in response.headers


def test_patch_calls_the_service_with_the_token_user_the_path_id_and_the_data(
    client: TestClient, service: FakeService
) -> None:
    client.patch(ITEM, json={"target_role": "SRE", "seniority": "mid"})

    [(name, pool, user, (goal_id, data))] = service.calls
    assert (name, pool, user, goal_id) == ("update", POOL, USER, GOAL_ID)
    assert data == CareerGoalUpdate(target_role="SRE", seniority="mid")


def test_patch_cannot_be_pointed_at_another_user_by_the_request(
    client: TestClient, service: FakeService
) -> None:
    other = "99999999-9999-9999-9999-999999999999"

    client.patch(
        ITEM, json={"description": "x"}, params={"user_id": other}, headers={"X-User-Id": other}
    )

    assert service.calls[0][2] == USER


@pytest.mark.parametrize(
    ("body", "changes"),
    [
        ({"target_role": "SRE"}, {"target_role": "SRE"}),
        ({"seniority": "senior"}, {"seniority": "senior"}),
        ({"description": "New"}, {"description": "New"}),
        ({"description": None}, {"description": None}),
        ({"description": "   "}, {"description": None}),
        ({"target_role": "  SRE  "}, {"target_role": "SRE"}),
        (
            {"target_role": "SRE", "seniority": "mid", "description": "D"},
            {"target_role": "SRE", "seniority": "mid", "description": "D"},
        ),
    ],
    ids=[
        "role-only",
        "seniority-only",
        "description-only",
        "null-clears-description",
        "blank-clears-description",
        "stripped",
        "all-fields",
    ],
)
def test_patch_forwards_exactly_the_fields_that_were_sent(
    client: TestClient, service: FakeService, body: dict[str, Any], changes: dict[str, Any]
) -> None:
    assert client.patch(ITEM, json=body).status_code == 200

    assert service.calls[0][3][1].changes() == changes  # omitted fields are not in the dict


@pytest.mark.parametrize(
    "goal_id",
    [str(GOAL_ID).upper(), GOAL_ID.hex, f"{{{GOAL_ID}}}", f"urn:uuid:{GOAL_ID}"],
    ids=["upper-case", "no-hyphens", "braces", "urn"],
)
def test_patch_hands_the_service_a_normalised_uuid_whatever_form_was_sent(
    client: TestClient, service: FakeService, goal_id: str
) -> None:
    response = client.patch(f"{COLLECTION}/{goal_id}", json={"description": "x"})

    assert response.status_code == 200
    assert service.calls[0][3][0] == GOAL_ID  # a UUID object, equal whatever the spelling


def test_patch_answers_404_for_a_missing_or_foreign_goal(
    client: TestClient, service: FakeService
) -> None:
    service.result = GoalNotFoundError()

    error = assert_error(client.patch(ITEM, json={"description": "x"}), 404, "GOAL_NOT_FOUND")

    assert "details" not in error


def test_patch_answers_409_for_an_archived_goal(client: TestClient, service: FakeService) -> None:
    service.result = GoalArchivedError()

    error = assert_error(client.patch(ITEM, json={"description": "x"}), 409, "GOAL_ARCHIVED")

    assert "details" not in error


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "body"),
        ({"target_role": None}, "target_role"),
        ({"seniority": None}, "seniority"),
        ({"target_role": ""}, "target_role"),
        ({"target_role": "   "}, "target_role"),
        ({"target_role": "r" * 101}, "target_role"),
        ({"target_role": 5}, "target_role"),
        ({"target_role": "a\u0000b"}, "target_role"),
        ({"seniority": "lead"}, "seniority"),
        ({"seniority": "Junior"}, "seniority"),
        ({"description": "d" * 2001}, "description"),
        ({"description": 5}, "description"),
        ({"description": "a\u0000b"}, "description"),
        ({"description": "x", "user_id": CANARY}, "user_id"),
        ({"description": "x", "id": CANARY}, "id"),
        ({"description": "x", "status": "archived"}, "status"),
        ({"description": "x", "created_at": CANARY}, "created_at"),
        ({"description": "x", "updated_at": CANARY}, "updated_at"),
        ({"description": "x", "unknown": CANARY}, "unknown"),
    ],
)
def test_patch_validation_errors_are_422_and_never_reach_the_service(
    client: TestClient, service: FakeService, body: dict[str, Any], field: str
) -> None:
    response = client.patch(ITEM, json=body)

    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert field in {entry["field"] for entry in error["details"]}
    assert all(set(entry) == {"field", "message"} for entry in error["details"])
    assert CANARY not in response.text
    assert service.calls == []


@pytest.mark.parametrize("content", ["not json", '{"description": ', "[]", '"text"', "42"])
def test_patch_with_a_body_that_is_not_a_json_object_is_422(
    client: TestClient, service: FakeService, content: str
) -> None:
    response = client.patch(ITEM, content=content, headers={"Content-Type": "application/json"})

    assert_error(response, 422, "VALIDATION_ERROR")
    assert service.calls == []


def test_patch_without_a_body_is_422(client: TestClient, service: FakeService) -> None:
    assert_error(client.patch(ITEM), 422, "VALIDATION_ERROR")
    assert service.calls == []


@pytest.mark.parametrize(
    "goal_id", ["not-a-uuid", "123", "active", GOAL_ID.hex[:-1], f"{GOAL_ID}0"]
)
def test_patch_with_an_invalid_uuid_is_422(
    client: TestClient, service: FakeService, goal_id: str
) -> None:
    response = client.patch(f"{COLLECTION}/{goal_id}", json={"description": "x"})

    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert "goal_id" in {entry["field"] for entry in error["details"]}
    assert service.calls == []


# --- POST /career-goals/{goal_id}/archive ---------------------------------------------------


def test_archive_returns_the_archived_goal_with_200(
    client: TestClient, service: FakeService
) -> None:
    service.result = record(status="archived")

    response = client.post(ARCHIVE)

    assert response.status_code == 200
    assert response.json() == {
        "id": str(GOAL_ID),
        "target_role": "DevOps Engineer",
        "seniority": "junior",
        "description": "Prepare for a Junior DevOps Engineer position.",
        "status": "archived",
        "created_at": "2026-11-18T09:30:00Z",
        "updated_at": "2026-11-18T09:30:00Z",
    }
    assert "user_id" not in response.json()
    assert "location" not in response.headers


def test_archive_calls_the_service_with_the_token_user_and_the_path_id(
    client: TestClient, service: FakeService
) -> None:
    client.post(ARCHIVE)

    assert service.calls == [("archive", POOL, USER, GOAL_ID)]


def test_archive_cannot_be_pointed_at_another_user_by_the_request(
    client: TestClient, service: FakeService
) -> None:
    other = "99999999-9999-9999-9999-999999999999"

    client.post(ARCHIVE, params={"user_id": other}, headers={"X-User-Id": other})

    assert service.calls == [("archive", POOL, USER, GOAL_ID)]


def test_archive_needs_no_body_and_ignores_one(client: TestClient, service: FakeService) -> None:
    assert client.post(ARCHIVE, json={"status": "active"}).status_code == 200
    assert client.post(ARCHIVE).status_code == 200
    assert [call[0] for call in service.calls] == ["archive", "archive"]


def test_archive_answers_404_for_a_missing_or_foreign_goal(
    client: TestClient, service: FakeService
) -> None:
    service.result = GoalNotFoundError()

    error = assert_error(client.post(ARCHIVE), 404, "GOAL_NOT_FOUND")

    assert "details" not in error


def test_archive_answers_409_for_an_already_archived_goal(
    client: TestClient, service: FakeService
) -> None:
    service.result = GoalArchivedError()

    error = assert_error(client.post(ARCHIVE), 409, "GOAL_ARCHIVED")

    assert "details" not in error


@pytest.mark.parametrize(
    "goal_id", ["not-a-uuid", "123", "active", GOAL_ID.hex[:-1], f"{GOAL_ID}0"]
)
def test_archive_with_an_invalid_uuid_is_422(
    client: TestClient, service: FakeService, goal_id: str
) -> None:
    response = client.post(f"{COLLECTION}/{goal_id}/archive")

    error = assert_error(response, 422, "VALIDATION_ERROR")
    assert "goal_id" in {entry["field"] for entry in error["details"]}
    assert service.calls == []


def test_authentication_comes_before_path_and_body_validation_on_the_new_endpoints(
    unauthenticated: TestClient, service: FakeService
) -> None:
    patched = unauthenticated.patch(f"{COLLECTION}/not-a-uuid", json={"user_id": CANARY})
    archived = unauthenticated.post(f"{COLLECTION}/not-a-uuid/archive")

    assert_error(patched, 401, "UNAUTHORIZED")
    assert_error(archived, 401, "UNAUTHORIZED")
    assert CANARY not in patched.text
    assert service.calls == []
