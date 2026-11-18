"""The Allow header of a 405 lists ALL methods the path supports (RFC 9110, 15.5.6).

Starlette itself names only the methods of the first matching route, so the answer depended
on the order in which the routes were declared. These tests pin the corrected behaviour on the
real application and on small applications that reproduce the layouts of the project.
"""

import asyncio
import copy
import itertools

import pytest
from fastapi import APIRouter, FastAPI
from fastapi import HTTPException as FastAPIHTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import main
from app.api import errors as api_errors
from app.api.errors import register_error_handlers


def ok() -> dict[str, str]:
    return {}


def build_app(*routes: tuple[str, str, int | None]) -> FastAPI:
    """An app with the central handlers and one route per (path, method, status_to_raise).
    A route with a status raises HTTPException(status) instead of answering."""
    app = FastAPI()
    register_error_handlers(app)
    for path, method, raises in routes:

        def endpoint(raises: int | None = raises) -> dict[str, str]:
            if raises is not None:
                raise FastAPIHTTPException(status_code=raises)
            return {"ok": "yes"}

        app.add_api_route(path, endpoint, methods=[method])
    return app


def allow_of(app: FastAPI, method: str, path: str) -> str | None:
    response = TestClient(app).request(method, path)
    assert response.status_code == 405, response.text
    return response.headers.get("allow")


def fake_request(app: FastAPI, path: str, method: str = "PUT") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "root_path": "",
            "headers": [],
            "query_string": b"",
            "app": app,
        }
    )


# --- the real application -----------------------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "HEAD", "OPTIONS"])
def test_the_profile_resource_lists_both_methods(method: str) -> None:
    response = TestClient(main.app).request(method, "/api/v1/profile")

    assert response.status_code == 405
    assert response.headers["allow"] == "GET, PATCH"


