from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.documents import service
from app.documents.chunking import TokenChunker
from app.documents.embeddings import EmbeddingError
from app.documents.models import DocumentStatus
from app.documents.pdf import ExtractedPDF, PDFPage
from app.documents.storage import StoredUpload
from app.documents.vectors import VectorStoreError
from app.tenants.dependencies import TenantContext
from app.tenants.models import Tenant, TenantMembership, TenantRole
from app.users.models import User


class TestTokenizer:
    def encode(self, text, add_special_tokens=False): return text.split()
    def decode(self, tokens, skip_special_tokens=True): return " ".join(tokens)


class TestModel:
    tokenizer = TestTokenizer()
    def get_sentence_embedding_dimension(self): return 2
    def encode(self, texts, **_kwargs): return [[1.0, 0.0] for _ in texts]


def _context(session_factory):
    with session_factory() as session:
        user = User(email="pipeline@example.com", hashed_password="hash", full_name="Pipeline")
        tenant = Tenant(name="Pipeline", slug="pipeline")
        session.add_all([user, tenant])
        session.flush()
        membership = TenantMembership(tenant_id=tenant.id, user_id=user.id, role=TenantRole.HR)
        session.add(membership)
        session.commit()
        return TenantContext(user.id, tenant.id, membership.id, TenantRole.HR)


def _setup(monkeypatch):
    monkeypatch.setattr(service, "extract_pdf", lambda *_args: ExtractedPDF((PDFPage(1, "alpha beta gamma"),), 1))
    model = TestModel()
    monkeypatch.setattr(service, "get_embedding_model", lambda: model)
    monkeypatch.setattr(service, "embed_texts", lambda texts, _model: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(service, "embedding_dimension", lambda _model: 2)
    monkeypatch.setattr(service, "TokenChunker", lambda *_args: TokenChunker(TestTokenizer(), 20, 2))
    monkeypatch.setattr(service, "initialize_chunk_collection", lambda *_args: "chunks")


def _upload(tmp_path: Path) -> StoredUpload:
    path = tmp_path / f"{uuid4()}.pdf"
    path.write_bytes(b"%PDF-1.7")
    return StoredUpload(path, str(uuid4()), path.stat().st_size, "b" * 64)


def test_ingestion_success_persists_completion_only_after_vector_write(
    test_session_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(monkeypatch)
    captured = {}
    def write_vectors(collection, chunks, vectors, **kwargs):
        captured.update(collection=collection, count=len(chunks), vectors=vectors, acl=kwargs)
        return [str(chunk.chunk_id) for chunk in chunks]
    monkeypatch.setattr(service, "upsert_chunks", write_vectors)
    context = _context(test_session_factory)
    with test_session_factory() as session:
        document = service.ingest_document(
            session, context=context, upload=_upload(tmp_path), original_filename="source.pdf",
            classification=service.DocumentClassification.CONFIDENTIAL, is_public=False,
            allowed_roles=["HR"], allowed_users=[],
        )
        assert document.status == DocumentStatus.COMPLETED
        assert document.page_count == 1 and document.chunk_count == 1
        assert captured["acl"]["tenant_id"] == context.tenant_id
        assert captured["acl"]["classification"] == 2


def test_partial_qdrant_failure_attempts_compensating_cleanup(
    test_session_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(monkeypatch)
    inserted_ids = []
    cleaned = {}
    def partially_insert(_collection, chunks, _vectors, **_kwargs):
        inserted_ids.extend(str(chunk.chunk_id) for chunk in chunks)
        raise VectorStoreError("sanitized")
    def cleanup(collection, point_ids):
        cleaned.update(collection=collection, ids=point_ids)
    monkeypatch.setattr(service, "upsert_chunks", partially_insert)
    monkeypatch.setattr(service, "delete_points", cleanup)
    context = _context(test_session_factory)
    with test_session_factory() as session:
        with pytest.raises(service.IngestionFailedError) as error:
            service.ingest_document(
                session, context=context, upload=_upload(tmp_path), original_filename="source.pdf",
                classification=service.DocumentClassification.INTERNAL, is_public=False,
                allowed_roles=[], allowed_users=[],
            )
        persisted = session.get(service.Document, error.value.document.id)
        assert persisted.status == DocumentStatus.FAILED
        assert persisted.failure_detail == "VECTOR_STORAGE_FAILED"
        assert cleaned == {"collection": "chunks", "ids": inserted_ids}


def test_database_finalization_failure_cleans_vectors_and_leaves_failed_status(
    test_session_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup(monkeypatch)
    point_ids = []
    cleaned = []
    monkeypatch.setattr(service, "upsert_chunks", lambda _c, chunks, _v, **_k: point_ids.extend(str(c.chunk_id) for c in chunks) or point_ids)
    monkeypatch.setattr(service, "delete_points", lambda _c, ids: cleaned.extend(ids))
    context = _context(test_session_factory)
    with test_session_factory() as session:
        original_commit = session.commit
        calls = {"count": 0}
        def commit_with_final_failure():
            calls["count"] += 1
            if calls["count"] == 3:
                raise OperationalError("commit", {}, RuntimeError("private db detail"))
            original_commit()
        monkeypatch.setattr(session, "commit", commit_with_final_failure)
        with pytest.raises(service.IngestionFailedError):
            service.ingest_document(
                session, context=context, upload=_upload(tmp_path), original_filename="source.pdf",
                classification=service.DocumentClassification.INTERNAL, is_public=False,
                allowed_roles=[], allowed_users=[],
            )
        failed = session.query(service.Document).one()
        assert failed.status == DocumentStatus.FAILED
        assert failed.failure_detail == "PROCESSING_FAILED"
        assert cleaned == point_ids


def test_embedding_failure_does_not_write_vectors(test_session_factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(monkeypatch)
    monkeypatch.setattr(service, "embed_texts", lambda *_args: (_ for _ in ()).throw(EmbeddingError("private")))
    monkeypatch.setattr(service, "upsert_chunks", lambda *_args, **_kwargs: pytest.fail("vectors must not be written"))
    context = _context(test_session_factory)
    with test_session_factory() as session:
        with pytest.raises(service.IngestionFailedError) as error:
            service.ingest_document(
                session, context=context, upload=_upload(tmp_path), original_filename="source.pdf",
                classification=service.DocumentClassification.INTERNAL, is_public=False,
                allowed_roles=[], allowed_users=[],
            )
        assert error.value.code == "EMBEDDING_FAILED"
