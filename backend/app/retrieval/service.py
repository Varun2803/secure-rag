import logging
from math import isfinite

import grpc
import httpx
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.documents.embeddings import EmbeddingError, embedding_dimension, embed_texts, get_embedding_model
from app.documents.models import Document, DocumentStatus
from app.documents.vectors import VectorStoreError, initialize_chunk_collection
from app.database.qdrant import get_qdrant_client
from app.retrieval.schemas import RetrievalResult, RetrievalSearchRequest, RetrievalSearchResponse
from app.retrieval.security import CandidateChunk, build_tenant_acl_filter, can_access_document, parse_candidate
from app.tenants.dependencies import TenantContext

logger = logging.getLogger(__name__)
MAX_CANDIDATES = 100
CANDIDATE_MULTIPLIER = 5


class RetrievalUnavailableError(RuntimeError):
    """Embedding, vector, or authoritative database service is unavailable."""


def search_documents(
    session: Session,
    *,
    context: TenantContext,
    request: RetrievalSearchRequest,
) -> RetrievalSearchResponse:
    try:
        model = get_embedding_model()
        dimension = embedding_dimension(model)
        query_vectors = embed_texts([request.query], model)
        if not isinstance(query_vectors, (list, tuple)) or len(query_vectors) != 1:
            raise EmbeddingError("Invalid query vector dimensions")
        query_vector = query_vectors[0]
        if not isinstance(query_vector, (list, tuple)) or len(query_vector) != dimension:
            raise EmbeddingError("Invalid query vector dimensions")
        try:
            if any(isinstance(value, bool) or not isfinite(value) for value in query_vector):
                raise EmbeddingError("Invalid query vector values")
        except (TypeError, ValueError, OverflowError) as exc:
            raise EmbeddingError("Invalid query vector values") from exc
        collection = initialize_chunk_collection(dimension)
        candidate_limit = min(request.top_k * CANDIDATE_MULTIPLIER, MAX_CANDIDATES)
        response = get_qdrant_client().query_points(
            collection_name=collection,
            query=query_vector,
            query_filter=build_tenant_acl_filter(context),
            limit=candidate_limit,
            with_payload=True,
            with_vectors=False,
        )
    except (EmbeddingError, VectorStoreError, UnexpectedResponse, ResponseHandlingException,
            httpx.HTTPError, grpc.RpcError, OSError, TimeoutError, ValueError) as exc:
        logger.warning("Retrieval backend unavailable (%s)", type(exc).__name__)
        raise RetrievalUnavailableError from exc

    points = getattr(response, "points", None)
    if not isinstance(points, (list, tuple)):
        logger.warning("Qdrant returned a malformed retrieval response")
        points = []
    candidates: list[CandidateChunk] = []
    for point in points:
        candidate = parse_candidate(point, context.tenant_id)
        if candidate is None:
            continue
        candidates.append(candidate)
    document_ids = {candidate.document_id for candidate in candidates}

    if document_ids:
        try:
            documents = session.scalars(
                select(Document).where(
                    Document.tenant_id == context.tenant_id,
                    Document.id.in_(document_ids),
                    Document.status == DocumentStatus.COMPLETED.value,
                )
            ).all()
        except SQLAlchemyError as exc:
            logger.warning("Retrieval authorization lookup failed (%s)", type(exc).__name__)
            raise RetrievalUnavailableError from exc
        documents_by_id = {document.id: document for document in documents}
    else:
        documents_by_id = {}

    authorized_by_chunk_id: dict[str, CandidateChunk] = {}
    for candidate in candidates:
        document = documents_by_id.get(candidate.document_id)
        if document is not None and can_access_document(document, context):
            key = str(candidate.chunk_id)
            previous = authorized_by_chunk_id.get(key)
            if previous is None or candidate.score > previous.score:
                authorized_by_chunk_id[key] = candidate
    authorized = sorted(authorized_by_chunk_id.values(), key=lambda item: (-item.score, str(item.chunk_id)))

    results = [
        RetrievalResult(
            chunk_id=item.chunk_id,
            document_id=item.document_id,
            chunk_index=item.chunk_index,
            page_numbers=list(item.page_numbers),
            text=item.text,
            similarity_score=item.score,
        )
        for item in authorized[:request.top_k]
    ]
    return RetrievalSearchResponse(
        query=request.query,
        tenant_id=context.tenant_id,
        results=results,
        result_count=len(results),
    )
