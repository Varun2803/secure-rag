from functools import lru_cache

import grpc
import httpx
from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from app.config import get_settings


@lru_cache
def get_qdrant_client() -> QdrantClient:
    settings = get_settings()
    return QdrantClient(url=settings.qdrant_url,
                        api_key=settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None,
                        timeout=5)


def check_qdrant_connection() -> bool:
    try:
        get_qdrant_client().get_collections()
        return True
    except (UnexpectedResponse, ResponseHandlingException, httpx.HTTPError, grpc.RpcError, ValueError, OSError, TimeoutError):
        return False


def close_qdrant_client() -> None:
    """Close a cached client and clear it, even when close raises."""
    if get_qdrant_client.cache_info().currsize:
        client = get_qdrant_client()
        try:
            client.close()
        finally:
            get_qdrant_client.cache_clear()


def collection_exists(collection_name: str) -> bool:
    try:
        return any(c.name == collection_name for c in get_qdrant_client().get_collections().collections)
    except (UnexpectedResponse, ResponseHandlingException, httpx.HTTPError, grpc.RpcError, ValueError, OSError, TimeoutError):
        return False
