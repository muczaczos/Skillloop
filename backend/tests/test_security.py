"""Unit tests for Supabase access token verification (app.core.security)."""

import time
import uuid

import pytest

from app.core.security import AuthServiceUnavailableError, InvalidTokenError
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
