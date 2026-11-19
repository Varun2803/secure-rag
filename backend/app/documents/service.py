import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.documents.chunking import TokenChunker
from app.documents.embeddings import embedding_dimension, embed_texts, get_embedding_model
from app.documents.models import Document, DocumentClassification, DocumentStatus
from app.documents.pdf import InvalidPDFError, extract_pdf
from app.documents.schemas import DocumentPage
from app.documents.storage import StoredUpload
from app.documents.vectors import VectorStoreError, delete_points, initialize_chunk_collection, upsert_chunks
from app.tenants.dependencies import TenantContext

logger = logging.getLogger(__name__)


class DuplicateDocumentError(RuntimeError):
    pass


class DocumentNotFoundError(LookupError):
    pass


class IngestionFailedError(RuntimeError):
    def __init__(self, document: Document, code: str):
        self.document = document
        self.code = code
        super().__init__("Document processing failed")


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, InvalidPDFError):
        return "PDF_INVALID"
    if isinstance(exc, VectorStoreError):
        return "VECTOR_STORAGE_FAILED"
    if type(exc).__name__ == "EmbeddingError":
        return "EMBEDDING_FAILED"
    return "PROCESSING_FAILED"


def ingest_document(
    session: Session,
    *,
    context: TenantContext,
    upload: StoredUpload,
    original_filename: str,
    classification: DocumentClassification,
    is_public: bool,
    allowed_roles: list[str],
    allowed_users: list[str],
) -> Document:
    document = Document(
        tenant_id=context.tenant_id,
        uploaded_by=context.user_id,
        filename=f"{upload.storage_key}.pdf",
        original_filename=original_filename,
        file_hash=upload.sha256,
        file_size=upload.size,
        content_type="application/pdf",
        status=DocumentStatus.PENDING,
        classification=classification.value,
        is_public=is_public,
        allowed_roles=allowed_roles,
        allowed_users=allowed_users,
    )
    session.add(document)
    try:
        session.commit()
        session.refresh(document)
    except IntegrityError as exc:
        session.rollback()
        raise DuplicateDocumentError from exc
    except SQLAlchemyError:
        session.rollback()
        raise

    collection: str | None = None
    point_ids: list[str] = []
    try:
        document.status = DocumentStatus.PROCESSING
        session.commit()
        extracted = extract_pdf(upload.path, original_filename, "application/pdf")
        model = get_embedding_model()
        tokenizer = getattr(model, "tokenizer", None)
        if tokenizer is None:
            raise RuntimeError("Embedding tokenizer unavailable")
        settings = get_settings()
        # Respect the loaded transformer's real input limit instead of silently truncating vectors.
        model_limit = getattr(model, "max_seq_length", settings.embedding_chunk_size + 2)
        chunk_size = min(settings.embedding_chunk_size, max(1, model_limit - 2))
        overlap = min(settings.embedding_chunk_overlap, max(0, chunk_size // 5))
        chunks = TokenChunker(tokenizer, chunk_size, overlap).chunk_pages(
            extracted.pages, document.id, context.tenant_id
        )
        vectors = embed_texts([chunk.text for chunk in chunks], model)
        dimension = embedding_dimension(model)
        if any(len(vector) != dimension for vector in vectors):
            raise RuntimeError("Invalid embedding dimension")
        collection = initialize_chunk_collection(dimension)
        # All points receive one immutable ACL snapshot from the document metadata.
        point_ids = [str(chunk.chunk_id) for chunk in chunks]
        if any(
            chunk.tenant_id != context.tenant_id or chunk.document_id != document.id
            for chunk in chunks
        ):
            raise ValueError("Chunk identity validation failed")
        upsert_chunks(
            collection, chunks, vectors, tenant_id=context.tenant_id,
            expected_document_id=document.id, expected_dimension=dimension,
            uploaded_by=context.user_id, classification=classification.value,
            is_public=is_public, allowed_roles=allowed_roles, allowed_users=allowed_users,
        )
        document.page_count = extracted.page_count
        document.chunk_count = len(chunks)
        document.status = DocumentStatus.COMPLETED
        document.failure_detail = None
        document.processing_completed_at = datetime.now(timezone.utc)
        session.commit()
        session.refresh(document)
        return document
    except Exception as exc:
        session.rollback()
        if collection and point_ids:
            try:
                delete_points(collection, point_ids)
            except Exception as cleanup_exc:
                logger.error("Document vector compensation failed (%s)", type(cleanup_exc).__name__)
        code = _failure_code(exc)
        logger.warning("Document ingestion failed (%s)", code)
        try:
            persisted = session.get(Document, document.id)
            if persisted is not None:
                persisted.status = DocumentStatus.FAILED
                persisted.failure_detail = code
                session.commit()
                document = persisted
        except SQLAlchemyError as db_exc:
            session.rollback()
            logger.error("Document failure status could not be saved (%s)", type(db_exc).__name__)
        raise IngestionFailedError(document, code) from exc


def list_documents(session: Session, tenant_id: UUID, *, limit: int, offset: int) -> DocumentPage:
    total = session.scalar(select(func.count()).select_from(Document).where(Document.tenant_id == tenant_id)) or 0
    items = list(session.scalars(
        select(Document).where(Document.tenant_id == tenant_id)
        .order_by(Document.created_at.desc(), Document.id).limit(limit).offset(offset)
    ).all())
    return DocumentPage(items=items, total=total, limit=limit, offset=offset)


def get_document(session: Session, tenant_id: UUID, document_id: UUID) -> Document:
    document = session.scalar(select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id))
    if document is None:
        raise DocumentNotFoundError
    return document