def test_the_405_body_is_still_the_unified_error() -> None:
    # The header fix must not touch the body.
    body = TestClient(main.app).post("/api/v1/profile").json()

    assert set(body) == {"error"}
    assert body["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_a_single_method_resource_keeps_its_single_method() -> None:
    assert allow_of(main.app, "POST", "/api/v1/health") == "GET"


def test_head_and_options_are_never_listed() -> None:
    allow = TestClient(main.app).delete("/api/v1/profile").headers["allow"]

    assert "HEAD" not in allow and "OPTIONS" not in allow


def test_head_is_listed_only_where_a_route_serves_it() -> None:
    client = TestClient(main.app)

    docs = client.post("/docs")
    profile = client.post("/api/v1/profile")

    assert docs.status_code == 405
    assert docs.headers["allow"] == "GET, HEAD"
    assert profile.status_code == 405
    assert profile.headers["allow"] == "GET, PATCH"


# --- independence from the declaration order ------------------------------------------------


@pytest.mark.parametrize("order", list(itertools.permutations(["GET", "PATCH", "DELETE"])))
def test_the_header_does_not_depend_on_the_declaration_order(order: tuple[str, ...]) -> None:
    app = build_app(*[("/res", method, None) for method in order])

    assert allow_of(app, "POST", "/res") == "DELETE, GET, PATCH"


def test_the_methods_are_sorted_alphabetically() -> None:
    app = build_app(("/res", "PUT", None), ("/res", "POST", None), ("/res", "GET", None))

    assert allow_of(app, "DELETE", "/res") == "GET, POST, PUT"


# --- layouts of the project ---------------------------------------------------------------------


def test_nested_routers_with_prefixes_and_path_parameters() -> None:
    inner = APIRouter(prefix="/career-goals")
    inner.add_api_route("/{goal_id}/competencies", lambda goal_id: {}, methods=["GET"])
    inner.add_api_route("/{goal_id}/competencies", lambda goal_id: {}, methods=["POST"])
    inner.add_api_route("/{goal_id}/archive", lambda goal_id: {}, methods=["POST"])
    outer = APIRouter()
    outer.include_router(inner)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(outer, prefix="/api/v1")

    assert allow_of(app, "PUT", "/api/v1/career-goals/abc/competencies") == "GET, POST"
    assert allow_of(app, "GET", "/api/v1/career-goals/abc/archive") == "POST"


def test_a_literal_path_that_a_parameter_route_also_matches() -> None:
    # PATCH /cg/active does reach the /cg/{goal_id} route (goal_id="active"), so it is allowed.
    app = build_app(("/cg/active", "GET", None), ("/cg/{goal_id}", "PATCH", None))

    assert allow_of(app, "DELETE", "/cg/active") == "GET, PATCH"
    assert allow_of(app, "DELETE", "/cg/abc") == "PATCH"


def test_an_unknown_path_is_a_404_without_an_allow_header() -> None:
    response = TestClient(main.app).request("PUT", "/api/v1/nothing-here")

    assert response.status_code == 404
    assert "allow" not in response.headers


# --- fallback when the routing knows nothing --------------------------------------------------------


def test_no_methods_are_found_for_a_path_without_routes() -> None:
    assert api_errors._allowed_methods(fake_request(main.app, "/nowhere")) == []


def test_the_header_of_the_exception_is_kept_when_the_routing_knows_no_method() -> None:
    exc = FastAPIHTTPException(status_code=405, headers={"Allow": "GET, XYZ"})

    response = asyncio.run(
        api_errors.http_exception_handler(fake_request(main.app, "/nowhere"), exc)
    )

    assert response.status_code == 405
    assert response.headers["allow"] == "GET, XYZ"  # nothing invented, nothing dropped


# --- a 405 raised by the application itself ---------------------------------------------------------


def test_a_405_raised_by_a_route_gets_the_real_methods_of_the_path() -> None:
    app = FastAPI()
    register_error_handlers(app)

    def endpoint() -> None:
        raise FastAPIHTTPException(status_code=405, headers={"Allow": "OPTIONS"})

    app.add_api_route("/odd", endpoint, methods=["GET"])
    app.add_api_route("/odd", ok, methods=["PATCH"])

    response = TestClient(app).get("/odd")

    assert response.status_code == 405
    assert response.headers["allow"] == "GET, PATCH"


def test_only_one_allow_header_is_sent_whatever_the_case_of_the_original() -> None:
    app = FastAPI()
    register_error_handlers(app)

    def endpoint() -> None:
        raise FastAPIHTTPException(status_code=405, headers={"allow": "OPTIONS"})

    app.add_api_route("/odd", endpoint, methods=["GET"])

    response = TestClient(app).get("/odd")

    assert response.headers.get_list("allow") == ["GET"]


def test_the_other_forwarded_headers_of_a_405_are_kept_and_the_rest_is_dropped() -> None:
    app = FastAPI()
    register_error_handlers(app)

    def endpoint() -> None:
        raise FastAPIHTTPException(
            status_code=405,
            headers={"Allow": "X", "Retry-After": "5", "X-Internal": "secret", "Set-Cookie": "a=b"},
        )

    app.add_api_route("/odd", endpoint, methods=["GET"])

    response = TestClient(app).get("/odd")

    assert response.headers["allow"] == "GET"
    assert response.headers["retry-after"] == "5"
    assert "x-internal" not in response.headers and "set-cookie" not in response.headers


# --- other statuses are untouched ---------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429, 500, 503])
def test_an_allow_header_of_any_other_status_is_passed_on_unchanged(status: int) -> None:
    app = FastAPI()
    register_error_handlers(app)

    def endpoint() -> None:
        raise FastAPIHTTPException(status_code=status, headers={"Allow": "GET, XYZ"})

    app.add_api_route("/odd", endpoint, methods=["GET"])
    app.add_api_route("/odd", ok, methods=["PATCH"])  # the routing would say GET, PATCH

    response = TestClient(app).get("/odd")

    assert response.status_code == status
    assert response.headers["allow"] == "GET, XYZ"  # only a 405 is rebuilt


def test_no_allow_header_appears_on_other_responses() -> None:
    app = build_app(("/odd", "GET", 403), ("/odd", "PATCH", None))

    response = TestClient(app).get("/odd")

    assert response.status_code == 403
    assert "allow" not in response.headers


def test_www_authenticate_is_still_forwarded_on_a_401() -> None:
    app = FastAPI()
    register_error_handlers(app)

    def endpoint() -> None:
        raise FastAPIHTTPException(status_code=401, headers={"WWW-Authenticate": "Bearer"})

    app.add_api_route("/odd", endpoint, methods=["GET"])

    response = TestClient(app).get("/odd")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "allow" not in response.headers


# --- the scope of the request is never touched ----------------------------------------------------------


def test_probing_does_not_modify_the_scope_of_the_request() -> None:
    # PATCH is neither the first nor the last method that is probed (DELETE is first, PUT
    # last), so a probe that leaks into the scope cannot end up looking unchanged by accident.
    request = fake_request(main.app, "/api/v1/profile", method="PATCH")
    before = copy.deepcopy({k: v for k, v in request.scope.items() if k != "app"})

    first = api_errors._allowed_methods(request)
    second = api_errors._allowed_methods(request)

    assert request.scope["method"] == "PATCH"  # not left at any probed method
    assert {k: v for k, v in request.scope.items() if k != "app"} == before
    assert first == second == ["GET", "PATCH"]


def test_probing_leaves_the_routing_working() -> None:
    client = TestClient(main.app)

    for _ in range(3):
        client.delete("/api/v1/profile")  # probing happens here

    assert client.get("/api/v1/health").json() == {"status": "ok"}
