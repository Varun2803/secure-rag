import asyncio
from datetime import datetime, timedelta, timezone
from fastapi import Request
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.rate_limit import AuthRateLimitBucket
from app.auth.rate_limit import consume_auth_rate_limit
from app.config import Settings


def _post(auth_app: FastAPI, path: str, **kwargs):
    async def send_request():
        transport = ASGITransport(app=auth_app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post(path, **kwargs)

    return asyncio.run(send_request())


def _client_request(
    address: str = "192.0.2.10", forwarded_for: str | None = None,
) -> Request:
    headers = [(b"x-forwarded-for", forwarded_for.encode())] if forwarded_for else []
    return Request({
        "type": "http", "method": "POST", "scheme": "http", "path": "/api/v1/auth/login",
        "raw_path": b"/api/v1/auth/login", "query_string": b"", "headers": headers,
        "client": (address, 1234), "server": ("testserver", 80),
    })


def test_shared_rate_limit_counter_resets_after_window(test_session_factory) -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with test_session_factory() as session:
        assert consume_auth_rate_limit(
            session, request=_client_request(), category="login", maximum=2, window_seconds=60, now=start,
        )
        assert consume_auth_rate_limit(
            session, request=_client_request(), category="login", maximum=2, window_seconds=60,
            now=start + timedelta(seconds=1),
        )
        assert not consume_auth_rate_limit(
            session, request=_client_request(), category="login", maximum=2, window_seconds=60,
            now=start + timedelta(seconds=2),
        )
        assert consume_auth_rate_limit(
            session, request=_client_request(), category="login", maximum=2, window_seconds=60,
            now=start + timedelta(seconds=61),
        )


def test_rate_limit_ignores_untrusted_forwarded_address_headers(test_session_factory) -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with test_session_factory() as session:
        assert consume_auth_rate_limit(
            session, request=_client_request(forwarded_for="203.0.113.1"),
            category="login", maximum=2, window_seconds=60, now=now,
        )
        assert not consume_auth_rate_limit(
            session, request=_client_request(forwarded_for="198.51.100.9"),
            category="login", maximum=1, window_seconds=60, now=now + timedelta(seconds=1),
        )
        buckets = session.scalars(select(AuthRateLimitBucket)).all()
    assert len(buckets) == 1
    assert buckets[0].request_count == 2


def test_forwarded_address_is_used_only_for_configured_proxy_peers(test_session_factory, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.auth.rate_limit.get_settings",
        lambda: Settings(trusted_proxy_cidrs="10.0.0.0/8"),
    )
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with test_session_factory() as session:
        assert consume_auth_rate_limit(
            session, request=_client_request("10.1.2.3", "198.51.100.1"),
            category="login", maximum=1, window_seconds=60, now=now,
        )
        assert consume_auth_rate_limit(
            session, request=_client_request("10.1.2.3", "198.51.100.2"),
            category="login", maximum=1, window_seconds=60, now=now + timedelta(seconds=1),
        )
        buckets = session.scalars(select(AuthRateLimitBucket)).all()
    assert len(buckets) == 2


def test_rate_limit_storage_has_a_hard_capacity_and_fails_closed(test_session_factory, monkeypatch) -> None:
    from app.auth.rate_limit import RateLimitUnavailableError

    monkeypatch.setattr("app.auth.rate_limit.MAX_BUCKET_ROWS", 1)
    with test_session_factory() as session:
        assert consume_auth_rate_limit(
            session, request=_client_request("192.0.2.10"), category="login",
            maximum=10, window_seconds=60,
        )
        try:
            consume_auth_rate_limit(
                session, request=_client_request("192.0.2.11"), category="login",
                maximum=10, window_seconds=60,
            )
        except RateLimitUnavailableError:
            pass
        else:
            raise AssertionError("A full rate-limit store must fail closed for new clients")


def test_login_route_enforces_shared_rate_limit_and_sanitizes_response(
    auth_app: FastAPI, monkeypatch, test_session_factory,
) -> None:
    monkeypatch.setattr("app.auth.routes.LOGIN_LIMIT", 1)
    first = _post(auth_app,
        "/api/v1/auth/login", data={"username": "unknown@example.com", "password": "invalid"},
    )
    second = _post(auth_app,
        "/api/v1/auth/login", data={"username": "unknown@example.com", "password": "invalid"},
    )

    assert first.status_code == 401
    assert second.status_code == 429
    assert second.headers["retry-after"] == "60"
    assert second.json() == {"detail": "Too many authentication requests"}
    assert "unknown@example.com" not in second.text
    with test_session_factory() as session:
        buckets = session.scalars(select(AuthRateLimitBucket)).all()
    assert len(buckets) == 1
    assert buckets[0].request_count == 2


def test_registration_route_enforces_its_own_rate_limit(auth_app: FastAPI, monkeypatch) -> None:
    monkeypatch.setattr("app.auth.routes.REGISTRATION_LIMIT", 1)
    payload = {"email": "first@example.com", "password": "StrongPassword123!", "full_name": "First User"}
    assert _post(auth_app, "/api/v1/auth/register", json=payload).status_code == 201
    denied = _post(auth_app,
        "/api/v1/auth/register",
        json={**payload, "email": "second@example.com"},
    )
    assert denied.status_code == 429
    assert "second@example.com" not in denied.text


def test_rate_limit_storage_failure_fails_closed(auth_app: FastAPI, monkeypatch) -> None:
    from app.auth.rate_limit import RateLimitUnavailableError

    def unavailable(*args, **kwargs):
        raise RateLimitUnavailableError

    monkeypatch.setattr("app.auth.rate_limit.consume_auth_rate_limit", unavailable)
    response = _post(auth_app,
        "/api/v1/auth/login", data={"username": "user@example.com", "password": "invalid"},
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication service unavailable"}
