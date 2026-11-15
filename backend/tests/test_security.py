"""Unit tests for Supabase access token verification (app.core.security)."""

import json
import time
import uuid

import pytest

from app.core.config import Settings
from app.core.security import (
    JWKS_FAILURE_BACKOFF_SECONDS,
    AuthServiceUnavailableError,
    InvalidTokenError,
    create_token_verifier,
)
from tests.auth_helpers import (
    JWKSServer,
    SigningKey,
    default_claims,
    make_hs256_token_with_public_key,
    make_token,
    make_unsigned_token,
    make_verifier,
    running_jwks_server,
)
from tests.conftest import FakeClock

# --- Valid tokens ---


def test_valid_token_returns_user_id(jwks_server: JWKSServer, signing_key: SigningKey) -> None:
    user_id = uuid.uuid4()
    token = make_token(signing_key, default_claims(sub=str(user_id)))

    user = make_verifier(jwks_server.url).verify(token)

    assert user.id == user_id


def test_token_expired_within_clock_skew_leeway_is_accepted(
    jwks_server: JWKSServer, signing_key: SigningKey
) -> None:
    token = make_token(signing_key, default_claims(exp=int(time.time()) - 5))

    make_verifier(jwks_server.url).verify(token)


def test_token_without_is_anonymous_claim_is_accepted(
    jwks_server: JWKSServer, signing_key: SigningKey
) -> None:
    token = make_token(signing_key, omit=("is_anonymous",))

    make_verifier(jwks_server.url).verify(token)


# --- Claims ---


def test_expired_token_is_rejected(jwks_server: JWKSServer, signing_key: SigningKey) -> None:
    token = make_token(signing_key, default_claims(exp=int(time.time()) - 120))

    with pytest.raises(InvalidTokenError):
        make_verifier(jwks_server.url).verify(token)


def test_wrong_issuer_is_rejected(jwks_server: JWKSServer, signing_key: SigningKey) -> None:
    token = make_token(signing_key, default_claims(iss="https://other-project.supabase.co/auth/v1"))

    with pytest.raises(InvalidTokenError):
        make_verifier(jwks_server.url).verify(token)


def test_wrong_audience_is_rejected(jwks_server: JWKSServer, signing_key: SigningKey) -> None:
    token = make_token(signing_key, default_claims(aud="some-other-audience"))

    with pytest.raises(InvalidTokenError):
        make_verifier(jwks_server.url).verify(token)


@pytest.mark.parametrize("claim", ["exp", "iat", "iss", "aud", "sub", "role"])
def test_missing_required_claim_is_rejected(
    jwks_server: JWKSServer, signing_key: SigningKey, claim: str
) -> None:
    token = make_token(signing_key, omit=(claim,))

    with pytest.raises(InvalidTokenError):
        make_verifier(jwks_server.url).verify(token)


@pytest.mark.parametrize("role", ["anon", "service_role", "", "AUTHENTICATED"])
def test_non_authenticated_role_is_rejected(
    jwks_server: JWKSServer, signing_key: SigningKey, role: str
) -> None:
    token = make_token(signing_key, default_claims(role=role))

    with pytest.raises(InvalidTokenError):
        make_verifier(jwks_server.url).verify(token)


def test_anonymous_user_is_rejected(jwks_server: JWKSServer, signing_key: SigningKey) -> None:
    token = make_token(signing_key, default_claims(is_anonymous=True))

    with pytest.raises(InvalidTokenError):
        make_verifier(jwks_server.url).verify(token)


@pytest.mark.parametrize("sub", ["not-a-uuid", "", "123"])
def test_subject_that_is_not_a_uuid_is_rejected(
    jwks_server: JWKSServer, signing_key: SigningKey, sub: str
) -> None:
    token = make_token(signing_key, default_claims(sub=sub))

    with pytest.raises(InvalidTokenError):
        make_verifier(jwks_server.url).verify(token)


# --- Signature and algorithm attacks ---


def test_token_signed_with_foreign_key_and_known_kid_is_rejected(
    jwks_server: JWKSServer, signing_key: SigningKey
) -> None:
    attacker_key = SigningKey(kid=signing_key.kid)  # same kid, different private key
    token = make_token(attacker_key)

    with pytest.raises(InvalidTokenError):
        make_verifier(jwks_server.url).verify(token)


def test_hs256_token_signed_with_public_key_is_rejected(
    jwks_server: JWKSServer, signing_key: SigningKey
) -> None:
    token = make_hs256_token_with_public_key(signing_key, default_claims())

    with pytest.raises(InvalidTokenError):
        make_verifier(jwks_server.url).verify(token)


