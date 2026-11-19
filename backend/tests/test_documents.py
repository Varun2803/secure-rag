from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from qdrant_client import models as qdrant_models
from sqlalchemy.exc import IntegrityError

from app.documents import embeddings, pdf, vectors
from app.documents.chunking import DocumentChunk, TokenChunker
from app.documents.models import Document, DocumentClassification, DocumentStatus
from app.documents.pdf import PDFPage
from app.documents.vectors import VectorStoreError
from app.tenants.models import Tenant, TenantMembership, TenantRole
from app.users.models import User


class WordTokenizer:
    def encode(self, text: str, add_special_tokens: bool = False) -> list[str]:
        return text.split()

    def decode(self, tokens: list[str], skip_special_tokens: bool = True) -> str:
        return " ".join(tokens)


def test_chunking_is_deterministic_and_preserves_page_and_document_identity() -> None:
    document_id, tenant_id = uuid4(), uuid4()
    chunker = TokenChunker(WordTokenizer(), chunk_size=4, overlap=1)
    pages = (PDFPage(4, "one two three four five"), PDFPage(5, "six seven"))
    first = chunker.chunk_pages(pages, document_id, tenant_id)
    second = chunker.chunk_pages(pages, document_id, tenant_id)
    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert [chunk.chunk_index for chunk in first] == list(range(len(first)))
    assert [chunk.page_numbers for chunk in first] == [(4,), (4,), (5,)]
    assert first[0].text == "one two three four"
    assert first[1].text.startswith("four five")
    assert all(chunk.document_id == document_id and chunk.tenant_id == tenant_id for chunk in first)


def test_chunking_rejects_empty_extracted_text() -> None:
    with pytest.raises(ValueError):
        TokenChunker(WordTokenizer()).chunk_pages((PDFPage(1, "  "),), uuid4(), uuid4())


def test_chunking_keeps_sentence_boundaries_when_possible() -> None:
    chunks = TokenChunker(WordTokenizer(), chunk_size=5, overlap=2).chunk_pages(
        (PDFPage(1, "First sentence. Second sentence. Third sentence."),), uuid4(), uuid4()
    )
    assert chunks[0].text == "First sentence. Second sentence."
    assert chunks[1].text == "Second sentence. Third sentence."
    assert chunks[-1].text.endswith("Third sentence.")


def test_pdf_validation_checks_signature_and_mime_before_parser(tmp_path: Path) -> None:
    path = tmp_path / "example.pdf"
    path.write_bytes(b"")
    with pytest.raises(pdf.InvalidPDFError, match="empty"):
        pdf.extract_pdf(path, "example.pdf", "application/pdf")
    path.write_bytes(b"not a pdf")
    with pytest.raises(pdf.InvalidPDFError):
        pdf.extract_pdf(path, "example.pdf", "application/pdf")
    path.write_bytes(b"%PDF-1.7")
    with pytest.raises(pdf.InvalidPDFError):
        pdf.extract_pdf(path, "example.pdf", "text/plain")


def test_real_pdf_parser_extracts_unicode_text(tmp_path: Path) -> None:
    import pymupdf

    source = pymupdf.open()
    page = source.new_page()
    page.insert_text((72, 72), "SecureRAG café")
    path = tmp_path / "generated.pdf"
    source.save(path)
    source.close()
    extracted = pdf.extract_pdf(path, "generated.pdf", "application/pdf")
    assert extracted.page_count == 1
    assert "SecureRAG" in extracted.pages[0].text
    assert "café" in extracted.pages[0].text


