import pytest
from pydantic import ValidationError

from app.config import Settings
from app.main import request_body_limit_status


def test_production_rejects_debug_mode() -> None:
    with pytest.raises(ValidationError, match="DEBUG must be disabled in production"):
        Settings(
            app_env="production",
            debug=True,
            secret_key="0123456789abcdef0123456789abcdef",
        )


def test_production_allows_debug_disabled() -> None:
    settings = Settings(
        app_env="production",
        debug=False,
        secret_key="0123456789abcdef0123456789abcdef",
    )
    assert settings.debug is False


@pytest.mark.parametrize("environment", [" Production ", "PROD"])
def test_debug_setting_is_case_and_whitespace_insensitive(environment: str) -> None:
    with pytest.raises(ValidationError, match="DEBUG must be disabled in production"):
        Settings(
            app_env=environment,
            debug=True,
            secret_key="0123456789abcdef0123456789abcdef",
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
