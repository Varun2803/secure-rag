from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from datetime import timedelta
from fastapi.testclient import TestClient
from pydantic import SecretStr
from qdrant_client.http import models as qdrant_models

from app.audit.events import AuditEventType
from app.audit.models import AuditLog
from app.audit.service import AuditPersistenceError
from app.auth.security import create_access_token
from app.config import Settings
from app.documents.models import Document, DocumentClassification, DocumentStatus
from app.permissions.models import DocumentPermissionSync, PermissionSyncStatus
from app.permissions.schemas import PermissionReplaceRequest
from app.permissions import service as permission_service
from app.permissions.service import (
    PermissionAuditError, PermissionDatabaseError, PermissionDocumentNotReadyError,
    replace_document_permissions,
)
from app.permissions import synchronization
from app.permissions.synchronization import (
    PermissionSyncPersistenceError, process_pending_permission_syncs, process_permission_sync_task,
)
from app.retrieval import service as retrieval_service
from app.retrieval.schemas import RetrievalSearchRequest
from app.tenants.dependencies import TenantContext
from app.tenants.models import Tenant, TenantMembership, TenantRole
from app.users.models import User

_SECRET = "permission-tests-jwt-secret-key-at-least-32-bytes"


@pytest.fixture(autouse=True)
def token_settings(monkeypatch):
    monkeypatch.setattr("app.auth.security.get_settings", lambda: Settings(secret_key=SecretStr(_SECRET)))


class FakeQdrant:
    def __init__(self, *, fail: bool = False, count: int = 2):
        self.fail = fail
        self.count_value = count
        self.set_calls: list[dict] = []

    def count(self, **kwargs):
        if self.fail:
            raise RuntimeError("private Qdrant endpoint and credentials")
        self.count_args = kwargs
        return SimpleNamespace(count=self.count_value)

    def set_payload(self, **kwargs):
        if self.fail:
            raise RuntimeError("private Qdrant endpoint and credentials")
        self.set_calls.append(kwargs)
        return SimpleNamespace(status=qdrant_models.UpdateStatus.COMPLETED)


def _seed(factory, *, document_acl=None):
    document_acl = document_acl or {}
    roles = [TenantRole.OWNER, TenantRole.ADMIN, TenantRole.HR, TenantRole.MANAGER, TenantRole.EMPLOYEE]
    with factory() as session:
        tenant = Tenant(name="Permissions Co", slug=f"permissions-{uuid4().hex[:10]}")
        users = [User(email=f"{role.value.lower()}-{uuid4().hex[:8]}@example.com",
                      hashed_password="not-used", full_name=role.value) for role in roles]
        user = User(email=f"allowed-{uuid4().hex[:8]}@example.com", hashed_password="not-used", full_name="Allowed")
        foreign = User(email=f"foreign-{uuid4().hex[:8]}@example.com", hashed_password="not-used", full_name="Foreign")
        foreign_tenant = Tenant(name="Foreign Co", slug=f"foreign-{uuid4().hex[:10]}")
        session.add_all([tenant, *users, user, foreign, foreign_tenant])
        session.flush()
        memberships = [TenantMembership(tenant_id=tenant.id, user_id=item.id, role=role)
                       for item, role in zip(users, roles, strict=True)]
        allowed_membership = TenantMembership(tenant_id=tenant.id, user_id=user.id, role=TenantRole.EMPLOYEE)
        foreign_membership = TenantMembership(
            tenant_id=foreign_tenant.id, user_id=foreign.id, role=TenantRole.ADMIN,
        )
        session.add_all([*memberships, allowed_membership, foreign_membership])
        session.flush()
        document = Document(
            tenant_id=tenant.id,
            uploaded_by=users[0].id,
            filename=f"{uuid4()}.pdf",
            original_filename="private.pdf",
            file_hash=uuid4().hex,
            file_size=100,
            content_type="application/pdf",
            page_count=1,
            chunk_count=2,
            status=DocumentStatus.COMPLETED,
            classification=document_acl.get("classification", DocumentClassification.INTERNAL.value),
            is_public=document_acl.get("is_public", False),
            allowed_roles=document_acl.get("allowed_roles", []),
            allowed_users=document_acl.get("allowed_users", []),
            permission_updated_at=None,
        )
        session.add(document)
        session.commit()
        context_by_role = {
            role: TenantContext(item.id, tenant.id, membership.id, role)
            for item, membership, role in zip(users, memberships, roles, strict=True)
        }
        return {
            "tenant_id": tenant.id,
            "document_id": document.id,
            "users": users,
            "contexts": context_by_role,
            "allowed_user": user,
            "foreign_user": foreign,
            "foreign_tenant_id": foreign_tenant.id,
            "membership_ids": [membership.id for membership in memberships],
        }


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _payload(*, classification="CONFIDENTIAL", is_public=False, allowed_roles=None, allowed_users=None):
    return {
        "classification": classification,
        "is_public": is_public,
        "allowed_roles": allowed_roles or [],
        "allowed_users": allowed_users or [],
    }