def test_unsigned_alg_none_token_is_rejected(
    jwks_server: JWKSServer, signing_key: SigningKey
) -> None:
    token = make_unsigned_token(default_claims(), kid=signing_key.kid)

    with pytest.raises(InvalidTokenError):
        make_verifier(jwks_server.url).verify(token)


def test_tampered_payload_is_rejected(jwks_server: JWKSServer, signing_key: SigningKey) -> None:
    header, _, signature = make_token(signing_key).split(".")
    other_payload = make_token(signing_key, default_claims(sub=str(uuid.uuid4()))).split(".")[1]
    token = f"{header}.{other_payload}.{signature}"

    with pytest.raises(InvalidTokenError):
        make_verifier(jwks_server.url).verify(token)


@pytest.mark.parametrize("token", ["", "abc", "a.b.c", "....", "Bearer xyz"])
def test_malformed_token_is_rejected_without_fetching_keys(
    jwks_server: JWKSServer, token: str
) -> None:
    with pytest.raises(InvalidTokenError):
        make_verifier(jwks_server.url).verify(token)

    assert jwks_server.request_count == 0


def test_algorithm_check_happens_before_key_lookup(
    jwks_server: JWKSServer, signing_key: SigningKey
) -> None:
    token = make_hs256_token_with_public_key(signing_key, default_claims())

    with pytest.raises(InvalidTokenError):
        make_verifier(jwks_server.url).verify(token)

    assert jwks_server.request_count == 0


# --- Key ids and JWKS ---


def test_token_without_kid_is_rejected_without_fetching_keys(
    jwks_server: JWKSServer, signing_key: SigningKey
) -> None:
    token = make_token(signing_key, kid="")

    with pytest.raises(InvalidTokenError):
        make_verifier(jwks_server.url).verify(token)

    assert jwks_server.request_count == 0


def test_unknown_kid_is_rejected(jwks_server: JWKSServer, signing_key: SigningKey) -> None:
    unknown_key = SigningKey(kid="unknown-key")
    token = make_token(unknown_key)

    with pytest.raises(InvalidTokenError):
        make_verifier(jwks_server.url).verify(token)


def test_unknown_kids_cannot_force_unlimited_jwks_refreshes(
    jwks_server: JWKSServer, signing_key: SigningKey
) -> None:
    verifier = make_verifier(jwks_server.url, cooldown_seconds=60)

    for _ in range(10):
        with pytest.raises(InvalidTokenError):
            verifier.verify(make_token(SigningKey(kid=f"random-{uuid.uuid4()}")))

    # Initial fetch only; refreshes are suppressed during the cooldown.
    assert jwks_server.request_count == 1


def test_keys_are_cached_between_verifications(
    jwks_server: JWKSServer, signing_key: SigningKey
) -> None:
    verifier = make_verifier(jwks_server.url)

    for _ in range(5):
        verifier.verify(make_token(signing_key))

    assert jwks_server.request_count == 1


def test_standby_key_published_before_rotation_is_accepted_without_refresh(
    jwks_server: JWKSServer, signing_key: SigningKey
) -> None:
    standby_key = SigningKey(kid="standby-key")
    jwks_server.publish(signing_key, standby_key)
    verifier = make_verifier(jwks_server.url, cooldown_seconds=60)

    verifier.verify(make_token(signing_key))
    verifier.verify(make_token(standby_key))

    assert jwks_server.request_count == 1


def test_rotated_key_is_picked_up_by_refresh_after_cooldown(
    jwks_server: JWKSServer, signing_key: SigningKey
) -> None:
    verifier = make_verifier(jwks_server.url, cooldown_seconds=0)
    verifier.verify(make_token(signing_key))

    new_key = SigningKey(kid="rotated-key")
    jwks_server.publish(signing_key, new_key)

    user = verifier.verify(make_token(new_key))

    assert user.id is not None
    assert jwks_server.request_count == 2


def test_unreachable_jwks_endpoint_raises_service_unavailable(signing_key: SigningKey) -> None:
    with running_jwks_server() as server:
        url = server.url
    # The server is stopped now, so the port refuses connections.

    with pytest.raises(AuthServiceUnavailableError):
        make_verifier(url).verify(make_token(signing_key))


def test_empty_jwks_raises_service_unavailable(
    jwks_server: JWKSServer, signing_key: SigningKey
) -> None:
    jwks_server.keys = []

    with pytest.raises(AuthServiceUnavailableError):
        make_verifier(jwks_server.url).verify(make_token(signing_key))


