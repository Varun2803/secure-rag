from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.audit.service import AuditMetadataError, sanitize_metadata
from app.config import Settings, validate_llm_endpoint
from app.documents.models import DocumentStatus
from app.generation.citations import validate_answer_payload
from app.generation.context import AuthorizedContext, ContextSource
from app.tenants.dependencies import TenantContext
from app.tenants.models import TenantRole
from app.retrieval.security import build_tenant_acl_filter, can_access_document


def test_qdrant_filter_uses_verified_tenant_and_acl_or_conditions() -> None:
    user_id, tenant_id = uuid4(), uuid4()
    context = TenantContext(user_id, tenant_id, uuid4(), TenantRole.HR)
    query_filter = build_tenant_acl_filter(context)
    assert query_filter.must[0].key == "tenant_id"
    assert query_filter.must[0].match.value == str(tenant_id)
    assert {condition.key for condition in query_filter.should} == {
        "is_public", "allowed_roles", "allowed_users",
    }


def test_database_acl_denies_cross_tenant_uploader_and_role_bypass() -> None:
    user_id, tenant_id = uuid4(), uuid4()
    context = TenantContext(user_id, tenant_id, uuid4(), TenantRole.OWNER)
    document = SimpleNamespace(
        tenant_id=tenant_id,
        status=DocumentStatus.COMPLETED,
        is_public=False,
        allowed_roles=[],
        allowed_users=[],
        uploaded_by=user_id,
    )
    assert not can_access_document(document, context)
    document.allowed_roles = [TenantRole.OWNER.value]
    assert can_access_document(document, context)
    document.tenant_id = uuid4()
    assert not can_access_document(document, context)


def test_audit_metadata_rejects_secrets_and_document_content() -> None:
    for sensitive in ({"password": "secret"}, {"api_key": "secret"}, {"document_text": "private"}):
        with pytest.raises(AuditMetadataError):
            sanitize_metadata(sensitive)
    assert sanitize_metadata({"top_k": 5, "result_count": 2}) == {"top_k": 5, "result_count": 2}


def test_remote_llm_requires_tls_and_local_http_is_development_loopback_only() -> None:
    validate_llm_endpoint("https://provider.example/v1", "production")
    with pytest.raises(ValueError):
        validate_llm_endpoint("http://provider.example/v1", "production")
    with pytest.raises(ValueError):
        validate_llm_endpoint("http://10.0.0.8:9000/v1", "development")
    local = Settings(llm_api_key=SecretStr("not-a-real-key"), llm_base_url="http://localhost:9000/v1")
    assert local.llm_base_url.startswith("http://localhost")


def test_generation_unknown_citation_abstains_without_accepting_model_metadata() -> None:
    source = ContextSource("S1", uuid4(), uuid4(), (2,), 0.8, "approved content")
    context = AuthorizedContext((source,), "", 1)
    answer = validate_answer_payload(
        {"answer": "Answer [S2]", "citations": ["S2"], "insufficient_information": False},
        context,
    )
    assert answer.grounded is False
    assert answer.citations == ()
