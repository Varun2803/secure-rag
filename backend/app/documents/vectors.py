from math import isfinite
from uuid import UUID

import grpc
import httpx
from qdrant_client import models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.config import get_settings
from app.database.qdrant import get_qdrant_client
from app.tenants.models import TenantRole

ACL_FIELDS = (
    "chunk_id", "document_id", "tenant_id", "uploaded_by", "classification",
    "is_public", "allowed_roles", "allowed_users", "text",
)


class VectorStoreError(RuntimeError):
    """Vector collection or write operation failed safely."""


class VectorValidationError(ValueError):
    """Chunk identity, ACL, or embedding data failed validation."""


def initialize_chunk_collection(dimension: int, client=None) -> str:
    if dimension <= 0:
        raise ValueError("Vector dimension must be positive")
    client = client or get_qdrant_client()
    name = get_settings().qdrant_collection_name
    try:
        if any(item.name == name for item in client.get_collections().collections):
            info = client.get_collection(name)
            vector_config = info.config.params.vectors
            if not isinstance(vector_config, models.VectorParams) or vector_config.size != dimension:
                raise VectorStoreError("Existing vector collection has incompatible dimensions")
            if vector_config.distance != models.Distance.COSINE:
                raise VectorStoreError("Existing vector collection has incompatible distance metric")
        else:
            client.create_collection(name, vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE))
            info = client.get_collection(name)
        payload_schema = dict(getattr(info, "payload_schema", None) or {})
        for field, schema in (
            ("tenant_id", models.PayloadSchemaType.KEYWORD),
            ("document_id", models.PayloadSchemaType.KEYWORD),
            ("chunk_id", models.PayloadSchemaType.KEYWORD),
            ("allowed_roles", models.PayloadSchemaType.KEYWORD),
            ("allowed_users", models.PayloadSchemaType.KEYWORD),
            ("classification", models.PayloadSchemaType.INTEGER),
            ("is_public", models.PayloadSchemaType.BOOL),
        ):
            if field not in payload_schema:
                client.create_payload_index(name, field, field_schema=schema, wait=True)
                payload_schema[field] = schema
        return name
    except VectorStoreError:
        raise
    except (UnexpectedResponse, ResponseHandlingException, httpx.HTTPError, grpc.RpcError, OSError, TimeoutError, ValueError) as exc:
        raise VectorStoreError("Vector collection initialization failed") from exc


def validate_payload(payload: dict) -> None:
    if any(field not in payload for field in ACL_FIELDS):
        raise VectorValidationError("Chunk security metadata is incomplete")
    if not isinstance(payload["allowed_roles"], list) or not isinstance(payload["allowed_users"], list):
        raise VectorValidationError("Chunk ACL metadata is invalid")
    if not isinstance(payload["is_public"], bool) or isinstance(payload["classification"], bool) or not isinstance(payload["classification"], int):
        raise VectorValidationError("Chunk ACL metadata is invalid")
    if payload["classification"] not in range(4):
        raise VectorValidationError("Chunk classification is invalid")
    if any(not isinstance(role, str) or role not in {value.value for value in TenantRole}
           for role in payload["allowed_roles"]):
        raise VectorValidationError("Chunk role ACL is invalid")
    if len(set(payload["allowed_roles"])) != len(payload["allowed_roles"]):
        raise VectorValidationError("Chunk role ACL contains duplicates")
    try:
        normalized_users = [str(UUID(str(user_id))) for user_id in payload["allowed_users"]]
        UUID(str(payload["uploaded_by"]))
    except (ValueError, TypeError) as exc:
        raise VectorValidationError("Chunk user ACL is invalid") from exc
    if len(set(normalized_users)) != len(normalized_users):
        raise VectorValidationError("Chunk user ACL contains duplicates")
    try:
        for key in ("tenant_id", "document_id", "chunk_id"):
            UUID(str(payload[key]))
    except (ValueError, TypeError) as exc:
        raise VectorValidationError("Chunk identity metadata is invalid") from exc
    if not isinstance(payload.get("text"), str) or not payload["text"].strip():
        raise VectorValidationError("Chunk text is invalid")