def test_pdf_extraction_handles_unicode_page_text_and_scanned_pdf(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakePage:
        def __init__(self, text: str):
            self.text = text

        def get_text(self, _kind: str) -> str:
            return self.text

    class FakeDoc:
        needs_pass = False
        page_count = 1

        def __init__(self, text: str):
            self.pages = [FakePage(text)]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return iter(self.pages)

    current = {"text": "  caf\u00e9\r\n world  "}
    monkeypatch.setitem(__import__("sys").modules, "pymupdf", SimpleNamespace(open=lambda _path: FakeDoc(current["text"])))
    path = tmp_path / "example.pdf"
    path.write_bytes(b"%PDF-1.7")
    extracted = pdf.extract_pdf(path, "example.pdf", "application/pdf")
    assert extracted.pages[0].page_number == 1
    assert pdf.clean_text(extracted.pages[0].text) == "caf\u00e9\nworld"
    current["text"] = "   "
    with pytest.raises(pdf.InvalidPDFError, match="no extractable text"):
        pdf.extract_pdf(path, "example.pdf", "application/pdf")


def test_pdf_rejects_encrypted_and_over_page_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeDoc:
        needs_pass = True
        page_count = 501

        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def __iter__(self): return iter(())

    monkeypatch.setitem(__import__("sys").modules, "pymupdf", SimpleNamespace(open=lambda _path: FakeDoc()))
    path = tmp_path / "locked.pdf"
    path.write_bytes(b"%PDF-1.7")
    with pytest.raises(pdf.InvalidPDFError, match="Encrypted"):
        pdf.extract_pdf(path, "locked.pdf", "application/pdf")
    FakeDoc.needs_pass = False
    with pytest.raises(pdf.InvalidPDFError, match="page limit"):
        pdf.extract_pdf(path, "locked.pdf", "application/pdf")


class FakeEmbeddingModel:
    tokenizer = WordTokenizer()

    def get_sentence_embedding_dimension(self) -> int:
        return 3

    def encode(self, texts, **kwargs):
        self.kwargs = kwargs
        return [[1.0, 0.0, 0.0] for _ in texts]


def test_embedding_batches_validate_dimensions_and_reject_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    model = FakeEmbeddingModel()
    vectors_out = embeddings.embed_texts(["first", "second"], model)
    assert len(vectors_out) == 2
    assert all(len(vector) == embeddings.embedding_dimension(model) for vector in vectors_out)
    assert model.kwargs["normalize_embeddings"] is True
    with pytest.raises(embeddings.EmbeddingError):
        embeddings.embed_texts(["  "], model)


def test_embedding_model_loading_failure_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    from types import ModuleType

    class BrokenSentenceTransformer:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("private model endpoint")

    monkeypatch.setitem(sys.modules, "sentence_transformers", ModuleType("sentence_transformers"))
    sys.modules["sentence_transformers"].SentenceTransformer = BrokenSentenceTransformer
    embeddings.reset_embedding_model()
    with pytest.raises(embeddings.EmbeddingError, match="could not be loaded") as error:
        embeddings.get_embedding_model()
    assert "private model endpoint" not in str(error.value)
    embeddings.reset_embedding_model()


class FakeQdrant:
    def __init__(self):
        self.collections = []
        self.indexes = []
        self.created = []
        self.payload_schema = {}

    def get_collections(self):
        return SimpleNamespace(collections=self.collections)

    def create_collection(self, name, vectors_config):
        self.created.append((name, vectors_config))
        self.collections.append(SimpleNamespace(name=name))

    def get_collection(self, name):
        return SimpleNamespace(
            config=SimpleNamespace(params=SimpleNamespace(vectors=qdrant_models.VectorParams(size=4, distance=qdrant_models.Distance.COSINE))),
            payload_schema=self.payload_schema,
        )

    def create_payload_index(self, name, field, field_schema, wait):
        self.indexes.append((name, field, field_schema, wait))
        self.payload_schema[field] = field_schema

    def upsert(self, **kwargs):
        self.points = kwargs["points"]

    def delete(self, **kwargs):
        self.deleted = kwargs


def test_qdrant_collection_creation_reuse_and_indexes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.documents.vectors.get_settings", lambda: SimpleNamespace(qdrant_collection_name="chunks"))
    client = FakeQdrant()
    assert vectors.initialize_chunk_collection(4, client) == "chunks"
    assert len(client.created) == 1
    assert len(client.indexes) == 7
    client.collections = [SimpleNamespace(name="chunks")]
    index_count = len(client.indexes)
    assert vectors.initialize_chunk_collection(4, client) == "chunks"
    assert len(client.indexes) == index_count
    with pytest.raises(VectorStoreError):
        vectors.initialize_chunk_collection(5, client)


def test_qdrant_rejects_missing_acl_metadata() -> None:
    with pytest.raises(ValueError, match="security metadata"):
        vectors.validate_payload({"tenant_id": str(uuid4())})


def _valid_payload() -> dict:
    return {
        "chunk_id": str(uuid4()), "document_id": str(uuid4()), "tenant_id": str(uuid4()),
        "uploaded_by": str(uuid4()), "classification": 1, "is_public": False,
        "allowed_roles": ["HR"], "allowed_users": [str(uuid4())], "text": "private text",
    }


@pytest.mark.parametrize("missing", list(vectors.ACL_FIELDS))
def test_qdrant_rejects_missing_mandatory_payload_fields(missing: str) -> None:
    payload = _valid_payload()
    del payload[missing]
    with pytest.raises(vectors.VectorValidationError):
        vectors.validate_payload(payload)


@pytest.mark.parametrize("field", ["chunk_id", "document_id", "tenant_id", "uploaded_by"])
def test_qdrant_rejects_invalid_uuid_payload_fields(field: str) -> None:
    payload = _valid_payload()
    payload[field] = "not-a-uuid"
    with pytest.raises(vectors.VectorValidationError):
        vectors.validate_payload(payload)


@pytest.mark.parametrize("classification", [-1, 4, True, "1"])
def test_qdrant_rejects_invalid_classification(classification) -> None:
    payload = _valid_payload()
    payload["classification"] = classification
    with pytest.raises(vectors.VectorValidationError):
        vectors.validate_payload(payload)


def test_qdrant_rejects_invalid_roles_acl_duplicates_and_empty_text() -> None:
    for field, value in (
        ("allowed_roles", ["SUPERADMIN"]),
        ("allowed_roles", ["HR", "HR"]),
        ("allowed_users", [str(uuid4()), str(uuid4())]),
        ("allowed_users", ["not-a-uuid"]),
        ("is_public", 1),
        ("text", "  "),
    ):
        payload = _valid_payload()
        payload[field] = value
        if field == "allowed_users" and value[0] != "not-a-uuid":
            payload[field] = [payload["allowed_users"][0], payload["allowed_users"][0]]
        with pytest.raises(vectors.VectorValidationError):
            vectors.validate_payload(payload)


def test_qdrant_points_receive_parent_acl_and_page_metadata() -> None:
    client = FakeQdrant()
    tenant_id, document_id, uploaded_by = uuid4(), uuid4(), uuid4()
    chunk = DocumentChunk(uuid4(), document_id, tenant_id, 0, (3, 4), "protected chunk")
    point_ids = vectors.upsert_chunks(
        "chunks", [chunk], [[0.1, 0.2]], tenant_id=tenant_id, uploaded_by=uploaded_by,
        expected_document_id=document_id, expected_dimension=2,
        classification=2, is_public=False, allowed_roles=["HR"], allowed_users=[str(uploaded_by)], client=client,
    )
    payload = client.points[0].payload
    assert point_ids == [str(chunk.chunk_id)]
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["document_id"] == str(document_id)
    assert payload["classification"] == 2 and payload["is_public"] is False
    assert payload["allowed_roles"] == ["HR"] and payload["allowed_users"] == [str(uploaded_by)]
    assert payload["page_numbers"] == [3, 4]


@pytest.mark.parametrize("bad_identity", ["tenant", "document", "chunk"])
def test_qdrant_rejects_chunk_identity_mismatch_before_upsert(bad_identity: str) -> None:
    client = FakeQdrant()
    tenant_id, document_id = uuid4(), uuid4()
    chunk = DocumentChunk(uuid4(), document_id, tenant_id, 0, (1,), "protected")
    if bad_identity == "tenant":
        chunk = DocumentChunk(chunk.chunk_id, chunk.document_id, uuid4(), chunk.chunk_index, chunk.page_numbers, chunk.text)
    elif bad_identity == "document":
        chunk = DocumentChunk(chunk.chunk_id, uuid4(), chunk.tenant_id, chunk.chunk_index, chunk.page_numbers, chunk.text)
    elif bad_identity == "chunk":
        chunk = DocumentChunk("invalid", chunk.document_id, chunk.tenant_id, chunk.chunk_index, chunk.page_numbers, chunk.text)
    with pytest.raises(vectors.VectorValidationError):
        vectors.upsert_chunks(
            "chunks", [chunk], [[0.1, 0.2]], tenant_id=tenant_id,
            expected_document_id=document_id, expected_dimension=2, uploaded_by=uuid4(),
            classification=1, is_public=False, allowed_roles=[], allowed_users=[], client=client,
        )
    assert not hasattr(client, "points")


@pytest.mark.parametrize("bad_vector", [[0.1], [float("nan"), 0.2], [float("inf"), 0.2]])
def test_qdrant_rejects_wrong_dimension_or_nonfinite_vectors(bad_vector) -> None:
    client = FakeQdrant()
    tenant_id, document_id = uuid4(), uuid4()
    chunk = DocumentChunk(uuid4(), document_id, tenant_id, 0, (1,), "protected")
    with pytest.raises(vectors.VectorValidationError):
        vectors.upsert_chunks(
            "chunks", [chunk], [bad_vector], tenant_id=tenant_id,
            expected_document_id=document_id, expected_dimension=2, uploaded_by=uuid4(),
            classification=1, is_public=False, allowed_roles=[], allowed_users=[], client=client,
        )
    assert not hasattr(client, "points")


def test_qdrant_rejects_chunk_vector_count_mismatch_before_upsert() -> None:
    client = FakeQdrant()
    with pytest.raises(vectors.VectorValidationError, match="counts"):
        vectors.upsert_chunks(
            "chunks", [], [[0.1, 0.2]], tenant_id=uuid4(), expected_document_id=uuid4(),
            expected_dimension=2, uploaded_by=uuid4(), classification=1,
            is_public=False, allowed_roles=[], allowed_users=[], client=client,
        )
    assert not hasattr(client, "points")


def test_document_database_constraints_and_secure_defaults(test_session_factory) -> None:
    with test_session_factory() as session:
        user = User(email="doc@example.com", hashed_password="hash", full_name="Doc User")
        tenant = Tenant(name="Docs", slug="docs")
        session.add_all([user, tenant])
        session.flush()
        session.add(TenantMembership(tenant_id=tenant.id, user_id=user.id, role=TenantRole.OWNER))
        session.flush()
        document = Document(
            tenant_id=tenant.id, uploaded_by=user.id, filename=f"{uuid4()}.pdf", original_filename="report.pdf",
            file_hash="a" * 64, file_size=20, content_type="application/pdf",
            status=DocumentStatus.PENDING,
        )
        session.add(document)
        session.commit()
        assert document.classification == DocumentClassification.INTERNAL.value
        assert document.is_public is False
        assert document.allowed_roles == [] and document.allowed_users == []
        duplicate = Document(
            tenant_id=tenant.id, uploaded_by=user.id, filename=f"{uuid4()}.pdf", original_filename="copy.pdf",
            file_hash="a" * 64, file_size=20, content_type="application/pdf",
            status=DocumentStatus.PENDING,
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
