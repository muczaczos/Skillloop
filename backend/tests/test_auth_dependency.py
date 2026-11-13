"""HTTP-level tests for the get_current_user dependency.

A throwaway FastAPI app with one protected route is built here, so no endpoint outside
API-DESIGN.md is added to the real application.
"""

import logging
import time
import uuid
from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import (
    AUTH_UNAVAILABLE_DETAIL,
    UNAUTHORIZED_DETAIL,
    CurrentUser,
    get_token_verifier,
)
from tests.auth_helpers import (
    JWKSServer,
    SigningKey,
    default_claims,
    make_token,
    make_verifier,
    running_jwks_server,
)


def build_app(jwks_url: str) -> FastAPI:
    app = FastAPI()

    @app.get("/protected")
    def protected(user: CurrentUser) -> dict[str, str]:
        return {"user_id": str(user.id)}

    verifier = make_verifier(jwks_url)
    app.dependency_overrides[get_token_verifier] = lambda: verifier
    return app


@pytest.fixture
def client(jwks_server: JWKSServer) -> Iterator[TestClient]:
    with TestClient(build_app(jwks_server.url)) as test_client:
        yield test_client


def assert_unauthorized(response: httpx.Response) -> None:
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"detail": UNAUTHORIZED_DETAIL}


def test_valid_token_authenticates_user(client: TestClient, signing_key: SigningKey) -> None:
    user_id = uuid.uuid4()
    token = make_token(signing_key, default_claims(sub=str(user_id)))

    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"user_id": str(user_id)}


def test_missing_authorization_header_returns_401(client: TestClient) -> None:
    assert_unauthorized(client.get("/protected"))


@pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", "Bearer", "Bearer ", "Token abc"])
def test_wrong_scheme_or_empty_token_returns_401(client: TestClient, header: str) -> None:
    assert_unauthorized(client.get("/protected", headers={"Authorization": header}))


def test_invalid_tokens_return_identical_401_responses(
    client: TestClient, signing_key: SigningKey
) -> None:
    expired = make_token(signing_key, default_claims(exp=int(time.time()) - 120))
    wrong_audience = make_token(signing_key, default_claims(aud="other"))
    foreign_signature = make_token(SigningKey(kid=signing_key.kid))

    for token in (expired, wrong_audience, foreign_signature, "garbage"):
        response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        # The response must not reveal which check failed.
        assert_unauthorized(response)


def test_rejected_token_is_not_logged(
    client: TestClient, signing_key: SigningKey, caplog: pytest.LogCaptureFixture
) -> None:
    token = make_token(signing_key, default_claims(exp=int(time.time()) - 120))
    header, payload, signature = token.split(".")

    with caplog.at_level(logging.DEBUG):
        response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert caplog.records, "a rejection should be logged"
    for fragment in (header, payload, signature):
        assert fragment not in caplog.text


def test_unreachable_jwks_returns_503(signing_key: SigningKey) -> None:
    with running_jwks_server() as server:
        url = server.url

    with TestClient(build_app(url)) as client:
        token = make_token(signing_key)
        response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 503
    assert response.json() == {"detail": AUTH_UNAVAILABLE_DETAIL}
