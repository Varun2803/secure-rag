from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth.security import create_access_token, hash_password
from app.config import Settings
from app.documents.storage import StoredUpload
from app.documents.models import Document, DocumentStatus
from app.main import create_app
from app.tenants.models import Tenant, TenantMembership, TenantRole
from app.users.models import User

_SECRET = "document-test-secret-key-that-is-at-least-32-bytes"


def test_document_api_routes_are_registered() -> None:
    paths = create_app().openapi()["paths"]
    assert "/api/v1/tenants/{tenant_id}/documents/upload" in paths
    assert "/api/v1/tenants/{tenant_id}/documents" in paths
    assert "/api/v1/tenants/{tenant_id}/documents/{document_id}" in paths
    assert "/api/v1/tenants/{tenant_id}/documents/{document_id}/status" in paths


def _user(session_factory, email: str) -> User:
    with session_factory() as session:
        user = User(email=email, hashed_password=hash_password("DocumentTestPassword123!"), full_name=email)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def _authorization(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _tenant_with_members(session_factory, users: list[tuple[User, TenantRole]], slug: str) -> Tenant:
    with session_factory() as session:
        tenant = Tenant(name=slug.title(), slug=slug)
        session.add(tenant)
        session.flush()
        for user, role in users:
            session.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role=role))
        session.commit()
        session.refresh(tenant)
        return tenant


def _document_payload(tenant_id, uploaded_by):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid4()), "tenant_id": str(tenant_id), "uploaded_by": str(uploaded_by),
        "filename": f"{uuid4()}.pdf", "original_filename": "safe.pdf", "file_hash": "a" * 64,
        "file_size": 8, "content_type": "application/pdf", "page_count": 1, "chunk_count": 1,
        "status": "COMPLETED", "classification": 1, "is_public": False, "allowed_roles": [],
        "allowed_users": [], "created_at": now, "updated_at": now, "processing_completed_at": now,
    }


@pytest.fixture(autouse=True)
def token_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.auth.security.get_settings", lambda: Settings(secret_key=SecretStr(_SECRET)))


