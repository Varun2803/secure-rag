import logging
from sqlalchemy.orm import Session

from app.config import get_settings
from app.generation.citations import validate_answer_payload
from app.generation.context import build_authorized_context
from app.generation.prompts import SYSTEM_PROMPT, build_user_prompt
from app.generation.providers.base import (
    GenerationProvider,
    InvalidProviderResponseError,
    ProviderUnavailableError,
)
from app.generation.providers.openai_compatible import OpenAICompatibleProvider
from app.generation.schemas import GenerationRequest, GenerationResponse
from app.retrieval.schemas import RetrievalSearchRequest
from app.retrieval.service import RetrievalUnavailableError, search_documents
from app.tenants.dependencies import TenantContext

logger = logging.getLogger(__name__)
INSUFFICIENT_INFORMATION = "The available documents do not provide sufficient information to answer this question."


class GenerationUnavailableError(RuntimeError):
    """Secure retrieval or generation provider is unavailable."""


def generate_answer(
    session: Session,
    *,
    context: TenantContext,
    request: GenerationRequest,
    provider: GenerationProvider | None = None,
) -> GenerationResponse:
    """Generate only from Phase 5's database-authorized retrieval response."""
    try:
        retrieval = search_documents(
            session,
            context=context,
            request=RetrievalSearchRequest(query=request.query, top_k=request.top_k),
        )
    except RetrievalUnavailableError as exc:
        raise GenerationUnavailableError from exc

    settings = get_settings()
    try:
        authorized = build_authorized_context(
            retrieval, context=context, max_tokens=settings.rag_max_context_tokens,
            max_chunks=settings.rag_max_context_chunks, query=request.query,
        )
    except (ValueError, TypeError) as exc:
        logger.warning("Authorized retrieval response failed validation (%s)", type(exc).__name__)
        raise GenerationUnavailableError from exc
    if not authorized.sources:
        return GenerationResponse(
            query=request.query, answer=INSUFFICIENT_INFORMATION, citations=[], result_count=0, grounded=False,
        )

    try:
        model_response = (provider or OpenAICompatibleProvider(settings)).generate(
            system_message=SYSTEM_PROMPT,
            user_message=build_user_prompt(request.query, authorized.serialized_sources),
        )
        result = validate_answer_payload(
            {"answer": model_response.answer, "citations": model_response.citations,
             "insufficient_information": model_response.insufficient_information},
            authorized,
        )
    except InvalidProviderResponseError as exc:
        logger.warning("LLM response validation failed (%s)", type(exc).__name__)
        raise GenerationUnavailableError from exc
    except ProviderUnavailableError as exc:
        raise GenerationUnavailableError from exc
    except (ValueError, TypeError, AttributeError) as exc:
        logger.warning("Generation result validation failed (%s)", type(exc).__name__)
        raise GenerationUnavailableError from exc

    return GenerationResponse(
        query=request.query,
        answer=result.answer,
        citations=list(result.citations),
        result_count=len(result.citations),
        grounded=result.grounded,
    )