# --- JWKS failures are 503-class (AuthServiceUnavailableError), never "invalid token" ---

_KEY_WITHOUT_KID = {"kty": "EC", "crv": "P-256", "x": "AAAA", "y": "AAAA"}


@pytest.mark.parametrize(
    "body",
    [
        b"<html><body>502 Bad Gateway</body></html>",
        b"",
        b"{not json",
        b"\xff\xfe\x00 not utf-8",
        b"[]",
        b"null",
        b'"keys"',
        b'{"keys": []}',
        b'{"keys": "oops"}',
        b'{"other": 1}',
        b'{"keys": [{"kty": "EC", "kid": "unusable"}]}',
        json.dumps({"keys": [_KEY_WITHOUT_KID]}).encode(),
    ],
    ids=[
        "html",
        "empty",
        "invalid-json",
        "invalid-utf8",
        "array",
        "null",
        "string",
        "empty-keys",
        "keys-not-a-list",
        "no-keys-member",
        "unusable-key",
        "key-without-kid",
    ],
)
def test_broken_jwks_document_raises_service_unavailable(
    jwks_server: JWKSServer, signing_key: SigningKey, body: bytes
) -> None:
    jwks_server.raw_body = body

    with pytest.raises(AuthServiceUnavailableError):
        make_verifier(jwks_server.url).verify(make_token(signing_key))


@pytest.mark.parametrize(
    "unusable_for_signing",
    [
        lambda jwk: {k: v for k, v in jwk.items() if k != "kid"},
        lambda jwk: {**jwk, "use": "enc"},
    ],
    ids=["valid-key-without-kid", "valid-key-with-use-enc"],
)
def test_jwks_with_valid_keys_but_none_usable_for_signing_raises_service_unavailable(
    jwks_server: JWKSServer, signing_key: SigningKey, unusable_for_signing
) -> None:
    # Parsed successfully by PyJWT, but filtered out by get_signing_keys() ("no signing keys").
    jwks_server.keys = [unusable_for_signing(signing_key.public_jwk())]

    with pytest.raises(AuthServiceUnavailableError):
        make_verifier(jwks_server.url).verify(make_token(signing_key))


@pytest.mark.parametrize("status", [404, 500, 502, 503, 302])
def test_jwks_http_error_status_raises_service_unavailable(
    jwks_server: JWKSServer, signing_key: SigningKey, status: int
) -> None:
    jwks_server.status = status

    with pytest.raises(AuthServiceUnavailableError):
        make_verifier(jwks_server.url).verify(make_token(signing_key))


@pytest.mark.parametrize("behavior", ["truncate", "reset"])
def test_interrupted_jwks_response_raises_service_unavailable(
    jwks_server: JWKSServer, signing_key: SigningKey, behavior: str
) -> None:
    jwks_server.behavior = behavior

    with pytest.raises(AuthServiceUnavailableError):
        make_verifier(jwks_server.url).verify(make_token(signing_key))


def test_unavailable_error_message_has_class_name_only(
    jwks_server: JWKSServer, signing_key: SigningKey
) -> None:
    jwks_server.raw_body = b"secret-looking {garbage"

    with pytest.raises(AuthServiceUnavailableError) as exc_info:
        make_verifier(jwks_server.url).verify(make_token(signing_key))

    assert str(exc_info.value) == "signing keys could not be loaded (JSONDecodeError)"


def test_unknown_kid_with_valid_jwks_is_invalid_token_not_unavailable(
    jwks_server: JWKSServer,
) -> None:
    token = make_token(SigningKey(kid="not-published"))

    with pytest.raises(InvalidTokenError):
        make_verifier(jwks_server.url).verify(token)


def test_failed_refresh_after_unknown_kid_is_unavailable_not_invalid_token(
    jwks_server: JWKSServer, signing_key: SigningKey, clock: FakeClock
) -> None:
    # A failing refresh triggered by an unknown kid is a JWKS problem (503), not a bad token.
    verifier = make_verifier(jwks_server.url)
    verifier.verify(make_token(signing_key))
    clock.advance(31)  # cooldown elapsed, so the unknown kid triggers a refresh
    jwks_server.raw_body = b"<html>"

    with pytest.raises(AuthServiceUnavailableError):
        verifier.verify(make_token(SigningKey(kid="not-published")))


# --- Cache behaviour during JWKS outages ---


