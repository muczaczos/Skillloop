import os
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
