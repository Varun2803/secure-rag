import pytest
from pydantic import ValidationError

from app.config import Settings
from app.main import request_body_limit_status

PRODUCTION_INFRA = {
    "database_url": "postgresql+psycopg://user:password@db.example.com/securerag",
    "qdrant_url": "https://qdrant.example.com",
    "qdrant_api_key": "qdrant-test-api-key",
}


def test_production_rejects_debug_mode() -> None:
    with pytest.raises(ValidationError, match="DEBUG must be disabled in production"):
        Settings(
            app_env="production",
            debug=True,
            secret_key="0123456789abcdef0123456789abcdef",
            trusted_hosts="api.example.com",
            **PRODUCTION_INFRA,
        )


def test_production_allows_debug_disabled() -> None:
    settings = Settings(
        app_env="production",
        debug=False,
        secret_key="0123456789abcdef0123456789abcdef",
        trusted_hosts="api.example.com",
        **PRODUCTION_INFRA,
    )
    assert settings.debug is False


@pytest.mark.parametrize("environment", [" Production ", "PROD"])
def test_debug_setting_is_case_and_whitespace_insensitive(environment: str) -> None:
    with pytest.raises(ValidationError, match="DEBUG must be disabled in production"):
        Settings(
            app_env=environment,
            debug=True,
            secret_key="0123456789abcdef0123456789abcdef",
            trusted_hosts="api.example.com",
            **PRODUCTION_INFRA,
        )


@pytest.mark.parametrize(
    "content_length,expected",
    [(None, None), ("0", None), ("100", None), ("101", 413),
     ("-1", 400), ("+1", 400), (" 1", 400), ("١", 400), ("invalid", 400)],
)
def test_request_body_limit_rejects_invalid_and_oversized_declared_bodies(
    content_length: str | None, expected: int | None,
) -> None:
    assert request_body_limit_status(content_length, 100) == expected


@pytest.mark.parametrize(
    "secret", [None, "short", "replace-with-a-random-secret-of-at-least-32-bytes",
               "dev-secret-012345678901234567890123"],
)
def test_production_rejects_missing_weak_or_placeholder_signing_secret(secret: str | None) -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(app_env="production", secret_key=secret, trusted_hosts="api.example.com", **PRODUCTION_INFRA)


def test_production_requires_explicit_trusted_hosts() -> None:
    with pytest.raises(ValidationError, match="TRUSTED_HOSTS"):
        Settings(
            app_env="production", secret_key="0123456789abcdef0123456789abcdef", **PRODUCTION_INFRA,
        )


@pytest.mark.parametrize("hosts", ["*", "*.example.com"])
def test_production_rejects_wildcard_trusted_hosts(hosts: str) -> None:
    with pytest.raises(ValidationError, match="TRUSTED_HOSTS"):
        Settings(
            app_env="production", secret_key="0123456789abcdef0123456789abcdef",
            trusted_hosts=hosts, **PRODUCTION_INFRA,
        )


def test_production_requires_database_and_authenticated_qdrant() -> None:
    common = {
        "app_env": "production", "secret_key": "0123456789abcdef0123456789abcdef",
        "trusted_hosts": "api.example.com", "qdrant_url": "https://qdrant.example.com",
        "qdrant_api_key": "qdrant-test-api-key",
    }
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(**common)
    with pytest.raises(ValidationError, match="QDRANT_API_KEY"):
        Settings(**{**common, "database_url": "postgresql+psycopg://user:password@db.example.com/securerag",
                    "qdrant_api_key": None})
    with pytest.raises(ValidationError, match="PostgreSQL DATABASE_URL"):
        Settings(**{**common, "database_url": "sqlite:///production.db"})


@pytest.mark.parametrize("url", ["http://qdrant.example.com", "http://10.0.0.12:6333"])
def test_production_rejects_plain_http_qdrant_endpoints(url: str) -> None:
    with pytest.raises(ValidationError, match="QDRANT_URL"):
        Settings(
            app_env="production", secret_key="0123456789abcdef0123456789abcdef",
            trusted_hosts="api.example.com", database_url=PRODUCTION_INFRA["database_url"],
            qdrant_url=url, qdrant_api_key="qdrant-test-api-key",
        )


def test_trusted_host_list_rejects_url_syntax() -> None:
    with pytest.raises(ValidationError, match="TRUSTED_HOSTS"):
        Settings(trusted_hosts="https://api.example.com")


def test_trusted_proxy_networks_are_validated() -> None:
    assert Settings(trusted_proxy_cidrs="10.0.0.0/8, 192.0.2.10/32").trusted_proxy_cidrs == "10.0.0.0/8, 192.0.2.10/32"
    with pytest.raises(ValidationError, match="TRUSTED_PROXY_CIDRS"):
        Settings(trusted_proxy_cidrs="trust-all-forwarders")


def test_development_qdrant_local_endpoints_are_supported() -> None:
    assert Settings(qdrant_url="http://localhost:6333").qdrant_url == "http://localhost:6333"
    assert Settings(qdrant_url="http://qdrant:6333").qdrant_url == "http://qdrant:6333"


def test_qdrant_remote_http_is_rejected_even_in_development() -> None:
    with pytest.raises(ValidationError, match="QDRANT_URL"):
        Settings(qdrant_url="http://10.0.0.12:6333")


def test_production_disables_interactive_api_documentation(monkeypatch) -> None:
    from app.main import create_app

    monkeypatch.setattr("app.main.get_settings", lambda: Settings(
        app_env="production", debug=False,
        secret_key="0123456789abcdef0123456789abcdef", trusted_hosts="api.example.com",
        **PRODUCTION_INFRA,
    ))
    application = create_app()
    assert application.openapi_url is None
    assert application.docs_url is None
    assert application.redoc_url is None
    trusted_host_middleware = next(item for item in application.user_middleware if item.cls.__name__ == "TrustedHostMiddleware")
    assert trusted_host_middleware.kwargs["allowed_hosts"] == ["api.example.com"]
