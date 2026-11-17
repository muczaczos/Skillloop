from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.core.errors import ProfileNotFoundError
from app.models.auth import AuthenticatedUser
from app.models.profile import ProfileRecord
from app.schemas.profile import ProfileUpdate
from app.services import profile_service

USER = AuthenticatedUser(id=uuid4())
POOL: Any = object()
CONN: Any = object()


def _profile(**overrides: Any) -> ProfileRecord:
    values: dict[str, Any] = {
        "id": USER.id,
        "display_name": "Ada",
        "preferred_language": "en",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    return ProfileRecord(**{**values, **overrides})


class Recorder:
    """Records the order of transaction and repository events."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.transaction_args: tuple[Any, Any] | None = None


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    rec = Recorder()

    @contextmanager
    def fake_user_transaction(pool: Any, user: Any) -> Iterator[Any]:
        rec.transaction_args = (pool, user)
        rec.events.append("begin")
        try:
            yield CONN
        finally:
            rec.events.append("end")

    monkeypatch.setattr(profile_service, "user_transaction", fake_user_transaction)
    return rec


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch, recorder: Recorder) -> MagicMock:
    mock = MagicMock()
    mock.get.side_effect = lambda *a: recorder.events.append("get") or mock.get.result
    mock.update.side_effect = lambda *a: recorder.events.append("update") or mock.update.result
    monkeypatch.setattr(profile_service, "profiles", mock)
    return mock


def test_get_profile_returns_existing_profile(repo: MagicMock, recorder: Recorder) -> None:
    profile = _profile()
    repo.get.result = profile

    assert profile_service.get_profile(POOL, USER) is profile
    repo.get.assert_called_once_with(CONN, USER.id)
    assert recorder.transaction_args == (POOL, USER)


def test_get_profile_missing_raises_not_found(repo: MagicMock) -> None:
    repo.get.result = None

    with pytest.raises(ProfileNotFoundError):
        profile_service.get_profile(POOL, USER)


def test_get_profile_runs_inside_user_transaction(repo: MagicMock, recorder: Recorder) -> None:
    repo.get.result = _profile()

    profile_service.get_profile(POOL, USER)

    assert recorder.events == ["begin", "get", "end"]


def test_update_profile_returns_updated_profile(repo: MagicMock) -> None:
    updated = _profile(display_name="Grace")
    repo.update.result = updated

    result = profile_service.update_profile(POOL, USER, ProfileUpdate(display_name="Grace"))

    assert result is updated


def test_update_profile_passes_changes_to_repository(repo: MagicMock) -> None:
    repo.update.result = _profile()
    data = ProfileUpdate(display_name=None, preferred_language="pl")

    profile_service.update_profile(POOL, USER, data)

    # An explicit null is a change (clears display_name); omitted fields are not sent.
    repo.update.assert_called_once_with(
        CONN, USER.id, {"display_name": None, "preferred_language": "pl"}
    )
    only_name = ProfileUpdate(display_name="Grace")
    profile_service.update_profile(POOL, USER, only_name)
    assert repo.update.call_args.args[2] == {"display_name": "Grace"}


def test_update_profile_missing_raises_not_found(repo: MagicMock) -> None:
    repo.update.result = None

    with pytest.raises(ProfileNotFoundError):
        profile_service.update_profile(POOL, USER, ProfileUpdate(display_name="Grace"))


def test_update_profile_runs_inside_user_transaction(repo: MagicMock, recorder: Recorder) -> None:
    repo.update.result = _profile()

    profile_service.update_profile(POOL, USER, ProfileUpdate(display_name="Grace"))

    assert recorder.transaction_args == (POOL, USER)
    assert recorder.events == ["begin", "update", "end"]