def upsert_chunks(collection: str, chunks: list, vectors: list[list[float]], *, tenant_id: UUID,
                  expected_document_id: UUID, expected_dimension: int,
                  uploaded_by: UUID, classification: int, is_public: bool,
                  allowed_roles: list[str], allowed_users: list[str], client=None) -> list[str]:
    if len(chunks) != len(vectors):
        raise VectorValidationError("Chunk and vector counts do not match")
    if not chunks or expected_dimension <= 0:
        raise VectorValidationError("Chunk and vector dimensions are invalid")
    try:
        expected_tenant = UUID(str(tenant_id))
        expected_document = UUID(str(expected_document_id))
    except (ValueError, TypeError) as exc:
        raise VectorValidationError("Expected chunk identity is invalid") from exc
    point_ids: set[UUID] = set()
    points = []
    for chunk, vector in zip(chunks, vectors, strict=True):
        try:
            chunk_tenant = UUID(str(chunk.tenant_id))
            chunk_document = UUID(str(chunk.document_id))
            chunk_id = UUID(str(chunk.chunk_id))
        except (AttributeError, ValueError, TypeError) as exc:
            raise VectorValidationError("Chunk identity is invalid") from exc
        if chunk_tenant != expected_tenant:
            raise VectorValidationError("Chunk tenant does not match the verified tenant")
        if chunk_document != expected_document:
            raise VectorValidationError("Chunk document identity does not match the expected document")
        if chunk_id in point_ids:
            raise VectorValidationError("Chunk IDs contain duplicates")
        point_ids.add(chunk_id)
        if not isinstance(vector, (list, tuple)) or len(vector) != expected_dimension:
            raise VectorValidationError("Embedding vector dimension is invalid")
        try:
            if any(isinstance(value, bool) or not isfinite(value) for value in vector):
                raise VectorValidationError("Embedding vector values must be finite numbers")
        except TypeError as exc:
            raise VectorValidationError("Embedding vector values are invalid") from exc
        try:
            page_numbers = list(chunk.page_numbers)
            payload = {
                "chunk_id": str(chunk_id), "document_id": str(chunk_document),
                "tenant_id": str(expected_tenant), "uploaded_by": str(uploaded_by),
                "chunk_index": chunk.chunk_index,
                "page_number": page_numbers[0] if len(page_numbers) == 1 else None,
                "page_numbers": page_numbers, "classification": classification,
                "is_public": is_public, "allowed_roles": list(allowed_roles),
                "allowed_users": list(allowed_users), "text": chunk.text,
            }
        except (AttributeError, TypeError) as exc:
            raise VectorValidationError("Chunk metadata is invalid") from exc
        validate_payload(payload)
        points.append(models.PointStruct(id=str(chunk_id), vector=vector, payload=payload))
    client = client or get_qdrant_client()
    try:
        client.upsert(collection_name=collection, points=points, wait=True)
    except (UnexpectedResponse, ResponseHandlingException, httpx.HTTPError, grpc.RpcError, OSError, TimeoutError, ValueError) as exc:
        raise VectorStoreError("Vector storage failed") from exc
    return [str(UUID(str(chunk.chunk_id))) for chunk in chunks]


def delete_points(collection: str, point_ids: list[str], client=None) -> None:
    if not point_ids:
        return
    client = client or get_qdrant_client()
    try:
        client.delete(collection_name=collection, points_selector=models.PointIdsList(points=point_ids), wait=True)
    except (UnexpectedResponse, ResponseHandlingException, httpx.HTTPError, grpc.RpcError, OSError, TimeoutError, ValueError) as exc:
        raise VectorStoreError("Vector cleanup failed") from exc
