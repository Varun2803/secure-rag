from collections.abc import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.base import Base
from app.database.postgres import get_db
from app.main import create_app
from app.tenants.dependencies import TenantContext, TenantContextDependency


@pytest.fixture
def test_session_factory() -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def auth_app(test_session_factory: sessionmaker[Session]) -> FastAPI:
    application = create_app()

    def override_get_db() -> Generator[Session, None, None]:
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_db] = override_get_db

    @application.get("/__test/tenant-context/{tenant_id}")
    def tenant_context_probe(context: TenantContextDependency) -> dict[str, str]:
        assert isinstance(context, TenantContext)
        return {
            "user_id": str(context.user_id),
            "tenant_id": str(context.tenant_id),
            "membership_id": str(context.membership_id),
            "role": context.role.value,
        }

    return application


@pytest.fixture
def auth_client(auth_app: FastAPI) -> Generator[TestClient, None, None]:
    with TestClient(auth_app, raise_server_exceptions=False) as client:
        yield client