def test_warm_cache_keeps_working_during_jwks_outage(
    jwks_server: JWKSServer, signing_key: SigningKey, clock: FakeClock
) -> None:
    verifier = make_verifier(jwks_server.url)
    verifier.verify(make_token(signing_key))
    jwks_server.status = 500
    clock.advance(299)  # still inside the 300 s lifespan

    verifier.verify(make_token(signing_key))

    assert jwks_server.request_count == 1


def test_expired_cache_and_failed_refresh_raises_service_unavailable(
    jwks_server: JWKSServer, signing_key: SigningKey, clock: FakeClock
) -> None:
    verifier = make_verifier(jwks_server.url)
    verifier.verify(make_token(signing_key))
    jwks_server.status = 500
    clock.advance(301)  # cache expired; expired keys must not be served (no stale-on-error)

    with pytest.raises(AuthServiceUnavailableError):
        verifier.verify(make_token(signing_key))

    assert jwks_server.request_count == 2


def test_service_recovers_after_jwks_returns(
    jwks_server: JWKSServer, signing_key: SigningKey, clock: FakeClock
) -> None:
    verifier = make_verifier(jwks_server.url)
    verifier.verify(make_token(signing_key))
    jwks_server.status = 500
    clock.advance(301)
    with pytest.raises(AuthServiceUnavailableError):
        verifier.verify(make_token(signing_key))

    jwks_server.status = 200
    clock.advance(JWKS_FAILURE_BACKOFF_SECONDS + 1)

    verifier.verify(make_token(signing_key))


def test_failed_downloads_are_not_retried_during_backoff(
    jwks_server: JWKSServer, signing_key: SigningKey, clock: FakeClock
) -> None:
    verifier = make_verifier(jwks_server.url)
    verifier.verify(make_token(signing_key))
    jwks_server.status = 500
    clock.advance(301)

    for _ in range(10):
        with pytest.raises(AuthServiceUnavailableError):
            verifier.verify(make_token(signing_key))

    assert jwks_server.request_count == 2  # initial fetch + one failed refresh


def test_random_kids_during_outage_cause_a_single_download_attempt(
    jwks_server: JWKSServer, signing_key: SigningKey, clock: FakeClock
) -> None:
    verifier = make_verifier(jwks_server.url)
    verifier.verify(make_token(signing_key))  # warm cache
    clock.advance(31)  # PyJWT cooldown elapsed, so unknown kids would force refreshes
    jwks_server.status = 500

    for _ in range(10):
        with pytest.raises(AuthServiceUnavailableError):
            verifier.verify(make_token(SigningKey(kid=f"random-{uuid.uuid4()}")))

    assert jwks_server.request_count == 2


def test_backoff_does_not_block_tokens_served_from_valid_cache(
    jwks_server: JWKSServer, signing_key: SigningKey, clock: FakeClock
) -> None:
    verifier = make_verifier(jwks_server.url)
    verifier.verify(make_token(signing_key))
    clock.advance(31)
    jwks_server.status = 500
    with pytest.raises(AuthServiceUnavailableError):
        verifier.verify(make_token(SigningKey(kid="random")))  # starts the backoff

    verifier.verify(make_token(signing_key))  # known kid, valid cache


# --- Key rotation and cooldown ---


def test_rotated_key_is_rejected_inside_cooldown_and_accepted_after_it(
    jwks_server: JWKSServer, signing_key: SigningKey, clock: FakeClock
) -> None:
    verifier = make_verifier(jwks_server.url, cooldown_seconds=30)
    verifier.verify(make_token(signing_key))
    new_key = SigningKey(kid="rotated-key")
    jwks_server.publish(signing_key, new_key)

    clock.advance(29)
    with pytest.raises(InvalidTokenError):
        verifier.verify(make_token(new_key))
    assert jwks_server.request_count == 1  # no refresh inside the cooldown

    clock.advance(2)  # 31 s since the last successful fetch
    verifier.verify(make_token(new_key))
    assert jwks_server.request_count == 2


# --- Production configuration ---


def test_production_verifier_uses_expected_jwks_client_settings() -> None:
    settings = Settings(supabase_url="https://abc.supabase.co")

    client = create_token_verifier(settings)._jwks_client

    assert client.uri == "https://abc.supabase.co/auth/v1/.well-known/jwks.json"
    assert client.timeout == 5
    assert client.jwk_set_cache is not None
    assert client.jwk_set_cache.lifespan == 300
    assert client.cooldown_duration == 30
    assert client._failure_backoff_seconds == JWKS_FAILURE_BACKOFF_SECONDS
