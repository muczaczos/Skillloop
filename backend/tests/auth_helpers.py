"""Test helpers for Supabase-like access tokens.

Tokens are signed with locally generated ECC P-256 keys and the public keys are served
from a local JWKS HTTP server, so tests never contact Supabase.
"""

import base64
import hashlib
import hmac
import json
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jwt import PyJWKClient
from jwt.algorithms import ECAlgorithm

from app.core.security import TokenVerifier

TEST_SUPABASE_URL = "https://test-project.supabase.co"
TEST_ISSUER = f"{TEST_SUPABASE_URL}/auth/v1"
TEST_AUDIENCE = "authenticated"
JWKS_PATH = "/auth/v1/.well-known/jwks.json"


@dataclass
class SigningKey:
    kid: str
    private_key: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1())
    )

    def public_jwk(self) -> dict[str, Any]:
        jwk = ECAlgorithm.to_jwk(self.private_key.public_key(), as_dict=True)
        return {**jwk, "kid": self.kid, "alg": "ES256", "use": "sig"}

    def public_pem(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )


def default_claims(**overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": TEST_ISSUER,
        "aud": TEST_AUDIENCE,
        "sub": str(uuid.uuid4()),
        "role": "authenticated",
        "iat": now,
        "exp": now + 3600,
        "is_anonymous": False,
        "email": "user@example.com",
    }
    claims.update(overrides)
    return claims


def make_token(
    key: SigningKey,
    claims: dict[str, Any] | None = None,
    *,
    kid: str | None = None,
    omit: tuple[str, ...] = (),
) -> str:
    """Sign an ES256 token. `kid` overrides the header key id; `omit` removes claims."""
    payload = claims if claims is not None else default_claims()
    payload = {k: v for k, v in payload.items() if k not in omit}
    return jwt.encode(
        payload,
        key.private_key,
        algorithm="ES256",
        headers={"kid": kid if kid is not None else key.kid},
    )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_json(value: dict[str, Any]) -> str:
    return _b64url(json.dumps(value, separators=(",", ":")).encode())


def make_unsigned_token(claims: dict[str, Any], *, kid: str) -> str:
    """Craft an "alg: none" token by hand (libraries refuse to produce one)."""
    header = {"alg": "none", "typ": "JWT", "kid": kid}
    return f"{_b64url_json(header)}.{_b64url_json(claims)}."


def make_hs256_token_with_public_key(key: SigningKey, claims: dict[str, Any]) -> str:
    """Craft an algorithm-confusion token: HS256 using the *public* key as HMAC secret."""
    header = {"alg": "HS256", "typ": "JWT", "kid": key.kid}
    signing_input = f"{_b64url_json(header)}.{_b64url_json(claims)}"
    signature = hmac.new(key.public_pem(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


class JWKSServer:
    """Minimal local HTTP server publishing a mutable JWKS document."""

    def __init__(self) -> None:
        self.keys: list[dict[str, Any]] = []
        self.request_count = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path != JWKS_PATH:
                    self.send_error(404)
                    return
                outer.request_count += 1
                body = json.dumps({"keys": outer.keys}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                pass  # keep test output clean

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}{JWKS_PATH}"

    def publish(self, *keys: SigningKey) -> None:
        self.keys = [k.public_jwk() for k in keys]

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@contextmanager
def running_jwks_server() -> Iterator[JWKSServer]:
    server = JWKSServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


def make_verifier(jwks_url: str, *, cooldown_seconds: float = 30) -> TokenVerifier:
    client = PyJWKClient(
        jwks_url,
        cache_jwk_set=True,
        lifespan=300,
        timeout=2,
        cooldown_duration=cooldown_seconds,
    )
    return TokenVerifier(jwks_client=client, issuer=TEST_ISSUER, audience=TEST_AUDIENCE)
