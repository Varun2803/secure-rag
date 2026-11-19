from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    # Context manager intentionally enters and exits FastAPI lifespan.
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client


def test_application_initialization() -> None:
    application = create_app()
    assert application.title == "SecureRAG"
    assert application.router.lifespan_context is not None


def test_openapi_available(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert "/api/v1/health" in response.json()["paths"]


def test_root_health(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "application": "SecureRAG"}


@pytest.mark.parametrize("checker,endpoint", [
    ("check_database_connection", "database"), ("check_qdrant_connection", "vector-db")
])
def test_service_health_success(client: TestClient, checker: str, endpoint: str) -> None:
    with patch(f"app.api.health.{checker}", return_value=True) as check:
        response = client.get(f"/api/v1/health/{endpoint}")
    check.assert_called_once_with()
    assert response.status_code == 200


@pytest.mark.parametrize("checker,endpoint,service", [
    ("check_database_connection", "database", "database"),
    ("check_qdrant_connection", "vector-db", "vector-db")
])
def test_service_health_failure(client: TestClient, checker: str, endpoint: str, service: str) -> None:
    with patch(f"app.api.health.{checker}", return_value=False) as check:
        response = client.get(f"/api/v1/health/{endpoint}")
    check.assert_called_once_with()
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "service": service}
    assert "connection" not in response.text


def test_missing_database_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.database import postgres
    postgres.dispose_database_engine()
    monkeypatch.setattr(postgres, "get_settings", lambda: Settings(database_url=None))
    with pytest.raises(postgres.DatabaseNotConfiguredError):
        postgres.get_database_engine()
    assert postgres.check_database_connection() is False


def test_database_session_factory_and_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.database import postgres
    postgres.dispose_database_engine()
    engine = MagicMock()
    monkeypatch.setattr(postgres, "create_database_engine", lambda: engine)
    factory1 = postgres.get_session_factory()
    factory2 = postgres.get_session_factory()
    assert factory1 is factory2
    assert postgres.get_database_engine() is engine
    session = MagicMock()
    monkeypatch.setattr(postgres, "get_session_factory", lambda: lambda: session)
    generator = postgres.get_db()
    next(generator)
    with pytest.raises(StopIteration):
        next(generator)
    session.close.assert_called_once_with()
    postgres.dispose_database_engine()
    engine.dispose.assert_called_once_with()


def test_database_health_reuses_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.database import postgres
    postgres.dispose_database_engine()
    connection = MagicMock()
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection
    monkeypatch.setattr(postgres, "create_database_engine", lambda: engine)
    assert postgres.check_database_connection() is True
    assert postgres.get_database_engine() is engine
    engine.dispose.assert_not_called()
    postgres.dispose_database_engine()


def test_qdrant_connection_error_is_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    from app.database import qdrant
    client = MagicMock()
    client.get_collections.side_effect = httpx.ConnectError("private endpoint")
    qdrant.get_qdrant_client.cache_clear()
    monkeypatch.setattr(qdrant, "get_qdrant_client", lambda: client)
    assert qdrant.check_qdrant_connection() is False


def test_qdrant_client_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.database import qdrant
    client = MagicMock()
    qdrant.get_qdrant_client.cache_clear()
    monkeypatch.setattr(qdrant, "QdrantClient", lambda **kwargs: client)
    assert qdrant.get_qdrant_client() is client
    qdrant.close_qdrant_client()
    client.close.assert_called_once_with()
    assert qdrant.get_qdrant_client.cache_info().currsize == 0


def test_application_exception_handler(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> Settings:
        raise RuntimeError("private connection detail")
    monkeypatch.setattr("app.api.health.get_settings", fail)
    response = client.get("/api/v1/health")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}


def test_lifespan_disposes_both_services_even_if_qdrant_cleanup_fails() -> None:
    with patch("app.main.close_qdrant_client", side_effect=RuntimeError("close failed")) as close_qdrant:
        with patch("app.main.dispose_database_engine") as dispose_database:
            with TestClient(create_app()) as test_client:
                assert test_client.get("/api/v1/health").status_code == 200
            close_qdrant.assert_called_once_with()
            dispose_database.assert_called_once_with()
