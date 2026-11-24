"""The OpenAPI document must describe the API that really runs.

No database and no network: the document is built in memory (`app.openapi()` does not start the
lifespan) and the runtime checks use the same fake services as the other API tests.

Two kinds of checks:
- static: the document has the structure and content agreed for it;
- conformance: what the real application sends validates against the models the document
  points to, and the documented headers really are sent.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from psycopg import errors as pg_errors

import app.api.career_goals as goals_api
import app.api.profile as profile_api
from app import main
from app.api.deps import get_current_user, get_db_pool
from app.core.errors import (
    ActiveGoalExistsError,
    AppError,
    GoalArchivedError,
    GoalNotFoundError,
    NoActiveGoalError,
    ProfileNotFoundError,
)
from app.db.errors import DatabaseUnavailableError
from app.models.auth import AuthenticatedUser
from app.schemas.career_goal import CareerGoalCreate, CareerGoalResponse, CareerGoalUpdate
from app.schemas.errors import ErrorResponse, ValidationErrorResponse
from app.schemas.profile import ProfileResponse, ProfileUpdate

SPEC: dict[str, Any] = main.app.openapi()
PATHS: dict[str, Any] = SPEC["paths"]

PROFILE = "/api/v1/profile"
GOALS = "/api/v1/career-goals"
ACTIVE = f"{GOALS}/active"
ITEM = f"{GOALS}/{{goal_id}}"
ARCHIVE = f"{GOALS}/{{goal_id}}/archive"
HEALTH = "/api/v1/health"

# (method, path) -> status -> the domain error codes documented for it (None: no domain code).
INFRA: dict[str, set[str] | None] = {
    "401": {"UNAUTHORIZED"},
    "503": {"SERVICE_UNAVAILABLE"},
    "504": {"DATABASE_TIMEOUT"},
}
EXPECTED: dict[tuple[str, str], dict[str, set[str] | None]] = {
    ("get", HEALTH): {"200": None},
    ("get", PROFILE): {"200": None, "404": {"PROFILE_NOT_FOUND"}, **INFRA},
    ("patch", PROFILE): {"200": None, "404": {"PROFILE_NOT_FOUND"}, "422": None, **INFRA},
    ("post", GOALS): {"201": None, "409": {"ACTIVE_GOAL_EXISTS"}, "422": None, **INFRA},
    ("get", ACTIVE): {"200": None, "404": {"NO_ACTIVE_GOAL"}, **INFRA},
    ("patch", ITEM): {
        "200": None,
        "404": {"GOAL_NOT_FOUND"},
        "409": {"GOAL_ARCHIVED"},
        "422": None,
        **INFRA,
    },
    ("post", ARCHIVE): {
        "200": None,
        "404": {"GOAL_NOT_FOUND"},
        "409": {"GOAL_ARCHIVED"},
        "422": None,
        **INFRA,
    },
}
OPERATIONS = sorted(EXPECTED)


def operation(method: str, path: str) -> dict[str, Any]:
    return PATHS[path][method]


def media(response: dict[str, Any]) -> dict[str, Any]:
    return response["content"]["application/json"]


def ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


def all_subclasses(cls: type[AppError]) -> Iterator[type[AppError]]:
    for sub in cls.__subclasses__():
        yield sub
        yield from all_subclasses(sub)


# --- static checks of the document ------------------------------------------------------------


def test_exactly_the_agreed_operations_are_documented() -> None:
    documented = {(m, p) for p, ops in PATHS.items() for m in ops}
    assert documented == set(EXPECTED)


@pytest.mark.parametrize(("method", "path"), OPERATIONS)
def test_each_operation_documents_exactly_the_agreed_statuses(method: str, path: str) -> None:
    assert set(operation(method, path)["responses"]) == set(EXPECTED[(method, path)])


@pytest.mark.parametrize(("method", "path"), OPERATIONS)
def test_error_responses_use_the_unified_models(method: str, path: str) -> None:
    for status, codes in EXPECTED[(method, path)].items():
        if int(status) < 400:
            continue
        response = operation(method, path)["responses"][status]
        model = "ValidationErrorResponse" if status == "422" else "ErrorResponse"
        assert media(response)["schema"] == ref(model), status
        documented = {ex["value"]["error"]["code"] for ex in media(response)["examples"].values()}
        if codes is not None:
            assert documented == codes, status


def test_fastapis_default_validation_error_is_gone() -> None:
    schemas = SPEC["components"]["schemas"]
    assert "HTTPValidationError" not in schemas
    assert "ValidationError" not in schemas
    assert {"ErrorResponse", "ValidationErrorResponse"} <= set(schemas)


def test_the_bearer_scheme_keeps_its_name_and_describes_the_token() -> None:
    scheme = SPEC["components"]["securitySchemes"]["HTTPBearer"]
    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"
    assert scheme["bearerFormat"] == "JWT"
    assert scheme["description"]


@pytest.mark.parametrize(("method", "path"), OPERATIONS)
def test_only_the_health_check_is_public(method: str, path: str) -> None:
    security = operation(method, path).get("security")
    assert security == (None if path == HEALTH else [{"HTTPBearer": []}])


@pytest.mark.parametrize(("method", "path"), [op for op in OPERATIONS if op[1] != HEALTH])
def test_a_401_documents_the_www_authenticate_header(method: str, path: str) -> None:
    headers = operation(method, path)["responses"]["401"]["headers"]
    assert headers["WWW-Authenticate"]["schema"]["type"] == "string"


def test_only_the_career_goal_401_mentions_a_user_that_no_longer_exists() -> None:
    def description(path: str, method: str) -> str:
        return operation(method, path)["responses"]["401"]["description"]

    assert "no longer exists" not in description(PROFILE, "get")
    assert "no longer exists" not in description(PROFILE, "patch")
    for method, path in (("post", GOALS), ("get", ACTIVE), ("patch", ITEM), ("post", ARCHIVE)):
        assert "no longer exists" in description(path, method)


def test_the_health_check_answers_only_200_with_a_typed_body() -> None:
    health = operation("get", HEALTH)
    assert set(health["responses"]) == {"200"}
    assert media(health["responses"]["200"])["schema"] == ref("HealthResponse")
    assert health["summary"] == "Liveness check"
    assert "database" in health["description"].lower()


def test_operation_ids_are_unchanged_and_unique() -> None:
    ids = [operation(m, p)["operationId"] for m, p in OPERATIONS]
    assert len(set(ids)) == len(ids)
    assert sorted(ids) == sorted(
        [
            "health_api_v1_health_get",
            "get_profile_api_v1_profile_get",
            "update_profile_api_v1_profile_patch",
            "create_career_goal_api_v1_career_goals_post",
            "get_active_career_goal_api_v1_career_goals_active_get",
            "update_career_goal_api_v1_career_goals__goal_id__patch",
            "archive_career_goal_api_v1_career_goals__goal_id__archive_post",
        ]
    )


def test_every_tag_is_declared_with_a_description() -> None:
    declared = {t["name"]: t.get("description") for t in SPEC["tags"]}
    used = {tag for m, p in OPERATIONS for tag in operation(m, p)["tags"]}
    assert used <= set(declared)
    assert all(declared.values())


@pytest.mark.parametrize(("method", "path"), OPERATIONS)
def test_every_operation_has_a_summary_and_a_description(method: str, path: str) -> None:
    op = operation(method, path)
    assert op["summary"].strip()
    assert op["description"].strip()


def test_the_description_of_the_api_covers_errors_authentication_and_405() -> None:
    text = SPEC["info"]["description"]
    for expected in (
        "Bearer",
        "WWW-Authenticate",
        "request_id",
        "405",
        "Allow",
        "METHOD_NOT_ALLOWED",
    ):
        assert expected in text


# Competency errors that exist before their endpoints (Stage 9.2a). Remove them from this set when
# the endpoints are documented (Stage 9.2e); the test then covers them again.
NOT_EXPOSED_YET = {"COMPETENCY_LIMIT_REACHED", "COMPETENCY_NAME_EXISTS"}


def test_every_domain_error_is_documented_by_some_operation() -> None:
    documented = {
        ex["value"]["error"]["code"]
        for op in (operation(m, p) for m, p in OPERATIONS)
        for response in op["responses"].values()
        for ex in response.get("content", {})
        .get("application/json", {})
        .get("examples", {})
        .values()
    }
    domain = {cls.code for cls in all_subclasses(AppError)} - NOT_EXPOSED_YET
    assert domain <= documented, f"undocumented error codes: {sorted(domain - documented)}"


def test_every_example_in_the_document_validates_against_its_model() -> None:
    for method, path in OPERATIONS:
        for status, response in operation(method, path)["responses"].items():
            examples = response.get("content", {}).get("application/json", {}).get("examples", {})
            model = ValidationErrorResponse if status == "422" else ErrorResponse
            for example in examples.values():
                model.model_validate(example["value"])
    for model in (
        CareerGoalCreate,
        CareerGoalUpdate,
        CareerGoalResponse,
        ProfileUpdate,
        ProfileResponse,
    ):
        for example in model.model_json_schema()["examples"]:
            model.model_validate(example)


# --- conformance: what the real application sends --------------------------------------------

USER = AuthenticatedUser(id=UUID("11111111-1111-1111-1111-111111111111"))
GOAL_ID = UUID("7c9e6679-7425-40de-944b-e07fc1f90ae7")
T0 = datetime(2026, 11, 18, 9, 30, tzinfo=UTC)


class Raises:
    """A service function that raises what the test sets."""

    def __init__(self) -> None:
        self.exc: BaseException = RuntimeError("not set")

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise self.exc


@pytest.fixture
def failing(monkeypatch: pytest.MonkeyPatch) -> Raises:
    raises = Raises()
    for module, names in (
        (profile_api.profile_service, ("get_profile", "update_profile")),
        (
            goals_api.career_goal_service,
            ("create_goal", "get_active_goal", "update_goal", "archive_goal"),
        ),
    ):
        for name in names:
            monkeypatch.setattr(module, name, raises)
    return raises


@pytest.fixture
def client(failing: Raises) -> Iterator[TestClient]:
    main.app.dependency_overrides[get_current_user] = lambda: USER
    main.app.dependency_overrides[get_db_pool] = lambda: object()
    try:
        yield TestClient(main.app, raise_server_exceptions=False)
    finally:
        main.app.dependency_overrides.pop(get_current_user, None)
        main.app.dependency_overrides.pop(get_db_pool, None)


REQUESTS: dict[tuple[str, str], tuple[str, dict[str, Any] | None]] = {
    ("get", PROFILE): (PROFILE, None),
    ("patch", PROFILE): (PROFILE, {"display_name": "Ann"}),
    ("post", GOALS): (GOALS, {"target_role": "DevOps Engineer", "seniority": "junior"}),
    ("get", ACTIVE): (ACTIVE, None),
    ("patch", ITEM): (f"{GOALS}/{GOAL_ID}", {"description": "x"}),
    ("post", ARCHIVE): (f"{GOALS}/{GOAL_ID}/archive", None),
}

# Status -> (exception the service raises, the code the documented response promises).
FAILURES: dict[str, tuple[BaseException, str]] = {
    "503": (DatabaseUnavailableError("database unavailable (PoolTimeout)"), "SERVICE_UNAVAILABLE"),
    "504": (pg_errors.QueryCanceled("statement timeout"), "DATABASE_TIMEOUT"),
}
DOMAIN = {
    ("get", PROFILE, "404"): ProfileNotFoundError(),
    ("patch", PROFILE, "404"): ProfileNotFoundError(),
    ("post", GOALS, "409"): ActiveGoalExistsError(),
    ("get", ACTIVE, "404"): NoActiveGoalError(),
    ("patch", ITEM, "404"): GoalNotFoundError(),
    ("patch", ITEM, "409"): GoalArchivedError(),
    ("post", ARCHIVE, "404"): GoalNotFoundError(),
    ("post", ARCHIVE, "409"): GoalArchivedError(),
}


def send(client: TestClient, method: str, path: str) -> Any:
    url, body = REQUESTS[(method, path)]
    return client.request(method.upper(), url, json=body)


@pytest.mark.parametrize(("method", "path"), sorted(REQUESTS))
@pytest.mark.parametrize("status", ["503", "504"])
def test_real_infrastructure_errors_match_the_documented_ones(
    client: TestClient, failing: Raises, method: str, path: str, status: str
) -> None:
    assert status in operation(method, path)["responses"]
    failing.exc, code = FAILURES[status]

    response = send(client, method, path)

    assert response.status_code == int(status)
    assert ErrorResponse.model_validate(response.json()).error.code == code


@pytest.mark.parametrize(("method", "path", "status"), sorted(DOMAIN))
def test_real_domain_errors_match_the_documented_ones(
    client: TestClient, failing: Raises, method: str, path: str, status: str
) -> None:
    failing.exc = DOMAIN[(method, path, status)]
    documented = operation(method, path)["responses"][status]

    response = send(client, method, path)

    assert response.status_code == int(status)
    body = ErrorResponse.model_validate(response.json())
    assert body.error.code in {
        ex["value"]["error"]["code"] for ex in media(documented)["examples"].values()
    }


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("patch", PROFILE, {}),
        ("post", GOALS, {"seniority": "junior"}),
        ("patch", f"{GOALS}/{GOAL_ID}", {"unknown": 1}),
        ("post", f"{GOALS}/not-a-uuid/archive", None),
    ],
)
def test_real_422_matches_the_validation_error_model(
    client: TestClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    response = client.request(method.upper(), path, json=body)

    assert response.status_code == 422
    model = ValidationErrorResponse.model_validate(response.json())
    assert model.error.code == "VALIDATION_ERROR"
    assert model.error.details


@pytest.mark.parametrize(("method", "path"), sorted(REQUESTS))
def test_real_401_sends_the_documented_header(method: str, path: str) -> None:
    # No dependency overrides: the real authentication dependency rejects the request first.
    response = TestClient(main.app, raise_server_exceptions=False).request(
        method.upper(), REQUESTS[(method, path)][0], json=REQUESTS[(method, path)][1]
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert ErrorResponse.model_validate(response.json()).error.code == "UNAUTHORIZED"


def test_the_405_promised_in_the_description_is_real() -> None:
    response = TestClient(main.app, raise_server_exceptions=False).delete(PROFILE)

    assert response.status_code == 405
    assert response.headers["Allow"] == "GET, PATCH"
    assert ErrorResponse.model_validate(response.json()).error.code == "METHOD_NOT_ALLOWED"
