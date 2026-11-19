import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.audit.events import AuditEventType, AuditOutcome
from app.audit.models import AuditLog, ImmutableAuditRecordError
from app.audit.schemas import AuditEventPublic
from app.audit.service import (
    AuditMetadataError,
    AuditPersistenceError,
    create_audit_event,
    query_audit_events,
    sanitize_metadata,
    stage_audit_event,
)
from app.auth.security import create_access_token, hash_password
from app.config import Settings
from app.documents.schemas import DocumentPublic
from app.documents.service import DuplicateDocumentError, IngestionFailedError
from app.documents.storage import StoredUpload
from app.retrieval.schemas import RetrievalSearchResponse
from app.tenants.dependencies import TenantContext
from app.tenants.models import Tenant, TenantMembership, TenantRole
from app.users.models import User

_SECRET = "audit-tests-secret-key-that-is-at-least-32-bytes-long"


def _seed_identity(factory, email: str = "audit@example.test", role: TenantRole = TenantRole.OWNER):
    with factory() as session:
        user = User(email=email, hashed_password=hash_password("AuditTestPassword123!"), full_name="Audit User")
        tenant = Tenant(name="Audit Company", slug=f"audit-{uuid4().hex[:10]}")
        session.add_all([user, tenant])
        session.flush()
        membership = TenantMembership(tenant_id=tenant.id, user_id=user.id, role=role)
        session.add(membership)
        session.commit()
        context = TenantContext(user.id, tenant.id, membership.id, role)
        return user, tenant, membership, context


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.fixture(autouse=True)
def token_settings(monkeypatch):
    monkeypatch.setattr("app.auth.security.get_settings", lambda: Settings(
        secret_key=SecretStr(_SECRET),
    ))


def test_audit_event_persists_typed_fields_and_aware_timestamp(test_session_factory):
    user, tenant, _, _ = _seed_identity(test_session_factory)
    request_id = uuid4()
    with test_session_factory() as session:
        event = create_audit_event(
            session, event_type=AuditEventType.AUTH_LOGIN_SUCCESS, action="auth.login",
            outcome=AuditOutcome.SUCCESS, tenant_id=tenant.id, actor_user_id=user.id,
            request_id=request_id,
        )
        assert event.id is not None and event.request_id == request_id
        assert event.created_at.tzinfo is not None
        assert AuditLog.__table__.c.created_at.type.timezone is True
        assert AuditEventPublic.model_validate(event).event_type == "AUTH_LOGIN_SUCCESS"


@pytest.mark.parametrize("event_type,outcome", [("NOT_AN_EVENT", "SUCCESS"), ("AUTH_LOGIN_FAILURE", "MAYBE")])
def test_event_type_and_outcome_are_validated(test_session_factory, event_type, outcome):
    with test_session_factory() as session, pytest.raises(ValueError):
        create_audit_event(session, event_type=event_type, action="auth.login", outcome=outcome)


def test_metadata_allowlist_retains_safe_values(test_session_factory):
    clean = sanitize_metadata({"top_k": 5, "result_count": 2, "grounded": True,
                               "changed_fields": ["name", "description"]})
    assert clean == {"top_k": 5, "result_count": 2, "grounded": True,
                     "changed_fields": ["name", "description"]}
    with test_session_factory() as session:
        record = create_audit_event(session, event_type="RETRIEVAL_SUCCESS", action="retrieval.search",
                                    outcome="SUCCESS", metadata=clean)
        assert record.metadata_json == clean


@pytest.mark.parametrize("key", [
    "password", "access_token", "refresh_token", "authorization", "api_key", "secret",
    "prompt", "system_prompt", "document_text", "content", "generated_answer", "raw_response",
])
def test_sensitive_metadata_fields_are_rejected(key):
    with pytest.raises(AuditMetadataError):
        sanitize_metadata({key: "must-not-persist"})


@pytest.mark.parametrize("metadata", [
    {"other": "not allowlisted"},
    {"status_category": {"nested": "not allowed"}},
    {"status_category": "entire user query"},
    {"changed_fields": ["password"]},
    {"duration_ms": True},
    {"status_category": "x" * 5000},
])
def test_unsupported_nested_or_oversized_metadata_is_rejected(metadata):
    with pytest.raises(AuditMetadataError):
        sanitize_metadata(metadata)


