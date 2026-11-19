from collections.abc import Generator
from threading import Lock

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


class DatabaseNotConfiguredError(RuntimeError):
    """Raised when a database operation is requested without a URL."""


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_engine_lock = Lock()


def create_database_engine() -> Engine:
    settings = get_settings()
    if not settings.database_url:
        raise DatabaseNotConfiguredError("DATABASE_URL is not configured")
    return create_engine(settings.database_url, pool_pre_ping=True, pool_size=settings.database_pool_size,
                         max_overflow=settings.database_max_overflow, pool_timeout=settings.database_pool_timeout,
                         pool_recycle=settings.database_pool_recycle)


def get_database_engine() -> Engine:
    """Return the process-wide engine, constructing it on first use."""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = create_database_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    engine = get_database_engine()
    if _session_factory is None:
        with _engine_lock:
            if _session_factory is None:
                _session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return _session_factory


def dispose_database_engine() -> None:
    """Dispose pooled connections and reset cached engine state."""
    global _engine, _session_factory
    with _engine_lock:
        engine, _engine = _engine, None
        _session_factory = None
        if engine is not None:
            engine.dispose()


def get_db() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def check_database_connection() -> bool:
    try:
        with get_database_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except (SQLAlchemyError, DatabaseNotConfiguredError, ValueError):
        return False
