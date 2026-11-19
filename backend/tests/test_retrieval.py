from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth.security import create_access_token, hash_password
from app.config import Settings
from app.documents.models import Document, DocumentStatus
from app.retrieval import security, service
from app.retrieval.schemas import RetrievalSearchRequest, RetrievalSearchResponse, RetrievalResult
from app.retrieval.service import RetrievalUnavailableError
from app.tenants.dependencies import TenantContext
from app.tenants.models import Tenant, TenantMembership, TenantRole
from app.users.models import User

_SECRET = "retrieval-test-secret-key-with-more-than-32-bytes"


def _identity(session_factory, role: TenantRole = TenantRole.HR, suffix: str = "a") -> tuple[TenantContext, UUID]:
    with session_factory() as session:
        user = User(email=f"retrieval-{suffix}@example.com", hashed_password="test-hash", full_name="Retriever")
        tenant = Tenant(name=f"Retrieval {suffix}", slug=f"retrieval-{suffix}")
        session.add_all([user, tenant])
        session.flush()
        membership = TenantMembership(tenant_id=tenant.id, user_id=user.id, role=role)
        session.add(membership)
        session.commit()
        return TenantContext(user.id, tenant.id, membership.id, role), tenant.id


def _document(
    session_factory,
    *,
    context: TenantContext,
    status: DocumentStatus = DocumentStatus.COMPLETED,
    is_public: bool = False,
    allowed_roles: list[str] | None = None,
    allowed_users: list[str] | None = None,
    uploader: UUID | None = None,
    classification: int = 1,
    suffix: str = "doc",
) -> UUID:
    document_id = uuid4()
    with session_factory() as session:
        session.add(Document(
            id=document_id,
            tenant_id=context.tenant_id,
            uploaded_by=uploader or context.user_id,
            filename=f"{uuid4()}.pdf",
            original_filename=f"{suffix}.pdf",
            file_hash=uuid4().hex,
            file_size=100,
            content_type="application/pdf",
            status=status,
            classification=classification,
            is_public=is_public,
            allowed_roles=allowed_roles or [],
            allowed_users=allowed_users or [],
        ))
        session.commit()
    return document_id


def _point(
    *, context: TenantContext, document_id: UUID, text: str = "authorized chunk",
    chunk_id: UUID | None = None, score: float = 0.9, tenant_id: UUID | None = None,
    allowed_roles: list[str] | None = None, allowed_users: list[str] | None = None,
):
    chunk_id = chunk_id or uuid4()
    return SimpleNamespace(
        id=str(chunk_id),
        score=score,
        payload={
            "chunk_id": str(chunk_id), "document_id": str(document_id),
            "tenant_id": str(tenant_id or context.tenant_id), "uploaded_by": str(context.user_id),
            "chunk_index": 0, "page_number": 1, "page_numbers": [1],
            "classification": 1, "is_public": False,
            "allowed_roles": allowed_roles or [], "allowed_users": allowed_users or [], "text": text,
        },
    )


