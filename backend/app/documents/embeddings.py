from threading import Lock

from app.config import get_settings


class EmbeddingError(RuntimeError):
    """Embedding model is unavailable or produced invalid vectors."""


_model = None
_model_lock = Lock()


def get_embedding_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                try:
                    from sentence_transformers import SentenceTransformer

                    settings = get_settings()
                    _model = SentenceTransformer(settings.embedding_model_name, device=settings.embedding_device)
                except Exception as exc:
                    raise EmbeddingError("Embedding model could not be loaded") from exc
    return _model


def reset_embedding_model() -> None:
    """Reset the process cache (intended for tests and controlled maintenance)."""
    global _model
    with _model_lock:
        _model = None


def embedding_dimension(model=None) -> int:
    model = model or get_embedding_model()
    dimension = model.get_sentence_embedding_dimension()
    if not isinstance(dimension, int) or dimension <= 0:
        raise EmbeddingError("Embedding model returned an invalid vector dimension")
    return dimension


def embed_texts(texts: list[str], model=None) -> list[list[float]]:
    if not texts or any(not text.strip() for text in texts):
        raise EmbeddingError("Cannot embed empty text")
    model = model or get_embedding_model()
    try:
        vectors = model.encode(
            texts,
            batch_size=get_settings().embedding_batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        dimension = embedding_dimension(model)
        result = vectors.tolist() if hasattr(vectors, "tolist") else vectors
        if len(result) != len(texts) or any(len(vector) != dimension for vector in result):
            raise EmbeddingError("Embedding model returned an invalid vector shape")
        return result
    except EmbeddingError:
        raise
    except Exception as exc:
        raise EmbeddingError("Embedding generation failed") from exc