def test_serialized_metadata_size_cap_is_enforced(monkeypatch):
    import app.audit.service as audit_service
    monkeypatch.setattr(audit_service, "MAX_METADATA_BYTES", 5)
    with pytest.raises(AuditMetadataError):
        sanitize_metadata({"top_k": 10})


def test_audit_event_rejects_invalid_resource_ids_and_reason_codes(test_session_factory):
    with test_session_factory() as session:
        with pytest.raises(ValueError):
            create_audit_event(session, event_type="AUTH_LOGIN_FAILURE", action="Auth Login",
                               outcome="FAILURE", reason_code="bad reason")
        with pytest.raises(ValueError):
            create_audit_event(session, event_type="AUTH_LOGIN_FAILURE", action="auth.login",
                               outcome="FAILURE", tenant_id="not-a-uuid")


def test_audit_writer_will_not_commit_pending_business_changes(test_session_factory):
    with test_session_factory() as session:
        user = User(email="pending@example.test", hashed_password="hash", full_name="Pending")
        user_id = uuid4()
        user.id = user_id
        session.add(user)
        with pytest.raises(AuditPersistenceError):
            create_audit_event(session, event_type="AUTH_LOGIN_FAILURE", action="auth.login", outcome="FAILURE")
        session.rollback()
        assert session.get(User, user_id) is None


def test_duplicate_request_event_is_idempotent(test_session_factory):
    request_id = uuid4()
    with test_session_factory() as session:
        first = create_audit_event(session, event_type="AUTH_LOGIN_FAILURE", action="auth.login",
                                   outcome="FAILURE", request_id=request_id, reason_code="INVALID_CREDENTIALS")
        second = create_audit_event(session, event_type="AUTH_LOGIN_FAILURE", action="auth.login",
                                    outcome="FAILURE", request_id=request_id, reason_code="INVALID_CREDENTIALS")
        assert second.id == first.id
        assert session.query(AuditLog).filter_by(request_id=request_id).count() == 1


def test_transaction_aware_audit_staging_is_idempotent_and_does_not_commit(test_session_factory):
    request_id = uuid4()
    with test_session_factory() as session:
        first = stage_audit_event(
            session, event_type=AuditEventType.AUTH_LOGIN_FAILURE, action="auth.login",
            outcome=AuditOutcome.FAILURE, request_id=request_id, reason_code="INVALID_CREDENTIALS",
        )
        second = stage_audit_event(
            session, event_type=AuditEventType.AUTH_LOGIN_FAILURE, action="auth.login",
            outcome=AuditOutcome.FAILURE, request_id=request_id, reason_code="INVALID_CREDENTIALS",
        )
        assert first is second
        assert first in session.new
        assert session.query(AuditLog).filter_by(request_id=request_id).count() == 0
        session.commit()
    with test_session_factory() as session:
        assert session.query(AuditLog).filter_by(request_id=request_id).count() == 1