def test_hr_can_upload_and_tenant_identity_comes_from_verified_context(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner, hr = _user(test_session_factory, "doc-owner@example.com"), _user(test_session_factory, "doc-hr@example.com")
    allowed_user = _user(test_session_factory, "doc-allowed@example.com")
    tenant = _tenant_with_members(
        test_session_factory,
        [(owner, TenantRole.OWNER), (hr, TenantRole.HR), (allowed_user, TenantRole.EMPLOYEE)],
        "doc-tenant",
    )
    stored = StoredUpload(tmp_path / "private.pdf", str(uuid4()), 8, "a" * 64)
    stored.path.write_bytes(b"private")
    ingested = {}
    async def fake_store(_upload): return stored
    def fake_ingest(session, **kwargs):
        ingested.update(kwargs)
        return _document_payload(tenant.id, hr.id)
    monkeypatch.setattr("app.documents.routes.store_upload", fake_store)
    monkeypatch.setattr("app.documents.routes.ingest_document", fake_ingest)
    response = auth_client.post(
        f"/api/v1/tenants/{tenant.id}/documents/upload", headers=_authorization(hr),
        files={"file": ("../../private.pdf", b"not parsed in mock", "application/pdf")},
        data={"classification": "2", "allowed_roles": ["EMPLOYEE"], "allowed_users": [str(allowed_user.id)]},
    )
    assert response.status_code == 201
    assert response.json()["tenant_id"] == str(tenant.id)
    assert response.json()["uploaded_by"] == str(hr.id)
    assert ingested["original_filename"] == "private.pdf"
    assert ingested["allowed_users"] == [str(allowed_user.id)]
    assert stored.path.exists()
    assert "storage_key" not in response.json() and "path" not in response.json()


def test_employee_upload_is_denied_before_file_storage(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, employee = _user(test_session_factory, "emp-owner@example.com"), _user(test_session_factory, "employee@example.com")
    tenant = _tenant_with_members(test_session_factory, [(owner, TenantRole.OWNER), (employee, TenantRole.EMPLOYEE)], "employee-docs")
    monkeypatch.setattr("app.documents.routes.store_upload", lambda *_: pytest.fail("must reject before storage"))
    response = auth_client.post(
        f"/api/v1/tenants/{tenant.id}/documents/upload", headers=_authorization(employee),
        files={"file": ("doc.pdf", b"%PDF-1.7", "application/pdf")},
    )
    assert response.status_code == 403


def test_cross_tenant_upload_is_rejected(auth_client: TestClient, test_session_factory) -> None:
    alice, bob = _user(test_session_factory, "alice-doc@example.com"), _user(test_session_factory, "bob-doc@example.com")
    _tenant_with_members(test_session_factory, [(alice, TenantRole.OWNER)], "alice-docs")
    bob_tenant = _tenant_with_members(test_session_factory, [(bob, TenantRole.OWNER)], "bob-docs")
    response = auth_client.post(
        f"/api/v1/tenants/{bob_tenant.id}/documents/upload", headers=_authorization(alice),
        files={"file": ("doc.pdf", b"%PDF-1.7", "application/pdf")},
    )
    assert response.status_code == 404


def test_inactive_membership_cannot_list_tenant_documents(
    auth_client: TestClient, test_session_factory
) -> None:
    owner, member = _user(test_session_factory, "inactive-owner@example.com"), _user(test_session_factory, "inactive-doc@example.com")
    tenant = _tenant_with_members(test_session_factory, [(owner, TenantRole.OWNER), (member, TenantRole.HR)], "inactive-docs")
    with test_session_factory() as session:
        membership = session.query(TenantMembership).filter_by(tenant_id=tenant.id, user_id=member.id).one()
        membership.is_active = False
        session.commit()
    response = auth_client.get(f"/api/v1/tenants/{tenant.id}/documents", headers=_authorization(member))
    assert response.status_code == 404


def test_document_list_details_and_status_remain_tenant_scoped(
    auth_client: TestClient, test_session_factory
) -> None:
    owner, hr = _user(test_session_factory, "read-owner@example.com"), _user(test_session_factory, "read-hr@example.com")
    tenant = _tenant_with_members(test_session_factory, [(owner, TenantRole.OWNER), (hr, TenantRole.HR)], "read-docs")
    with test_session_factory() as session:
        document = Document(
            tenant_id=tenant.id, uploaded_by=hr.id, filename=f"{uuid4()}.pdf", original_filename="report.pdf",
            file_hash="f" * 64, file_size=20, content_type="application/pdf",
            status=DocumentStatus.FAILED, failure_detail="PDF_INVALID",
        )
        session.add(document)
        session.commit()
        document_id = document.id
    headers = _authorization(hr)
    root = f"/api/v1/tenants/{tenant.id}/documents"
    listing = auth_client.get(root, headers=headers)
    detail = auth_client.get(f"{root}/{document_id}", headers=headers)
    status_response = auth_client.get(f"{root}/{document_id}/status", headers=headers)
    assert listing.status_code == 200 and listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == str(document_id)
    assert detail.status_code == 200 and detail.json()["id"] == str(document_id)
    assert "storage_key" not in detail.json() and "path" not in detail.json()
    assert status_response.status_code == 200
    assert status_response.json()["failure_detail"] == "PDF_INVALID"


def test_oversized_upload_is_rejected_and_temporary_file_removed(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner, hr = _user(test_session_factory, "size-owner@example.com"), _user(test_session_factory, "size-hr@example.com")
    tenant = _tenant_with_members(test_session_factory, [(owner, TenantRole.OWNER), (hr, TenantRole.HR)], "size-docs")
    settings = SimpleNamespace(document_storage_path=str(tmp_path), max_upload_size_bytes=4)
    monkeypatch.setattr("app.documents.storage.get_settings", lambda: settings)
    monkeypatch.setattr("app.documents.routes.get_settings", lambda: SimpleNamespace(max_filename_length=255))
    response = auth_client.post(
        f"/api/v1/tenants/{tenant.id}/documents/upload", headers=_authorization(hr),
        files={"file": ("too-large.pdf", b"12345", "application/pdf")},
    )
    assert response.status_code == 413
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("invalid_case", ["foreign", "inactive-user", "inactive-membership", "missing", "duplicate"])
def test_acl_assignment_requires_active_current_tenant_membership(
    invalid_case: str, auth_client: TestClient, test_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.documents.service import IngestionFailedError

    owner = _user(test_session_factory, f"acl-owner-{invalid_case}@example.com")
    hr = _user(test_session_factory, f"acl-hr-{invalid_case}@example.com")
    target = _user(test_session_factory, f"acl-target-{invalid_case}@example.com")
    members = [(owner, TenantRole.OWNER), (hr, TenantRole.HR)]
    if invalid_case not in {"foreign", "missing"}:
        members.append((target, TenantRole.EMPLOYEE))
    tenant = _tenant_with_members(test_session_factory, members, f"acl-{invalid_case}")
    if invalid_case == "foreign":
        _tenant_with_members(test_session_factory, [(target, TenantRole.EMPLOYEE)], "acl-foreign-only")
    elif invalid_case == "inactive-user":
        with test_session_factory() as session:
            session.get(User, target.id).is_active = False
            session.commit()
    elif invalid_case == "inactive-membership":
        with test_session_factory() as session:
            session.query(TenantMembership).filter_by(tenant_id=tenant.id, user_id=target.id).one().is_active = False
            session.commit()

    selected_user_id = uuid4() if invalid_case == "missing" else target.id
    allowed = [str(selected_user_id)] * (2 if invalid_case == "duplicate" else 1)
    monkeypatch.setattr("app.documents.routes.store_upload", lambda *_: pytest.fail("ACL validation must precede storage"))
    monkeypatch.setattr("app.documents.routes.ingest_document", lambda *_args, **_kwargs: pytest.fail("invalid ACL must not be ingested"))
    response = auth_client.post(
        f"/api/v1/tenants/{tenant.id}/documents/upload", headers=_authorization(hr),
        files={"file": ("document.pdf", b"%PDF-1.7", "application/pdf")},
        data={"allowed_users": allowed},
    )
    assert response.status_code == 422
    assert "Invalid document access" in response.text or "Duplicate document access" in response.text


@pytest.mark.parametrize("failure_code", ["PDF_INVALID", "EMBEDDING_FAILED", "VECTOR_STORAGE_FAILED"])
def test_ingestion_failures_remove_stored_pdf(
    failure_code: str, auth_client: TestClient, test_session_factory,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from app.documents.service import IngestionFailedError

    owner, hr = _user(test_session_factory, f"cleanup-owner-{failure_code}@example.com"), _user(test_session_factory, f"cleanup-hr-{failure_code}@example.com")
    tenant = _tenant_with_members(test_session_factory, [(owner, TenantRole.OWNER), (hr, TenantRole.HR)], f"cleanup-{failure_code.lower()}")
    stored = StoredUpload(tmp_path / f"{failure_code}.pdf", str(uuid4()), 8, "c" * 64)
    stored.path.write_bytes(b"private")
    async def fake_store(_upload): return stored
    monkeypatch.setattr("app.documents.routes.store_upload", fake_store)
    monkeypatch.setattr(
        "app.documents.routes.ingest_document",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(IngestionFailedError(SimpleNamespace(id=uuid4()), failure_code)),
    )
    response = auth_client.post(
        f"/api/v1/tenants/{tenant.id}/documents/upload", headers=_authorization(hr),
        files={"file": ("document.pdf", b"%PDF-1.7", "application/pdf")},
    )
    assert response.status_code in {422, 503}
    assert not stored.path.exists()
    assert failure_code in response.text


def test_duplicate_and_unexpected_failures_remove_stored_files(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.documents.service import DuplicateDocumentError

    owner, hr = _user(test_session_factory, "cleanup-other-owner@example.com"), _user(test_session_factory, "cleanup-other-hr@example.com")
    tenant = _tenant_with_members(test_session_factory, [(owner, TenantRole.OWNER), (hr, TenantRole.HR)], "cleanup-other")
    stored = StoredUpload(tmp_path / "duplicate.pdf", str(uuid4()), 8, "d" * 64)
    stored.path.write_bytes(b"private")
    async def fake_store(_upload): return stored
    monkeypatch.setattr("app.documents.routes.store_upload", fake_store)
    monkeypatch.setattr("app.documents.routes.ingest_document", lambda *_args, **_kwargs: (_ for _ in ()).throw(DuplicateDocumentError()))
    duplicate = auth_client.post(
        f"/api/v1/tenants/{tenant.id}/documents/upload", headers=_authorization(hr),
        files={"file": ("document.pdf", b"%PDF-1.7", "application/pdf")},
    )
    assert duplicate.status_code == 409
    assert not stored.path.exists()

    stored.path.write_bytes(b"private")
    monkeypatch.setattr("app.documents.routes.ingest_document", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("private error")))
    unexpected = auth_client.post(
        f"/api/v1/tenants/{tenant.id}/documents/upload", headers=_authorization(hr),
        files={"file": ("document.pdf", b"%PDF-1.7", "application/pdf")},
    )
    assert unexpected.status_code == 503
    assert not stored.path.exists()
    assert "private error" not in unexpected.text


def test_cleanup_failure_logs_no_path_and_preserves_sanitized_response(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    from app.documents.service import IngestionFailedError

    owner, hr = _user(test_session_factory, "cleanup-fail-owner@example.com"), _user(test_session_factory, "cleanup-fail-hr@example.com")
    tenant = _tenant_with_members(test_session_factory, [(owner, TenantRole.OWNER), (hr, TenantRole.HR)], "cleanup-fail")
    stored = StoredUpload(tmp_path / "sensitive-private-name.pdf", str(uuid4()), 8, "e" * 64)
    stored.path.write_bytes(b"private")
    async def fake_store(_upload): return stored
    monkeypatch.setattr("app.documents.routes.store_upload", fake_store)
    monkeypatch.setattr(
        "app.documents.routes.ingest_document",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(IngestionFailedError(SimpleNamespace(id=uuid4()), "EMBEDDING_FAILED")),
    )
    original_unlink = Path.unlink
    def failed_unlink(path: Path, *args, **kwargs):
        if path == stored.path:
            raise OSError("C:\\private\\sensitive-private-name.pdf")
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", failed_unlink)
    response = auth_client.post(
        f"/api/v1/tenants/{tenant.id}/documents/upload", headers=_authorization(hr),
        files={"file": ("document.pdf", b"%PDF-1.7", "application/pdf")},
    )
    assert response.status_code == 503
    assert "sensitive-private-name" not in caplog.text
    assert "C:\\private" not in response.text
