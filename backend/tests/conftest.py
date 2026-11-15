import os
import time
from collections.abc import Iterator

import pytest

# Deterministic settings for tests. Must be set before `app` is imported,
# because Settings() is instantiated at import time and SUPABASE_URL is required.
os.environ["SUPABASE_URL"] = "https://test-project.supabase.co"
os.environ["SUPABASE_JWT_AUDIENCE"] = "authenticated"

from tests.auth_helpers import (
    JWKSServer,
    SigningKey,
    running_jwks_server,
)


@pytest.fixture
def signing_key() -> SigningKey:
    return SigningKey(kid="test-key-1")


@pytest.fixture
def jwks_server(signing_key: SigningKey) -> Iterator[JWKSServer]:
    with running_jwks_server() as server:
        server.publish(signing_key)
        yield server


class FakeClock:
    """Controllable replacement for time.monotonic (used for cache and cooldown timing)."""

    def __init__(self) -> None:
        self.now = time.monotonic()

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Freeze time.monotonic so tests can move past cache lifespan / cooldown instantly."""
    fake = FakeClock()
    monkeypatch.setattr(time, "monotonic", fake)
    return fake