class FakeQdrant:
    def __init__(self, points=None):
        self.points = points or []
        self.calls = []

    def query_points(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(points=self.points)


def _mock_search_backends(monkeypatch: pytest.MonkeyPatch, client: FakeQdrant) -> None:
    model = object()
    monkeypatch.setattr(service, "get_embedding_model", lambda: model)
    monkeypatch.setattr(service, "embedding_dimension", lambda _model: 3)
    monkeypatch.setattr(service, "embed_texts", lambda texts, _model: [[0.1, 0.2, 0.3] for _ in texts])
    monkeypatch.setattr(service, "initialize_chunk_collection", lambda dimension: "securerag_chunks")
    monkeypatch.setattr(service, "get_qdrant_client", lambda: client)


def _search(session_factory, monkeypatch, context, points, top_k=5):
    client = FakeQdrant(points)
    _mock_search_backends(monkeypatch, client)
    request = RetrievalSearchRequest(query="  leave policy  ", top_k=top_k)
    with session_factory() as session:
        response = service.search_documents(session, context=context, request=request)
    return response, client


@pytest.fixture(autouse=True)
def token_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.auth.security.get_settings", lambda: Settings(secret_key=SecretStr(_SECRET)))


def test_tenant_acl_filter_uses_verified_context_and_or_conditions() -> None:
    context = TenantContext(uuid4(), uuid4(), uuid4(), TenantRole.MANAGER)
    query_filter = security.build_tenant_acl_filter(context)
    assert len(query_filter.must) == 1
    assert query_filter.must[0].key == "tenant_id"
    assert query_filter.must[0].match.value == str(context.tenant_id)
    assert [condition.key for condition in query_filter.should] == ["is_public", "allowed_roles", "allowed_users"]
    assert [condition.match.value for condition in query_filter.should] == [True, "MANAGER", str(context.user_id)]


@pytest.mark.parametrize(
    "is_public,allowed_roles,allowed_users,expected",
    [
        (True, [], [], True),
        (False, ["HR"], [], True),
        (False, [], ["MATCH"], True),
        (False, [], [], False),
    ],
)
def test_postgres_acl_is_authoritative(
    test_session_factory, monkeypatch: pytest.MonkeyPatch,
    is_public: bool, allowed_roles: list[str], allowed_users: list[str], expected: bool,
) -> None:
    context, _ = _identity(test_session_factory, suffix=f"acl-{uuid4().hex[:6]}")
    document_id = _document(
        test_session_factory, context=context, is_public=is_public,
        allowed_roles=allowed_roles, allowed_users=[str(context.user_id)] if allowed_users else [],
    )
    response, client = _search(
        test_session_factory, monkeypatch, context,
        [_point(context=context, document_id=document_id, allowed_roles=["STALE"], allowed_users=[])],
    )
    assert bool(response.results) is expected
    assert client.calls[0]["query"] == [0.1, 0.2, 0.3]
    assert client.calls[0]["limit"] == 25
    assert client.calls[0]["with_payload"] is True
    assert client.calls[0]["with_vectors"] is False


@pytest.mark.parametrize("role", [TenantRole.OWNER, TenantRole.ADMIN])
def test_owner_admin_and_classification_do_not_grant_access(test_session_factory, monkeypatch: pytest.MonkeyPatch, role) -> None:
    context, _ = _identity(test_session_factory, role=role, suffix=f"privileged-{role.value.lower()}")
    document_id = _document(test_session_factory, context=context, classification=0)
    response, _ = _search(test_session_factory, monkeypatch, context, [_point(context=context, document_id=document_id)])
    assert response.results == []


def test_uploader_is_not_implicitly_authorized(test_session_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    context, _ = _identity(test_session_factory, role=TenantRole.HR, suffix="uploader-deny")
    document_id = _document(test_session_factory, context=context, uploader=context.user_id)
    response, _ = _search(test_session_factory, monkeypatch, context, [_point(context=context, document_id=document_id)])
    assert response.results == []


@pytest.mark.parametrize("status", [DocumentStatus.PENDING, DocumentStatus.PROCESSING, DocumentStatus.FAILED])
def test_only_completed_documents_are_retrievable(test_session_factory, monkeypatch: pytest.MonkeyPatch, status) -> None:
    context, _ = _identity(test_session_factory, suffix=f"status-{status.value.lower()}")
    document_id = _document(test_session_factory, context=context, is_public=True, status=status)
    response, _ = _search(test_session_factory, monkeypatch, context, [_point(context=context, document_id=document_id)])
    assert response.results == []


def test_missing_database_document_and_cross_tenant_candidates_are_discarded(
    test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, _ = _identity(test_session_factory, suffix="discard-a")
    other_context, _ = _identity(test_session_factory, suffix="discard-b")
    foreign_document = _document(test_session_factory, context=other_context, is_public=True)
    points = [
        _point(context=context, document_id=foreign_document, tenant_id=other_context.tenant_id, text="secret B"),
        _point(context=context, document_id=uuid4(), text="missing row secret"),
    ]
    response, client = _search(test_session_factory, monkeypatch, context, points)
    assert response.results == []
    assert client.calls[0]["query_filter"].must[0].match.value == str(context.tenant_id)


def test_stale_qdrant_acl_cannot_grant_postgres_denied_document(
    test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, _ = _identity(test_session_factory, suffix="stale-acl")
    document_id = _document(test_session_factory, context=context, allowed_roles=[])
    stale_hit = _point(
        context=context, document_id=document_id,
        allowed_roles=[context.role.value], allowed_users=[str(context.user_id)],
        text="must not be authorized by stale Qdrant ACL",
    )
    response, _ = _search(test_session_factory, monkeypatch, context, [stale_hit])
    assert response.results == []


def test_unauthorized_candidates_do_not_occupy_top_k_and_chunks_are_deduplicated(
    test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, _ = _identity(test_session_factory, suffix="ranking")
    denied_document = _document(test_session_factory, context=context, suffix="denied")
    allowed_document = _document(test_session_factory, context=context, is_public=True, suffix="allowed")
    repeated_chunk = uuid4()
    points = [
        _point(context=context, document_id=denied_document, text="must never leak", score=0.99),
        _point(context=context, document_id=allowed_document, chunk_id=repeated_chunk, text="public text", score=0.7),
        _point(context=context, document_id=allowed_document, chunk_id=repeated_chunk, text="public text", score=0.8),
    ]
    response, _ = _search(test_session_factory, monkeypatch, context, points, top_k=1)
    assert response.result_count == 1
    assert response.results[0].text == "public text"
    assert response.results[0].similarity_score == 0.8
    assert "must never leak" not in str(response.model_dump())


def test_unauthorized_duplicate_does_not_suppress_an_authorized_chunk(
    test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, _ = _identity(test_session_factory, suffix="duplicate-acl")
    denied_document = _document(test_session_factory, context=context, suffix="denied-duplicate")
    allowed_document = _document(test_session_factory, context=context, is_public=True, suffix="allowed-duplicate")
    shared_chunk = uuid4()
    response, _ = _search(
        test_session_factory, monkeypatch, context,
        [
            _point(context=context, document_id=denied_document, chunk_id=shared_chunk,
                   text="unauthorized collision", score=0.99),
            _point(context=context, document_id=allowed_document, chunk_id=shared_chunk,
                   text="authorized collision", score=0.7),
        ],
    )
    assert response.result_count == 1
    assert response.results[0].text == "authorized collision"


def test_malformed_qdrant_payload_and_empty_results_are_safe(
    test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, _ = _identity(test_session_factory, suffix="malformed")
    bad = SimpleNamespace(id="bad", score=float("nan"), payload={"text": "unsafe"})
    response, _ = _search(test_session_factory, monkeypatch, context, [bad])
    assert response.results == []
    empty, _ = _search(test_session_factory, monkeypatch, context, [])
    assert empty.results == [] and empty.result_count == 0


@pytest.mark.parametrize("bad_vectors", [[], [[1.0, 2.0]], [[float("nan"), 0.0, 1.0]], [[float("inf"), 0.0, 1.0]]])
def test_invalid_query_vectors_return_unavailable(
    test_session_factory, monkeypatch: pytest.MonkeyPatch, bad_vectors
) -> None:
    context, _ = _identity(test_session_factory, suffix=f"vector-{uuid4().hex[:6]}")
    client = FakeQdrant()
    _mock_search_backends(monkeypatch, client)
    monkeypatch.setattr(service, "embed_texts", lambda *_args: bad_vectors)
    with test_session_factory() as session, pytest.raises(RetrievalUnavailableError):
        service.search_documents(session, context=context, request=RetrievalSearchRequest(query="query"))
    assert client.calls == []


def test_embedding_failure_is_sanitized(test_session_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    context, _ = _identity(test_session_factory, suffix="embed-failure")
    # Existing embedding helper wraps model load exceptions, so call it with its public failure type.
    from app.documents.embeddings import EmbeddingError
    monkeypatch.setattr(service, "get_embedding_model", lambda: (_ for _ in ()).throw(EmbeddingError("private")))
    with test_session_factory() as session, pytest.raises(RetrievalUnavailableError) as error:
        service.search_documents(session, context=context, request=RetrievalSearchRequest(query="query"))
    assert "private" not in str(error.value)


def test_qdrant_connection_failure_is_sanitized(test_session_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    context, _ = _identity(test_session_factory, suffix="qdrant-failure")
    client = FakeQdrant()
    client.query_points = lambda **_kwargs: (_ for _ in ()).throw(httpx.ConnectError("private endpoint"))
    _mock_search_backends(monkeypatch, client)
    with test_session_factory() as session, pytest.raises(RetrievalUnavailableError) as error:
        service.search_documents(session, context=context, request=RetrievalSearchRequest(query="query"))
    assert "private endpoint" not in str(error.value)


def test_candidate_retrieval_limit_is_bounded(test_session_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    context, _ = _identity(test_session_factory, suffix="limit")
    _, client = _search(test_session_factory, monkeypatch, context, [], top_k=20)
    assert client.calls[0]["limit"] == 100


def test_search_request_rejects_empty_long_and_untrusted_fields() -> None:
    for payload in (
        {"query": "   "},
        {"query": "x" * 2001},
        {"query": "hello", "top_k": 0},
        {"query": "hello", "top_k": 21},
        {"query": "hello", "tenant_id": str(uuid4())},
        {"query": "hello", "user_id": str(uuid4())},
        {"query": "hello", "role": "OWNER"},
        {"query": "hello", "allowed_users": [str(uuid4())]},
    ):
        with pytest.raises(ValueError):
            RetrievalSearchRequest.model_validate(payload)


def test_tenant_context_acl_authorization_uses_current_database_state() -> None:
    context = TenantContext(uuid4(), uuid4(), uuid4(), TenantRole.HR)
    assert security.can_access_document(SimpleNamespace(
        tenant_id=context.tenant_id, status=DocumentStatus.COMPLETED,
        is_public=False, allowed_roles=["HR"], allowed_users=[]
    ), context)
    assert security.can_access_document(SimpleNamespace(
        tenant_id=context.tenant_id, status=DocumentStatus.COMPLETED,
        is_public=False, allowed_roles=[], allowed_users=[str(context.user_id)]
    ), context)
    assert not security.can_access_document(SimpleNamespace(
        tenant_id=uuid4(), status=DocumentStatus.COMPLETED,
        is_public=True, allowed_roles=[], allowed_users=[]
    ), context)


def _api_identity(session_factory, email: str = "retrieval-api@example.com") -> tuple[User, Tenant]:
    with session_factory() as session:
        user = User(email=email, hashed_password=hash_password("RetrievalTestPassword123!"), full_name="API User")
        tenant = Tenant(name="Retrieval API", slug=f"api-{uuid4().hex[:8]}")
        session.add_all([user, tenant])
        session.flush()
        session.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role=TenantRole.HR))
        session.commit()
        session.refresh(user)
        session.refresh(tenant)
        return user, tenant


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def test_retrieval_api_auth_tenant_match_and_response_schema(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, tenant = _api_identity(test_session_factory)
    response_model = RetrievalSearchResponse(
        query="leave policy", tenant_id=tenant.id,
        results=[RetrievalResult(chunk_id=uuid4(), document_id=uuid4(), chunk_index=1,
                                 page_numbers=[2], text="allowed content", similarity_score=0.92)],
        result_count=1,
    )
    monkeypatch.setattr("app.retrieval.routes.search_documents", lambda *_args, **_kwargs: response_model)
    url = f"/api/v1/tenants/{tenant.id}/retrieval/search"
    missing = auth_client.post(url, json={"query": "leave"})
    invalid = auth_client.post(url, headers={"Authorization": "Bearer invalid"}, json={"query": "leave"})
    valid = auth_client.post(url, headers=_auth(user), json={"query": "leave policy", "top_k": 5})
    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert valid.status_code == 200
    assert valid.json()["tenant_id"] == str(tenant.id)
    assert valid.json()["result_count"] == 1
    assert set(valid.json()["results"][0]) == {
        "chunk_id", "document_id", "chunk_index", "page_numbers", "text", "similarity_score"
    }


def test_unauthorized_qdrant_candidate_never_reaches_api_response(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, tenant = _api_identity(test_session_factory, "retrieval-no-leak@example.com")
    with test_session_factory() as session:
        membership = session.query(TenantMembership).filter_by(tenant_id=tenant.id, user_id=user.id).one()
        context = TenantContext(user.id, tenant.id, membership.id, TenantRole.HR)
    document_id = _document(test_session_factory, context=context, is_public=False)
    qdrant = FakeQdrant([_point(context=context, document_id=document_id, text="unauthorized payroll secret")])
    _mock_search_backends(monkeypatch, qdrant)
    response = auth_client.post(
        f"/api/v1/tenants/{tenant.id}/retrieval/search", headers=_auth(user),
        json={"query": "payroll", "top_k": 5},
    )
    assert response.status_code == 200
    assert response.json()["results"] == []
    assert "unauthorized payroll secret" not in response.text


def test_retrieval_api_route_is_registered() -> None:
    from app.main import create_app

    assert "/api/v1/tenants/{tenant_id}/retrieval/search" in create_app().openapi()["paths"]


def test_inactive_user_tenant_mismatch_and_client_tenant_override_are_rejected(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, tenant = _api_identity(test_session_factory, "retrieval-mismatch@example.com")
    monkeypatch.setattr("app.retrieval.routes.search_documents", lambda *_args, **_kwargs: pytest.fail("must not search"))
    url = f"/api/v1/tenants/{uuid4()}/retrieval/search"
    mismatch = auth_client.post(url, headers=_auth(user), json={"query": "hello"})
    override = auth_client.post(
        f"/api/v1/tenants/{tenant.id}/retrieval/search", headers=_auth(user),
        json={"query": "hello", "tenant_id": str(uuid4())},
    )
    with test_session_factory() as session:
        session.get(User, user.id).is_active = False
        session.commit()
    inactive = auth_client.post(
        f"/api/v1/tenants/{tenant.id}/retrieval/search", headers=_auth(user), json={"query": "hello"}
    )
    assert mismatch.status_code == 404
    assert override.status_code == 422
    assert inactive.status_code == 401


def test_search_api_rejects_malformed_requests_with_422(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, tenant = _api_identity(test_session_factory, "retrieval-invalid-input@example.com")
    monkeypatch.setattr("app.retrieval.routes.search_documents", lambda *_args, **_kwargs: pytest.fail("invalid query must not search"))
    url = f"/api/v1/tenants/{tenant.id}/retrieval/search"
    bad_payloads = [
        {"query": "   "},
        {"query": "x" * 2001},
        {"query": "hello", "top_k": 0},
        {"query": "hello", "top_k": 21},
        {"query": "hello", "tenant_id": str(uuid4())},
        {"query": "hello", "role": "OWNER"},
        {"query": "hello", "allowed_users": [str(uuid4())]},
    ]
    for payload in bad_payloads:
        response = auth_client.post(url, headers=_auth(user), json=payload)
        assert response.status_code == 422


def test_retrieval_route_maps_unavailable_to_sanitized_503(
    auth_client: TestClient, test_session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    user, tenant = _api_identity(test_session_factory, "retrieval-down@example.com")
    monkeypatch.setattr(
        "app.retrieval.routes.search_documents",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RetrievalUnavailableError("private backend detail")),
    )
    response = auth_client.post(
        f"/api/v1/tenants/{tenant.id}/retrieval/search", headers=_auth(user), json={"query": "benefits"}
    )
    assert response.status_code == 503
    assert "private backend detail" not in response.text
