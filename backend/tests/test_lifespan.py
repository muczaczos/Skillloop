import logging
from typing import Annotated

import pytest
from fastapi.testclient import TestClient

from app import main
from tests.db_fakes import FakePool


@pytest.fixture
def fake_pool(monkeypatch: pytest.MonkeyPatch) -> FakePool:
    pool = FakePool()
    monkeypatch.setattr(main, "create_pool", lambda settings: pool)
    return pool


def test_pool_is_opened_without_waiting_and_closed_on_shutdown(fake_pool: FakePool) -> None:
    with TestClient(main.app) as client:
        assert fake_pool.opened_with == {"wait": False, "timeout": 30.0}
        assert fake_pool.close_calls == 0
        assert client.app.state.db_pool is fake_pool  # type: ignore[attr-defined]
    assert fake_pool.close_calls == 1


def test_pool_is_closed_even_if_the_app_fails_while_running(fake_pool: FakePool) -> None:
    with pytest.raises(RuntimeError), TestClient(main.app):
        raise RuntimeError("boom")

    assert fake_pool.close_calls == 1


def test_app_starts_in_degraded_mode_when_the_database_is_down(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pool = FakePool(available=0)
    monkeypatch.setattr(main, "create_pool", lambda settings: pool)
    monkeypatch.setattr(main, "STARTUP_WAIT_SECONDS", 0.2)

    with caplog.at_level(logging.INFO, logger="app.main"), TestClient(main.app) as client:
        response = client.get("/api/v1/health")
        assert pool.close_calls == 0  # still open: it keeps reconnecting

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}  # liveness only, says nothing about the database
    assert "degraded mode" in caplog.text
    assert pool.close_calls == 1


def test_start_log_never_contains_connection_details(
    fake_pool: FakePool, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG), TestClient(main.app):
        pass

    assert "test-password" not in caplog.text
    assert "postgresql://" not in caplog.text


def test_get_db_pool_without_a_pool_answers_503() -> None:
    from fastapi import Depends, FastAPI
    from psycopg_pool import ConnectionPool

    from app.api.deps import get_db_pool

    test_app = FastAPI()
    test_app.exception_handlers.update(main.app.exception_handlers)

    @test_app.get("/needs-db")
    def _needs_db(pool: Annotated[ConnectionPool, Depends(get_db_pool)]) -> dict[str, str]:
        return {"status": "ok"}

    response = TestClient(test_app).get("/needs-db")

    assert response.status_code == 503
    assert response.json() == {"detail": main.DATABASE_UNAVAILABLE_DETAIL}
