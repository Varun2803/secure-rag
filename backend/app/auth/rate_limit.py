"""Shared PostgreSQL-backed rate limiting for unauthenticated auth routes."""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address, ip_network
from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy import case, delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped, Session, mapped_column

from fastapi import HTTPException, Request, status

from app.config import get_settings
from app.database.base import Base

MAX_BUCKET_ROWS = 100_000


class AuthRateLimitBucket(Base):
    """One shared counter per pseudonymous client and auth operation."""

    __tablename__ = "auth_rate_limit_buckets"
    __table_args__ = (
        CheckConstraint("request_count > 0", name="ck_auth_rate_limit_count_positive"),
        Index("ix_auth_rate_limit_window_started", "window_started_at"),
    )

    bucket_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)


class RateLimitUnavailableError(RuntimeError):
    """Shared rate-limit state could not be safely persisted."""


def _bucket_key(request: Request, category: str) -> str:
    # Forwarded addresses are considered only when the direct ASGI peer matches
    # an explicitly configured trusted proxy network. The proxy must append the
    # client address and strip/normalize untrusted forwarding headers.
    address = request.client.host if request.client is not None else "unknown"
    settings = get_settings()
    trusted_proxies = [
        ip_network(cidr.strip(), strict=False)
        for cidr in settings.trusted_proxy_cidrs.split(",") if cidr.strip()
    ]
    try:
        peer = ip_address(address)
        is_trusted_proxy = any(peer in network for network in trusted_proxies)
    except ValueError:
        is_trusted_proxy = False
    if is_trusted_proxy:
        forwarded_chain = request.headers.get("x-forwarded-for", "")
        forwarded_client = forwarded_chain.rsplit(",", 1)[-1].strip()
        try:
            address = str(ip_address(forwarded_client))
        except ValueError:
            pass
    secret = settings.secret_key.get_secret_value() if settings.secret_key else "development-only-rate-limit-key"
    digest = hmac.new(secret.encode("utf-8"), f"{category}\0{address}".encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()


def consume_auth_rate_limit(
    session: Session,
    *,
    request: Request,
    category: str,
    maximum: int,
    window_seconds: int,
    now: datetime | None = None,
) -> bool:
    """Atomically increment a shared fixed-window counter and return whether allowed."""
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(seconds=window_seconds)
    table = AuthRateLimitBucket.__table__
    dialect = session.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert if dialect == "sqlite" else None
    if insert is None:
        raise RateLimitUnavailableError("Unsupported shared rate-limit database")
    bucket_key = _bucket_key(request, category)
    statement = insert(table).values(
        bucket_key=bucket_key, window_started_at=current, request_count=1,
    )
    excluded = statement.excluded
    expired = table.c.window_started_at <= cutoff
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.bucket_key],
        set_={
            "window_started_at": case((expired, excluded.window_started_at), else_=table.c.window_started_at),
            "request_count": case((expired, 1), else_=table.c.request_count + 1),
        },
    ).returning(table.c.request_count)
    try:
        # Keep storage bounded by removing stale counters in a small indexed batch.
        stale = select(table.c.bucket_key).where(
            table.c.window_started_at < current - timedelta(days=1)
        ).order_by(table.c.window_started_at).limit(100)
        session.execute(delete(table).where(table.c.bucket_key.in_(stale)))
        exists = session.scalar(select(table.c.bucket_key).where(table.c.bucket_key == bucket_key))
        if exists is None:
            row_count = session.scalar(select(func.count()).select_from(table)) or 0
            if row_count >= MAX_BUCKET_ROWS:
                session.rollback()
                raise RateLimitUnavailableError("Shared rate-limit storage is at capacity")
        count = session.scalar(statement)
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        raise RateLimitUnavailableError from exc
    return bool(count is not None and count <= maximum)


def enforce_auth_rate_limit(
    request: Request,
    session: Session,
    *,
    category: str,
    maximum: int,
    window_seconds: int,
) -> None:
    try:
        allowed = consume_auth_rate_limit(
            session, request=request, category=category,
            maximum=maximum, window_seconds=window_seconds,
        )
    except RateLimitUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Authentication service unavailable") from exc
    if not allowed:
        retry_after = max(1, window_seconds)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication requests",
            headers={"Retry-After": str(retry_after)},
        )