def _call_update(client: TestClient, data, *, user, tenant_id, document_id):
    return client.put(
        f"/api/v1/tenants/{tenant_id}/documents/{document_id}/permissions",
        headers=_headers(user), json=data,
    )


def _replace_permissions(session, data, payload, *, actor_user_id=None, request_id=None):
    return replace_document_permissions(
        session,
        tenant_id=data["tenant_id"],
        document_id=data["document_id"],
        payload=payload,
        actor_user_id=actor_user_id or data["users"][0].id,
        request_id=request_id or uuid4(),
    )


def test_permission_routes_are_registered_and_unauthenticated_is_rejected(auth_client, test_session_factory):
    data = _seed(test_session_factory)
    paths = auth_client.get("/openapi.json").json()["paths"]
    path = "/api/v1/tenants/{tenant_id}/documents/{document_id}/permissions"
    assert path in paths
    response = auth_client.get(f"/api/v1/tenants/{data['tenant_id']}/documents/{data['document_id']}/permissions")
    assert response.status_code == 401


@pytest.mark.parametrize("role", [TenantRole.OWNER, TenantRole.ADMIN])
def test_owner_and_admin_can_read_and_replace_permissions(
    auth_client, test_session_factory, monkeypatch, role,
):
    data = _seed(test_session_factory)
    qdrant = FakeQdrant()
    monkeypatch.setattr("app.permissions.synchronization.get_qdrant_client", lambda: qdrant)
    user = data["users"][[TenantRole.OWNER, TenantRole.ADMIN].index(role)]
    path = f"/api/v1/tenants/{data['tenant_id']}/documents/{data['document_id']}/permissions"
    read = auth_client.get(path, headers=_headers(user))
    assert read.status_code == 200
    assert set(read.json()) == {
        "document_id", "tenant_id", "classification", "is_public", "allowed_roles", "allowed_users",
        "permission_updated_at", "synchronization_status",
    }
    assert "private.pdf" not in read.text
    response = _call_update(
        auth_client,
        _payload(allowed_roles=["HR"], allowed_users=[str(data["allowed_user"].id)]),
        user=user, tenant_id=data["tenant_id"], document_id=data["document_id"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["classification"] == "CONFIDENTIAL"
    assert response.json()["allowed_roles"] == ["HR"]
    assert response.json()["allowed_users"] == [str(data["allowed_user"].id)]
    assert response.json()["synchronization_status"] == "COMPLETED"
    assert response.json()["permission_updated_at"]
    assert len(qdrant.set_calls) == 1
    with test_session_factory() as session:
        document = session.get(Document, data["document_id"])
        assert document.allowed_roles == ["HR"]
        assert document.allowed_users == [str(data["allowed_user"].id)]
        assert session.query(DocumentPermissionSync).filter_by(document_id=document.id).count() == 1
        assert session.query(AuditLog).filter_by(
            event_type=AuditEventType.DOCUMENT_PERMISSION_UPDATE.value,
            tenant_id=data["tenant_id"], actor_user_id=user.id,
        ).count() == 1


@pytest.mark.parametrize("role", [TenantRole.HR, TenantRole.MANAGER, TenantRole.EMPLOYEE])
def test_non_admin_roles_cannot_read_or_change_permissions(auth_client, test_session_factory, role):
    data = _seed(test_session_factory)
    user = data["users"][[TenantRole.OWNER, TenantRole.ADMIN, TenantRole.HR,
                           TenantRole.MANAGER, TenantRole.EMPLOYEE].index(role)]
    path = f"/api/v1/tenants/{data['tenant_id']}/documents/{data['document_id']}/permissions"
    assert auth_client.get(path, headers=_headers(user)).status_code == 403
    assert _call_update(auth_client, _payload(), user=user, tenant_id=data["tenant_id"],
                        document_id=data["document_id"]).status_code == 403


def test_invalid_jwt_inactive_membership_and_tenant_mismatch_are_rejected(auth_client, test_session_factory):
    data = _seed(test_session_factory)
    path = f"/api/v1/tenants/{data['tenant_id']}/documents/{data['document_id']}/permissions"
    invalid = auth_client.get(path, headers={"Authorization": "Bearer invalid"})
    assert invalid.status_code == 401
    with test_session_factory() as session:
        membership = session.get(TenantMembership, data["membership_ids"][0])
        membership.is_active = False
        session.commit()
    assert auth_client.get(path, headers=_headers(data["users"][0])).status_code == 404
    other_path = f"/api/v1/tenants/{data['foreign_tenant_id']}/documents/{data['document_id']}/permissions"
    assert auth_client.get(other_path, headers=_headers(data["users"][0])).status_code == 404


def test_cross_tenant_document_is_not_disclosed(auth_client, test_session_factory):
    data = _seed(test_session_factory)
    foreign_admin = data["foreign_user"]
    # The foreign identity has only its own tenant membership, so it cannot inspect this resource.
    response = auth_client.get(
        f"/api/v1/tenants/{data['foreign_tenant_id']}/documents/{data['document_id']}/permissions",
        headers=_headers(foreign_admin),
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Document not found"}


@pytest.mark.parametrize("body", [
    _payload(allowed_roles=["INVALID"]),
    _payload(allowed_roles=["HR", "HR"]),
    _payload(allowed_users=[str(uuid4()), str(uuid4())]),
    {**_payload(), "extra": "not allowed"},
    {"classification": "UNKNOWN", "is_public": False, "allowed_roles": [], "allowed_users": []},
    {"classification": "INTERNAL", "is_public": False, "allowed_roles": []},
])
def test_malformed_permission_replacements_are_rejected_without_partial_update(
    auth_client, test_session_factory, body,
):
    data = _seed(test_session_factory)
    response = _call_update(auth_client, body, user=data["users"][0],
                            tenant_id=data["tenant_id"], document_id=data["document_id"])
    assert response.status_code == 422
    with test_session_factory() as session:
        document = session.get(Document, data["document_id"])
        assert document.classification == DocumentClassification.INTERNAL.value
        assert not document.is_public and document.allowed_roles == [] and document.allowed_users == []
        assert session.query(DocumentPermissionSync).count() == 0


def test_user_assignment_requires_active_same_tenant_membership(auth_client, test_session_factory, monkeypatch):
    data = _seed(test_session_factory)
    qdrant = FakeQdrant()
    monkeypatch.setattr("app.permissions.synchronization.get_qdrant_client", lambda: qdrant)
    foreign_id = data["foreign_user"].id
    response = _call_update(auth_client, _payload(allowed_users=[str(foreign_id)]), user=data["users"][0],
                            tenant_id=data["tenant_id"], document_id=data["document_id"])
    assert response.status_code == 422
    with test_session_factory() as session:
        membership = session.query(TenantMembership).filter_by(user_id=data["allowed_user"].id).one()
        member = session.query(User).filter_by(id=data["allowed_user"].id).one()
        membership.is_active = False
        session.commit()
    response = _call_update(auth_client, _payload(allowed_users=[str(data["allowed_user"].id)]),
                            user=data["users"][0], tenant_id=data["tenant_id"],
                            document_id=data["document_id"])
    assert response.status_code == 422
    with test_session_factory() as session:
        session.query(TenantMembership).filter_by(user_id=member.id).update({"is_active": True})
        session.query(User).filter_by(id=member.id).update({"is_active": False})
        session.commit()
    response = _call_update(auth_client, _payload(allowed_users=[str(data["allowed_user"].id)]),
                            user=data["users"][0], tenant_id=data["tenant_id"],
                            document_id=data["document_id"])
    assert response.status_code == 422
    missing_id = uuid4()
    assert _call_update(auth_client, _payload(allowed_users=[str(missing_id)]), user=data["users"][0],
                        tenant_id=data["tenant_id"], document_id=data["document_id"]).status_code == 422
    assert not qdrant.set_calls


def test_invalid_assignment_does_not_partially_replace_existing_acl(
    auth_client, test_session_factory, monkeypatch,
):
    data = _seed(test_session_factory)
    monkeypatch.setattr("app.permissions.synchronization.get_qdrant_client", lambda: FakeQdrant())
    initial = _call_update(
        auth_client,
        _payload(allowed_roles=["HR"], allowed_users=[str(data["allowed_user"].id)]),
        user=data["users"][0], tenant_id=data["tenant_id"], document_id=data["document_id"],
    )
    assert initial.status_code == 200
    invalid = _call_update(
        auth_client,
        _payload(allowed_roles=["MANAGER"], allowed_users=[str(data["foreign_user"].id)]),
        user=data["users"][0], tenant_id=data["tenant_id"], document_id=data["document_id"],
    )
    assert invalid.status_code == 422
    with test_session_factory() as session:
        document = session.get(Document, data["document_id"])
        assert document.allowed_roles == ["HR"]
        assert document.allowed_users == [str(data["allowed_user"].id)]


def test_permission_update_and_revocation_audits_share_the_acl_outbox_transaction(
    test_session_factory, monkeypatch,
):
    data = _seed(test_session_factory, document_acl={"is_public": True})
    staged = []
    original_stage = permission_service.stage_audit_event

    def capture_stage(session, **event):
        record = original_stage(session, **event)
        if event["event_type"] in {
            AuditEventType.DOCUMENT_PERMISSION_UPDATE,
            AuditEventType.DOCUMENT_PERMISSION_REVOKED,
        }:
            document = session.get(Document, data["document_id"])
            task = next(item for item in session.new if isinstance(item, DocumentPermissionSync))
            assert record in session.new
            assert document in session.dirty
            assert task in session.new
            staged.append((record, event))
        return record

    monkeypatch.setattr(permission_service, "stage_audit_event", capture_stage)
    request_id = uuid4()
    with test_session_factory() as session:
        _replace_permissions(session, data, PermissionReplaceRequest.model_validate(_payload()),
                             actor_user_id=data["users"][0].id, request_id=request_id)
    assert {event["event_type"] for _, event in staged} == {
        AuditEventType.DOCUMENT_PERMISSION_UPDATE,
        AuditEventType.DOCUMENT_PERMISSION_REVOKED,
    }
    assert all(event["tenant_id"] == data["tenant_id"] for _, event in staged)
    assert all(event["actor_user_id"] == data["users"][0].id for _, event in staged)
    assert all(event["resource_id"] == data["document_id"] for _, event in staged)
    assert all(event["request_id"] == request_id for _, event in staged)
    with test_session_factory() as session:
        document = session.get(Document, data["document_id"])
        task = session.query(DocumentPermissionSync).filter_by(document_id=document.id).one()
        events = session.query(AuditLog).filter_by(request_id=request_id).all()
        assert document.is_public is False
        assert task.status == PermissionSyncStatus.PENDING.value
        assert {item.event_type for item in events} == {
            AuditEventType.DOCUMENT_PERMISSION_UPDATE.value,
            AuditEventType.DOCUMENT_PERMISSION_REVOKED.value,
        }


def test_permission_audit_insert_failure_rolls_back_acl_and_outbox(
    test_session_factory, monkeypatch,
):
    data = _seed(test_session_factory)

    def fail_audit_insert(*_args, **_kwargs):
        raise AuditPersistenceError("audit unavailable")

    monkeypatch.setattr(permission_service, "stage_audit_event", fail_audit_insert)
    with test_session_factory() as session:
        with pytest.raises(PermissionAuditError):
            _replace_permissions(
                session, data,
                PermissionReplaceRequest.model_validate(_payload(allowed_roles=["HR"])),
            )
    with test_session_factory() as session:
        document = session.get(Document, data["document_id"])
        assert document.classification == DocumentClassification.INTERNAL.value
        assert document.is_public is False and document.allowed_roles == [] and document.allowed_users == []
        assert document.permission_updated_at is None
        assert session.query(DocumentPermissionSync).filter_by(document_id=document.id).count() == 0
        assert session.query(AuditLog).filter_by(
            event_type=AuditEventType.DOCUMENT_PERMISSION_UPDATE.value,
            resource_id=str(document.id),
        ).count() == 0


def test_commit_time_audit_insert_error_rolls_back_permission_and_outbox(
    test_session_factory, monkeypatch,
):
    from sqlalchemy.exc import SQLAlchemyError

    data = _seed(test_session_factory)
    with test_session_factory() as session:
        original_flush = session.flush

        def fail_when_audit_is_pending(*, objects=None):
            if any(isinstance(item, AuditLog) for item in session.new):
                raise SQLAlchemyError("simulated audit insert error")
            return original_flush(objects=objects)

        monkeypatch.setattr(session, "flush", fail_when_audit_is_pending)
        with pytest.raises(PermissionDatabaseError):
            _replace_permissions(
                session, data,
                PermissionReplaceRequest.model_validate(_payload(allowed_roles=["HR"])),
            )
    with test_session_factory() as session:
        document = session.get(Document, data["document_id"])
        assert document.allowed_roles == []
        assert document.permission_updated_at is None
        assert session.query(DocumentPermissionSync).filter_by(document_id=document.id).count() == 0
        assert session.query(AuditLog).filter_by(
            event_type=AuditEventType.DOCUMENT_PERMISSION_UPDATE.value,
            resource_id=str(document.id),
        ).count() == 0


def test_revocation_audit_failure_rolls_back_update_event_acl_and_outbox(
    test_session_factory, monkeypatch,
):
    data = _seed(test_session_factory, document_acl={"is_public": True})
    original_stage = permission_service.stage_audit_event

    def fail_revoke_event(session, **event):
        if event["event_type"] == AuditEventType.DOCUMENT_PERMISSION_REVOKED:
            raise AuditPersistenceError("audit unavailable")
        return original_stage(session, **event)

    monkeypatch.setattr(permission_service, "stage_audit_event", fail_revoke_event)
    with test_session_factory() as session:
        with pytest.raises(PermissionAuditError):
            _replace_permissions(session, data, PermissionReplaceRequest.model_validate(_payload()))
    with test_session_factory() as session:
        document = session.get(Document, data["document_id"])
        assert document.is_public is True
        assert document.permission_updated_at is None
        assert session.query(DocumentPermissionSync).filter_by(document_id=document.id).count() == 0
        assert session.query(AuditLog).filter(
            AuditLog.event_type.in_([
                AuditEventType.DOCUMENT_PERMISSION_UPDATE.value,
                AuditEventType.DOCUMENT_PERMISSION_REVOKED.value,
            ]),
            AuditLog.resource_id == str(document.id),
        ).count() == 0


def test_api_does_not_report_permission_success_when_audit_staging_fails(
    auth_client, test_session_factory, monkeypatch,
):
    data = _seed(test_session_factory)

    def fail_audit_insert(*_args, **_kwargs):
        raise AuditPersistenceError("audit unavailable")

    monkeypatch.setattr(permission_service, "stage_audit_event", fail_audit_insert)
    response = _call_update(
        auth_client, _payload(allowed_roles=["HR"]), user=data["users"][0],
        tenant_id=data["tenant_id"], document_id=data["document_id"],
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "Document permissions could not be updated"}
    with test_session_factory() as session:
        document = session.get(Document, data["document_id"])
        assert document.allowed_roles == [] and document.permission_updated_at is None
        assert session.query(DocumentPermissionSync).filter_by(document_id=document.id).count() == 0
        event_types = {item.event_type for item in session.query(AuditLog).filter_by(
            tenant_id=data["tenant_id"], resource_id=str(document.id),
        ).all()}
        assert AuditEventType.DOCUMENT_PERMISSION_UPDATE.value not in event_types
        assert AuditEventType.DOCUMENT_PERMISSION_UPDATE_FAILURE.value in event_types


@pytest.mark.parametrize("qdrant_failure,expected_status,expected_event", [
    (False, PermissionSyncStatus.COMPLETED, AuditEventType.DOCUMENT_PERMISSION_SYNC_SUCCESS),
    (True, PermissionSyncStatus.PENDING, AuditEventType.DOCUMENT_PERMISSION_SYNC_FAILURE),
])
def test_sync_state_transition_and_outcome_audit_are_atomic(
    test_session_factory, monkeypatch, qdrant_failure, expected_status, expected_event,
):
    data = _seed(test_session_factory)
    with test_session_factory() as session:
        update = _replace_permissions(
            session, data, PermissionReplaceRequest.model_validate(_payload(allowed_roles=["HR"])),
        )
    staged = []
    original_stage = synchronization.stage_audit_event

    def capture_stage(session, **event):
        record = original_stage(session, **event)
        if event["event_type"] == expected_event:
            task = session.get(DocumentPermissionSync, update.sync_task.id)
            assert task.status == expected_status.value
            assert task in session.dirty
            assert record in session.new
            staged.append(record)
        return record

    monkeypatch.setattr(synchronization, "stage_audit_event", capture_stage)
    with test_session_factory() as session:
        result = process_permission_sync_task(
            session, task_id=update.sync_task.id, client=FakeQdrant(fail=qdrant_failure),
        )
    assert bool(staged)
    assert result.status == expected_status
    with test_session_factory() as session:
        task = session.get(DocumentPermissionSync, update.sync_task.id)
        assert task.status == expected_status.value
        assert session.query(AuditLog).filter_by(
            event_type=expected_event.value, resource_id=str(data["document_id"]),
        ).count() == 1


def test_sync_audit_failure_rolls_back_completion_and_allows_idempotent_retry(
    test_session_factory, monkeypatch,
):
    data = _seed(test_session_factory)
    with test_session_factory() as session:
        update = _replace_permissions(
            session, data, PermissionReplaceRequest.model_validate(_payload(allowed_roles=["HR"])),
        )
    qdrant = FakeQdrant()
    original_stage = synchronization.stage_audit_event

    def fail_success_audit(session, **event):
        if event["event_type"] == AuditEventType.DOCUMENT_PERMISSION_SYNC_SUCCESS:
            raise AuditPersistenceError("audit unavailable")
        return original_stage(session, **event)

    monkeypatch.setattr(synchronization, "stage_audit_event", fail_success_audit)
    with test_session_factory() as session:
        with pytest.raises(PermissionSyncPersistenceError):
            process_permission_sync_task(session, task_id=update.sync_task.id, client=qdrant)
    with test_session_factory() as session:
        task = session.get(DocumentPermissionSync, update.sync_task.id)
        assert task.status == PermissionSyncStatus.PROCESSING.value
        assert session.query(AuditLog).filter_by(
            event_type=AuditEventType.DOCUMENT_PERMISSION_SYNC_SUCCESS.value,
            resource_id=str(data["document_id"]),
        ).count() == 0
    assert len(qdrant.set_calls) == 1

    monkeypatch.setattr(synchronization, "stage_audit_event", original_stage)
    monkeypatch.setattr(synchronization, "PROCESSING_LEASE", timedelta(0))
    with test_session_factory() as session:
        outcomes = process_pending_permission_syncs(session, client=qdrant)
        assert len(outcomes) == 1 and outcomes[0].synchronized
    assert len(qdrant.set_calls) == 2
    with test_session_factory() as session:
        task = session.get(DocumentPermissionSync, update.sync_task.id)
        assert task.status == PermissionSyncStatus.COMPLETED.value
        assert session.query(AuditLog).filter_by(
            event_type=AuditEventType.DOCUMENT_PERMISSION_SYNC_SUCCESS.value,
            resource_id=str(data["document_id"]),
        ).count() == 1


def test_empty_private_acl_and_public_are_explicit_complete_replacements(
    auth_client, test_session_factory, monkeypatch,
):
    data = _seed(test_session_factory, document_acl={"allowed_roles": ["HR"]})
    qdrant = FakeQdrant()
    monkeypatch.setattr("app.permissions.synchronization.get_qdrant_client", lambda: qdrant)
    empty = _call_update(auth_client, _payload(), user=data["users"][0],
                         tenant_id=data["tenant_id"], document_id=data["document_id"])
    assert empty.status_code == 200
    assert empty.json()["allowed_roles"] == []
    assert empty.json()["is_public"] is False
    public = _call_update(auth_client, _payload(is_public=True), user=data["users"][0],
                          tenant_id=data["tenant_id"], document_id=data["document_id"])
    assert public.status_code == 200
    assert public.json()["is_public"] is True


def test_qdrant_payload_update_is_scoped_and_does_not_touch_vectors_or_text(
    auth_client, test_session_factory, monkeypatch,
):
    data = _seed(test_session_factory)
    qdrant = FakeQdrant()
    monkeypatch.setattr("app.permissions.synchronization.get_qdrant_client", lambda: qdrant)
    response = _call_update(auth_client, _payload(allowed_roles=["HR"]), user=data["users"][0],
                            tenant_id=data["tenant_id"], document_id=data["document_id"])
    assert response.status_code == 200
    call = qdrant.set_calls[0]
    assert set(call["payload"]) == {
        "tenant_id", "document_id", "classification", "is_public", "allowed_roles", "allowed_users", "uploaded_by",
    }
    assert call["payload"]["allowed_roles"] == ["HR"]
    assert call["points"].must[0].key == "tenant_id"
    assert call["points"].must[0].match.value == str(data["tenant_id"])
    assert call["points"].must[1].key == "document_id"
    assert call["points"].must[1].match.value == str(data["document_id"])
    assert call["wait"] is True


def test_qdrant_failure_keeps_outbox_pending_and_retry_uses_latest_acl(
    auth_client, test_session_factory, monkeypatch,
):
    data = _seed(test_session_factory)
    failed = FakeQdrant(fail=True)
    monkeypatch.setattr("app.permissions.synchronization.get_qdrant_client", lambda: failed)
    response = _call_update(auth_client, _payload(allowed_roles=["HR"]), user=data["users"][0],
                            tenant_id=data["tenant_id"], document_id=data["document_id"])
    assert response.status_code == 200
    assert response.json()["synchronization_status"] == "PENDING"
    with test_session_factory() as session:
        task = session.query(DocumentPermissionSync).filter_by(document_id=data["document_id"]).one()
        assert task.status == PermissionSyncStatus.PENDING.value
        assert task.attempts == 1
        assert task.last_error_code == "QDRANT_SYNC_FAILED"
        _replace_permissions(
            session, data, PermissionReplaceRequest.model_validate(_payload(allowed_roles=["MANAGER"])),
        )
    recovered = FakeQdrant()
    monkeypatch.setattr("app.permissions.synchronization.get_qdrant_client", lambda: recovered)
    with test_session_factory() as session:
        results = process_pending_permission_syncs(session, client=recovered)
        assert len(results) == 1 and results[0].synchronized
    assert recovered.set_calls[0]["payload"]["allowed_roles"] == ["MANAGER"]
    with test_session_factory() as session:
        task = session.query(DocumentPermissionSync).filter_by(document_id=data["document_id"]).one()
        assert task.status == PermissionSyncStatus.COMPLETED.value
        assert task.attempts == 1


def test_actual_failed_task_retry_is_audited_and_completes(
    auth_client, test_session_factory, monkeypatch,
):
    data = _seed(test_session_factory)
    monkeypatch.setattr("app.permissions.synchronization.get_qdrant_client", lambda: FakeQdrant(fail=True))
    response = _call_update(
        auth_client, _payload(allowed_roles=["HR"]), user=data["users"][0],
        tenant_id=data["tenant_id"], document_id=data["document_id"],
    )
    assert response.status_code == 200 and response.json()["synchronization_status"] == "PENDING"
    recovered = FakeQdrant()
    retry_staged = []
    original_stage = synchronization.stage_audit_event

    def capture_retry(session, **event):
        record = original_stage(session, **event)
        if event["event_type"] == AuditEventType.DOCUMENT_PERMISSION_SYNC_RETRY:
            task = next(item for item in session.dirty if isinstance(item, DocumentPermissionSync))
            assert task.status == PermissionSyncStatus.PROCESSING.value
            assert record in session.new
            retry_staged.append(record)
        return record

    monkeypatch.setattr(synchronization, "stage_audit_event", capture_retry)
    with test_session_factory() as session:
        outcome = process_pending_permission_syncs(session, client=recovered)
        assert len(outcome) == 1 and outcome[0].synchronized
    assert retry_staged
    with test_session_factory() as session:
        task = session.query(DocumentPermissionSync).filter_by(document_id=data["document_id"]).one()
        assert task.status == PermissionSyncStatus.COMPLETED.value and task.attempts == 2
        events = session.query(AuditLog).filter(AuditLog.tenant_id == data["tenant_id"]).all()
        event_types = [event.event_type for event in events]
        assert AuditEventType.DOCUMENT_PERMISSION_SYNC_RETRY.value in event_types
        assert AuditEventType.DOCUMENT_PERMISSION_SYNC_SUCCESS.value in event_types


def test_outbox_coalesces_repeated_replacements_and_rejects_noncompleted_documents(
    test_session_factory,
):
    data = _seed(test_session_factory)
    with test_session_factory() as session:
        for role in ("HR", "MANAGER"):
            _replace_permissions(
                session, data, PermissionReplaceRequest.model_validate(_payload(allowed_roles=[role])),
            )
        task = session.query(DocumentPermissionSync).filter_by(document_id=data["document_id"]).one()
        assert task.revision == 2
        assert session.query(DocumentPermissionSync).count() == 1
        document = session.get(Document, data["document_id"])
        document.status = DocumentStatus.PROCESSING
        session.commit()
        with pytest.raises(PermissionDocumentNotReadyError):
            _replace_permissions(
                session, data, PermissionReplaceRequest.model_validate(_payload()),
            )


def test_inflight_old_revision_cannot_mark_a_newer_permission_update_completed(
    test_session_factory, monkeypatch,
):
    data = _seed(test_session_factory)
    with test_session_factory() as session:
        update = _replace_permissions(
            session, data, PermissionReplaceRequest.model_validate(_payload(allowed_roles=["HR"])),
        )

        def simulate_concurrent_update(current_session, task, _client):
            task.revision += 1
            task.status = PermissionSyncStatus.PENDING.value
            document = current_session.get(Document, data["document_id"])
            document.allowed_roles = ["MANAGER"]
            current_session.commit()

        monkeypatch.setattr(synchronization, "_synchronize_latest_acl", simulate_concurrent_update)
        original_stage = synchronization.stage_audit_event
        staged_superseded = []

        def capture_superseded(session, **event):
            record = original_stage(session, **event)
            if event["action"] == "permissions.sync.superseded":
                task = next(item for item in session.dirty if isinstance(item, DocumentPermissionSync))
                assert task.status == PermissionSyncStatus.PENDING.value
                assert record in session.new
                staged_superseded.append(record)
            return record

        monkeypatch.setattr(synchronization, "stage_audit_event", capture_superseded)
        result = process_permission_sync_task(session, task_id=update.sync_task.id, client=FakeQdrant())
        assert not result.synchronized
        assert result.status == PermissionSyncStatus.PENDING
        task = session.get(DocumentPermissionSync, update.sync_task.id)
        assert task.revision == 2
        assert task.status == PermissionSyncStatus.PENDING.value
        assert staged_superseded


def test_permission_changes_and_sync_outcomes_are_audited_without_acl_identifiers_or_content(
    auth_client, test_session_factory, monkeypatch,
):
    data = _seed(test_session_factory)
    qdrant = FakeQdrant(fail=True)
    monkeypatch.setattr("app.permissions.synchronization.get_qdrant_client", lambda: qdrant)
    response = _call_update(auth_client, _payload(allowed_users=[str(data["allowed_user"].id)]),
                            user=data["users"][0], tenant_id=data["tenant_id"],
                            document_id=data["document_id"])
    assert response.status_code == 200
    with test_session_factory() as session:
        events = session.query(AuditLog).filter(AuditLog.tenant_id == data["tenant_id"]).all()
        types = {event.event_type for event in events}
        assert AuditEventType.DOCUMENT_PERMISSION_UPDATE.value in types
        assert AuditEventType.DOCUMENT_PERMISSION_SYNC_FAILURE.value in types
        serialized = str([event.metadata_json for event in events])
        assert str(data["allowed_user"].id) not in serialized
        assert "private.pdf" not in serialized and "document text" not in serialized
        update_event = next(event for event in events if event.event_type == "DOCUMENT_PERMISSION_UPDATE")
        assert update_event.actor_user_id == data["users"][0].id


def test_retrieval_grant_and_revocation_use_postgres_even_if_qdrant_payload_is_stale(
    test_session_factory, monkeypatch,
):
    data = _seed(test_session_factory)
    allowed_user = data["allowed_user"]
    context = TenantContext(allowed_user.id, data["tenant_id"], uuid4(), TenantRole.EMPLOYEE)
    chunk_id = uuid4()
    point = SimpleNamespace(
        id=str(chunk_id), score=0.9,
        payload={
            "tenant_id": str(data["tenant_id"]), "document_id": str(data["document_id"]),
            "chunk_id": str(chunk_id), "chunk_index": 0, "page_numbers": [1],
            "text": "sensitive document chunk", "allowed_users": [str(allowed_user.id)],
            "allowed_roles": [], "is_public": False, "classification": 3,
        },
    )
    monkeypatch.setattr(retrieval_service, "get_embedding_model", lambda: object())
    monkeypatch.setattr(retrieval_service, "embedding_dimension", lambda _: 3)
    monkeypatch.setattr(retrieval_service, "embed_texts", lambda *_: [[0.1, 0.2, 0.3]])
    monkeypatch.setattr(retrieval_service, "initialize_chunk_collection", lambda _: "securerag_chunks")
    monkeypatch.setattr(retrieval_service, "get_qdrant_client", lambda: SimpleNamespace(
        query_points=lambda **_: SimpleNamespace(points=[point]),
    ))
    request = RetrievalSearchRequest(query="policy", top_k=5)
    with test_session_factory() as session:
        denied = retrieval_service.search_documents(session, context=context, request=request)
        assert denied.result_count == 0
        _replace_permissions(
            session, data, PermissionReplaceRequest.model_validate(_payload(allowed_users=[str(allowed_user.id)])),
        )
        granted = retrieval_service.search_documents(session, context=context, request=request)
        assert granted.result_count == 1
        _replace_permissions(
            session, data, PermissionReplaceRequest.model_validate(_payload()),
        )
        revoked = retrieval_service.search_documents(session, context=context, request=request)
        assert revoked.result_count == 0
        assert revoked.results == []


def test_role_acl_grant_and_revoke_change_phase5_results_without_role_bypass(
    auth_client, test_session_factory, monkeypatch,
):
    data = _seed(test_session_factory)
    owner, hr, employee = data["users"][0], data["users"][2], data["users"][4]
    qdrant_for_write = FakeQdrant()
    monkeypatch.setattr("app.permissions.synchronization.get_qdrant_client", lambda: qdrant_for_write)
    update = _call_update(
        auth_client, _payload(allowed_roles=["HR"]), user=owner,
        tenant_id=data["tenant_id"], document_id=data["document_id"],
    )
    assert update.status_code == 200

    chunk_id = uuid4()
    point = SimpleNamespace(
        id=str(chunk_id), score=0.9,
        payload={
            "tenant_id": str(data["tenant_id"]), "document_id": str(data["document_id"]),
            "chunk_id": str(chunk_id), "chunk_index": 0, "page_numbers": [1],
            "text": "role protected chunk", "allowed_users": [], "allowed_roles": ["HR"],
            "is_public": False, "classification": DocumentClassification.RESTRICTED.value,
        },
    )
    search_client = SimpleNamespace(query_points=lambda **_: SimpleNamespace(points=[point]))
    monkeypatch.setattr(retrieval_service, "get_embedding_model", lambda: object())
    monkeypatch.setattr(retrieval_service, "embedding_dimension", lambda _: 3)
    monkeypatch.setattr(retrieval_service, "embed_texts", lambda *_: [[0.1, 0.2, 0.3]])
    monkeypatch.setattr(retrieval_service, "initialize_chunk_collection", lambda _: "securerag_chunks")
    monkeypatch.setattr(retrieval_service, "get_qdrant_client", lambda: search_client)

    search_path = f"/api/v1/tenants/{data['tenant_id']}/retrieval/search"
    hr_response = auth_client.post(search_path, headers=_headers(hr), json={"query": "policy", "top_k": 1})
    employee_response = auth_client.post(
        search_path, headers=_headers(employee), json={"query": "policy", "top_k": 1},
    )
    assert hr_response.status_code == 200 and hr_response.json()["result_count"] == 1
    assert employee_response.status_code == 200 and employee_response.json()["result_count"] == 0

    revoke = _call_update(
        auth_client, _payload(), user=owner,
        tenant_id=data["tenant_id"], document_id=data["document_id"],
    )
    assert revoke.status_code == 200
    stale_qdrant_response = auth_client.post(
        search_path, headers=_headers(hr), json={"query": "policy", "top_k": 1},
    )
    assert stale_qdrant_response.status_code == 200
    assert stale_qdrant_response.json()["result_count"] == 0