def test_audit_database_failure_is_raised_not_silently_swallowed(test_session_factory, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    with test_session_factory() as session:
        monkeypatch.setattr(session, "flush", lambda: (_ for _ in ()).throw(SQLAlchemyError("internal db detail")))
        with pytest.raises(AuditPersistenceError, match="could not be persisted"):
            create_audit_event(session, event_type="AUTH_LOGIN_FAILURE", action="auth.login", outcome="FAILURE")


def test_application_orm_cannot_update_or_delete_audit_events(test_session_factory):
    with test_session_factory() as session:
        record = create_audit_event(session, event_type="AUTH_LOGIN_FAILURE", action="auth.login", outcome="FAILURE")
        record.reason_code = "CHANGED"
        with pytest.raises(ImmutableAuditRecordError):
            session.flush()
        session.rollback()
        record = session.query(AuditLog).one()
        session.delete(record)
        with pytest.raises(ImmutableAuditRecordError):
            session.flush()
        session.rollback()


def test_audit_indexes_and_uniqueness_constraint_are_declared():
    table = AuditLog.__table__
    indexes = {index.name for index in table.indexes}
    assert {"ix_audit_logs_tenant_created_at", "ix_audit_logs_actor_created_at",
            "ix_audit_logs_event_created_at", "ix_audit_logs_resource"} <= indexes
    assert "uq_audit_logs_request_event_action" in {constraint.name for constraint in table.constraints}


def test_query_filters_tenant_actor_event_resource_outcome_dates_and_pagination(test_session_factory):
    first, tenant_a, _, context_a = _seed_identity(test_session_factory, "audit-a@example.test")
    second, tenant_b, _, _ = _seed_identity(test_session_factory, "audit-b@example.test")
    with test_session_factory() as session:
        for index in range(3):
            create_audit_event(
                session, event_type=AuditEventType.RETRIEVAL_SUCCESS, action="retrieval.search",
                outcome=AuditOutcome.SUCCESS, tenant_id=tenant_a.id, actor_user_id=first.id,
                resource_type="document", resource_id=f"resource-{index}",
                metadata={"result_count": index},
            )
        create_audit_event(
            session, event_type=AuditEventType.AUTH_LOGIN_FAILURE, action="auth.login",
            outcome=AuditOutcome.FAILURE, tenant_id=tenant_b.id, actor_user_id=second.id,
        )
        page = query_audit_events(
            session, context=context_a, event_type=AuditEventType.RETRIEVAL_SUCCESS,
            actor_user_id=first.id, resource_type="document", outcome=AuditOutcome.SUCCESS,
            resource_id="resource-2", start_at=datetime.now(timezone.utc) - timedelta(days=1),
            end_at=datetime.now(timezone.utc) + timedelta(days=1), limit=1, offset=0,
        )
        assert page.total == 1 and page.items[0].resource_id == "resource-2"
        all_events = query_audit_events(session, context=context_a, limit=2, offset=1)
        assert all_events.total == 3 and len(all_events.items) == 2
        assert all(event.tenant_id == tenant_a.id for event in all_events.items)
        assert all_events.items == sorted(all_events.items, key=lambda event: (event.created_at, event.id), reverse=True)
        with pytest.raises(ValueError):
            query_audit_events(session, context=tenant_b.id)


def test_query_rejects_invalid_date_ranges_and_pagination(test_session_factory):
    _, _, _, context = _seed_identity(test_session_factory)
    with test_session_factory() as session:
        with pytest.raises(ValueError):
            query_audit_events(session, context=context, limit=101)
        with pytest.raises(ValueError):
            query_audit_events(session, context=context, start_at=datetime.now())


def test_login_success_and_failure_are_audited_without_credentials(auth_client, test_session_factory):
    user, _, _, _ = _seed_identity(test_session_factory, "login-audit@example.test")
    failed = auth_client.post("/api/v1/auth/login", data={"username": user.email, "password": "wrong-secret"})
    success = auth_client.post("/api/v1/auth/login", data={"username": user.email,
                                                           "password": "AuditTestPassword123!"})
    assert failed.status_code == 401 and success.status_code == 200
    token = success.json()["access_token"]
    with test_session_factory() as session:
        rows = session.query(AuditLog).filter(AuditLog.event_type.in_(
            ["AUTH_LOGIN_SUCCESS", "AUTH_LOGIN_FAILURE"])).all()
        failed_row = next(row for row in rows if row.event_type == "AUTH_LOGIN_FAILURE")
        success_row = next(row for row in rows if row.event_type == "AUTH_LOGIN_SUCCESS")
    assert failed_row.actor_user_id is None and failed_row.reason_code == "INVALID_CREDENTIALS"
    assert success_row.actor_user_id == user.id
    serialized = json.dumps([row.metadata_json for row in rows])
    assert "wrong-secret" not in serialized and "AuditTestPassword123!" not in serialized and token not in serialized


def test_invalid_jwt_creates_actorless_audit_event(auth_client, test_session_factory):
    response = auth_client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalid-token-value"})
    assert response.status_code == 401
    with test_session_factory() as session:
        event = session.query(AuditLog).filter_by(event_type="AUTH_TOKEN_INVALID").one()
        assert event.actor_user_id is None and event.reason_code == "INVALID_TOKEN"


def test_tenant_success_and_cross_tenant_denial_are_audited_safely(auth_client, test_session_factory):
    owner, tenant, _, _ = _seed_identity(test_session_factory, "tenant-audit@example.test")
    success = auth_client.get(f"/__test/tenant-context/{tenant.id}", headers=_headers(owner))
    denied_tenant = Tenant(name="Unrelated", slug=f"unrelated-{uuid4().hex[:8]}")
    with test_session_factory() as session:
        session.add(denied_tenant)
        session.commit()
        target_id = denied_tenant.id
    denied = auth_client.get(f"/__test/tenant-context/{target_id}", headers=_headers(owner))
    assert success.status_code == 200 and denied.status_code == 404
    with test_session_factory() as session:
        events = session.query(AuditLog).filter(AuditLog.event_type.in_(
            ["TENANT_ACCESS_SUCCESS", "TENANT_ACCESS_DENIED"])).all()
    assert any(event.tenant_id == tenant.id and event.actor_user_id == owner.id for event in events)
    refusal = next(event for event in events if event.event_type == "TENANT_ACCESS_DENIED")
    assert refusal.tenant_id is None and refusal.resource_id == str(target_id)
    assert refusal.metadata_json == {}


def test_inactive_membership_denial_is_audited(auth_client, test_session_factory):
    owner, tenant, membership, _ = _seed_identity(test_session_factory, "inactive-membership-audit@example.test")
    with test_session_factory() as session:
        session.get(TenantMembership, membership.id).is_active = False
        session.commit()
    response = auth_client.get(f"/__test/tenant-context/{tenant.id}", headers=_headers(owner))
    assert response.status_code == 404
    with test_session_factory() as session:
        event = session.query(AuditLog).filter_by(event_type="TENANT_ACCESS_DENIED").one()
    assert event.tenant_id is None and event.actor_user_id == owner.id
    assert event.reason_code == "ACTIVE_MEMBERSHIP_REQUIRED"


def test_existing_tenant_administration_is_audited(auth_client, test_session_factory):
    owner, tenant, _, _ = _seed_identity(test_session_factory, "admin-audit@example.test")
    new_user = User(email="membership-audit@example.com", hashed_password=hash_password("AuditTestPassword123!"),
                    full_name="New Member")
    with test_session_factory() as session:
        session.add(new_user)
        session.commit()
    added = auth_client.post(
        f"/api/v1/tenants/{tenant.id}/memberships", headers=_headers(owner),
        json={"email": new_user.email, "role": "HR"},
    )
    updated = auth_client.patch(
        f"/api/v1/tenants/{tenant.id}", headers=_headers(owner),
        json={"name": "Renamed Audit Company"},
    )
    created = auth_client.post(
        "/api/v1/tenants", headers=_headers(owner),
        json={"name": "New Audit Company", "slug": "new-audit-company"},
    )
    assert added.status_code == 201 and updated.status_code == 200 and created.status_code == 201
    with test_session_factory() as session:
        tenant_events = session.query(AuditLog).filter(AuditLog.tenant_id == tenant.id).all()
    assert any(event.event_type == "TENANT_MEMBERSHIP_CHANGED" and
               event.action == "tenant.membership.create" and
               event.metadata_json.get("target_role") == "HR" for event in tenant_events)
    assert any(event.event_type == "TENANT_UPDATED" and
               event.metadata_json.get("changed_fields") == ["name"] for event in tenant_events)
    with test_session_factory() as session:
        assert session.query(AuditLog).filter_by(event_type="TENANT_CREATED",
                                                 tenant_id=UUID(created.json()["id"])).count() == 1


def _document_public(tenant_id, user_id):
    now = datetime.now(timezone.utc)
    return DocumentPublic(
        id=uuid4(), tenant_id=tenant_id, uploaded_by=user_id, filename=f"{uuid4()}.pdf",
        original_filename="sensitive-name.pdf", file_hash="a" * 64, file_size=100,
        content_type="application/pdf", page_count=2, chunk_count=3, status="COMPLETED",
        classification=1, is_public=False, allowed_roles=[], allowed_users=[],
        created_at=now, updated_at=now, processing_completed_at=now,
    )


def test_document_upload_and_ingestion_success_events_omit_names_and_paths(
    auth_client, test_session_factory, monkeypatch, tmp_path: Path,
):
    user, tenant, _, _ = _seed_identity(test_session_factory, "document-audit@example.test")
    stored = StoredUpload(tmp_path / "private-storage-path.pdf", str(uuid4()), 100, "f" * 64)
    monkeypatch.setattr("app.documents.routes.store_upload", _async_return(stored))
    monkeypatch.setattr("app.documents.routes.ingest_document", lambda *_args, **kwargs: _document_public(tenant.id, user.id))
    response = auth_client.post(f"/api/v1/tenants/{tenant.id}/documents/upload", headers=_headers(user),
                                files={"file": ("sensitive-name.pdf", b"pdf-bytes", "application/pdf")})
    assert response.status_code == 201
    with test_session_factory() as session:
        events = session.query(AuditLog).filter(
            AuditLog.request_id == UUID(response.headers["X-Request-ID"]),
        ).all()
    kinds = {event.event_type for event in events}
    assert {"DOCUMENT_UPLOAD_STARTED", "DOCUMENT_UPLOAD_SUCCESS", "DOCUMENT_INGESTION_SUCCESS"} <= kinds
    serialized = json.dumps([event.metadata_json for event in events])
    assert "sensitive-name.pdf" not in serialized and "private-storage-path" not in serialized


def _async_return(value):
    async def inner(*_args, **_kwargs):
        return value
    return inner


def test_duplicate_upload_is_audited_without_filename(auth_client, test_session_factory, monkeypatch, tmp_path):
    user, tenant, _, _ = _seed_identity(test_session_factory, "duplicate-audit@example.test")
    stored_path = tmp_path / "temporary.pdf"
    stored_path.write_bytes(b"temp")
    stored = StoredUpload(stored_path, str(uuid4()), 4, "a" * 64)
    monkeypatch.setattr("app.documents.routes.store_upload", _async_return(stored))
    monkeypatch.setattr("app.documents.routes.ingest_document", lambda *_a, **_k: (_ for _ in ()).throw(DuplicateDocumentError()))
    response = auth_client.post(f"/api/v1/tenants/{tenant.id}/documents/upload", headers=_headers(user),
                                files={"file": ("secret.pdf", b"pdf", "application/pdf")})
    assert response.status_code == 409 and not stored_path.exists()
    with test_session_factory() as session:
        event = session.query(AuditLog).filter_by(event_type="DOCUMENT_UPLOAD_FAILURE",
                                                  reason_code="DUPLICATE_DOCUMENT").one()
        assert "secret.pdf" not in json.dumps(event.metadata_json)


def test_failed_ingestion_is_audited_with_sanitized_failure_code(auth_client, test_session_factory, monkeypatch, tmp_path):
    user, tenant, _, _ = _seed_identity(test_session_factory, "failed-doc-audit@example.test")
    doc_id = uuid4()
    stored_path = tmp_path / "failed.pdf"
    stored_path.write_bytes(b"temp")
    stored = StoredUpload(stored_path, str(uuid4()), 4, "b" * 64)
    monkeypatch.setattr("app.documents.routes.store_upload", _async_return(stored))
    monkeypatch.setattr("app.documents.routes.ingest_document", lambda *_a, **_k: (_ for _ in ()).throw(
        IngestionFailedError(SimpleNamespace(id=doc_id), "PDF_INVALID")))
    response = auth_client.post(f"/api/v1/tenants/{tenant.id}/documents/upload", headers=_headers(user),
                                files={"file": ("policy.pdf", b"pdf", "application/pdf")})
    assert response.status_code == 422 and not stored_path.exists()
    with test_session_factory() as session:
        events = session.query(AuditLog).filter(AuditLog.resource_id == str(doc_id)).all()
    assert {event.event_type for event in events} == {"DOCUMENT_UPLOAD_FAILURE", "DOCUMENT_INGESTION_FAILURE"}
    assert all(event.reason_code == "PDF_INVALID" for event in events)


def test_retrieval_and_generation_events_store_counts_not_query_or_answer(
    auth_client, test_session_factory, monkeypatch,
):
    user, tenant, _, _ = _seed_identity(test_session_factory, "rag-audit@example.test")
    monkeypatch.setattr("app.retrieval.routes.search_documents", lambda session, *, context, request:
                        RetrievalSearchResponse(query=request.query, tenant_id=context.tenant_id, results=[], result_count=0))
    retrieval_response = auth_client.post(
        f"/api/v1/tenants/{tenant.id}/retrieval/search", headers=_headers(user),
        json={"query": "private exact query", "top_k": 4},
    )
    assert retrieval_response.status_code == 200
    monkeypatch.setattr("app.generation.routes.generate_answer", lambda *_a, **_k:
                        __import__("app.generation.schemas", fromlist=["GenerationResponse"]).GenerationResponse(
                            query="private exact query", answer="private generated answer", citations=[],
                            result_count=0, grounded=False))
    generation_response = auth_client.post(
        f"/api/v1/tenants/{tenant.id}/generation/answer", headers=_headers(user),
        json={"query": "private exact query", "top_k": 4},
    )
    assert generation_response.status_code == 200
    with test_session_factory() as session:
        events = session.query(AuditLog).filter(AuditLog.tenant_id == tenant.id).all()
    types = {event.event_type for event in events}
    assert {"RETRIEVAL_REQUEST", "RETRIEVAL_EMPTY", "GENERATION_REQUEST", "GENERATION_INSUFFICIENT_CONTEXT"} <= types
    serialized = json.dumps([event.metadata_json for event in events])
    assert "private exact query" not in serialized and "private generated answer" not in serialized
    assert all(event.tenant_id == tenant.id for event in events if event.event_type.startswith(("RETRIEVAL", "GENERATION")))


def test_unavailable_retrieval_and_generation_create_failure_events(
    auth_client, test_session_factory, monkeypatch,
):
    user, tenant, _, _ = _seed_identity(test_session_factory, "failed-rag-audit@example.test")
    from app.retrieval.service import RetrievalUnavailableError
    monkeypatch.setattr("app.retrieval.routes.search_documents", lambda *_a, **_k:
                        (_ for _ in ()).throw(RetrievalUnavailableError()))
    response = auth_client.post(f"/api/v1/tenants/{tenant.id}/retrieval/search", headers=_headers(user),
                                json={"query": "not logged", "top_k": 2})
    assert response.status_code == 503
    with test_session_factory() as session:
        event = session.query(AuditLog).filter_by(event_type="RETRIEVAL_FAILURE").one()
        assert event.reason_code == "RETRIEVAL_UNAVAILABLE" and "not logged" not in json.dumps(event.metadata_json)


def test_cross_tenant_retrieval_denial_is_audited_without_authorized_tenant_claim(
    auth_client, test_session_factory,
):
    owner, tenant, _, _ = _seed_identity(test_session_factory, "retrieval-denial-audit@example.test")
    response = auth_client.post(
        f"/api/v1/tenants/{tenant.id}/retrieval/search", headers={"Authorization": "Bearer invalid-token"},
        json={"query": "private", "top_k": 2},
    )
    assert response.status_code == 401
    other_user = User(email="not-member@example.test", hashed_password="hash", full_name="Other")
    with test_session_factory() as session:
        session.add(other_user)
        session.commit()
    response = auth_client.post(
        f"/api/v1/tenants/{tenant.id}/retrieval/search", headers=_headers(other_user),
        json={"query": "private", "top_k": 2},
    )
    assert response.status_code == 404
    with test_session_factory() as session:
        events = session.query(AuditLog).filter_by(event_type="RETRIEVAL_ACCESS_DENIED").all()
    assert len(events) == 1 and events[0].tenant_id is None
    assert events[0].actor_user_id == other_user.id and events[0].resource_id == str(tenant.id)


def test_generation_provider_failure_is_audited_without_prompt_or_answer(
    auth_client, test_session_factory, monkeypatch,
):
    from app.generation.service import GenerationUnavailableError
    user, tenant, _, _ = _seed_identity(test_session_factory, "generation-failure-audit@example.test")
    monkeypatch.setattr("app.generation.routes.generate_answer", lambda *_a, **_k:
                        (_ for _ in ()).throw(GenerationUnavailableError("provider detail")))
    response = auth_client.post(
        f"/api/v1/tenants/{tenant.id}/generation/answer", headers=_headers(user),
        json={"query": "private prompt text", "top_k": 2},
    )
    assert response.status_code == 503 and "provider detail" not in response.text
    with test_session_factory() as session:
        event = session.query(AuditLog).filter_by(event_type="GENERATION_FAILURE").one()
    assert event.reason_code == "GENERATION_UNAVAILABLE"
    assert "private prompt text" not in json.dumps(event.metadata_json)


def test_audit_migration_has_postgres_append_only_trigger():
    migration = Path("backend/alembic/versions/0004_create_audit_logs.py").read_text(encoding="utf-8")
    assert "BEFORE UPDATE OR DELETE ON audit_logs" in migration
    assert "BEFORE TRUNCATE ON audit_logs" in migration
    assert "reject_audit_log_mutation" in migration
