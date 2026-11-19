import json
from dataclasses import dataclass
from math import isfinite
from uuid import UUID

from app.generation.prompts import SYSTEM_PROMPT, build_user_prompt
from app.retrieval.schemas import RetrievalSearchResponse
from app.tenants.dependencies import TenantContext

CHAT_FRAMING_TOKEN_RESERVE = 256


def estimate_context_prompt_tokens(query: str, serialized_sources: str) -> int:
    """Return a conservative byte-count bound plus chat protocol reserve."""
    system_message = SYSTEM_PROMPT
    user_message = build_user_prompt(query, serialized_sources)
    return len(system_message.encode("utf-8")) + len(user_message.encode("utf-8")) + CHAT_FRAMING_TOKEN_RESERVE


@dataclass(frozen=True, slots=True)
class ContextSource:
    reference: str
    document_id: UUID
    chunk_id: UUID
    page_numbers: tuple[int, ...]
    relevance_score: float
    text: str


@dataclass(frozen=True, slots=True)
class AuthorizedContext:
    sources: tuple[ContextSource, ...]
    serialized_sources: str
    estimated_tokens: int


def build_authorized_context(
    retrieved: RetrievalSearchResponse,
    *,
    context: TenantContext,
    max_tokens: int,
    max_chunks: int,
    query: str = "",
) -> AuthorizedContext:
    """Bound context using only Phase 5's already-authorized response.

    The configured remote chat model may not share a tokenizer with the local
    embedding model. We therefore bound the UTF-8 byte length of the complete
    system and user message content (a conservative upper bound for byte-pair
    tokenizers), plus a fixed 256-token chat framing reserve. This does not
    promise an exact count for arbitrary provider tokenizers. Chunks are never
    truncated; candidates that exceed the full prompt budget are skipped.
    """
    if max_tokens < 1 or max_chunks < 1:
        raise ValueError("Context limits must be positive")
    if retrieved.tenant_id != context.tenant_id:
        raise ValueError("Retrieval tenant does not match verified tenant context")
    sources: list[ContextSource] = []
    serialized_parts: list[str] = []
    seen_chunks: set[UUID] = set()
    budget_used = estimate_context_prompt_tokens(query, "")
    if budget_used > max_tokens:
        raise ValueError("Prompt framing exceeds the configured context budget")
    for item in retrieved.results:
        if item.chunk_id in seen_chunks:
            continue
        seen_chunks.add(item.chunk_id)
        text = item.text.strip() if isinstance(item.text, str) else ""
        pages = item.page_numbers
        score = item.similarity_score
        if not text or not pages or any(isinstance(p, bool) or p < 1 for p in pages):
            continue
        if isinstance(score, bool) or not isfinite(score):
            continue
        reference = f"S{len(sources) + 1}"
        source = ContextSource(reference, item.document_id, item.chunk_id, tuple(pages), score, text)
        part = json.dumps({"source": reference, "document_id": str(item.document_id),
                           "chunk_id": str(item.chunk_id), "page_numbers": pages, "text": text},
                          ensure_ascii=False)
        proposed_parts = serialized_parts + [part]
        serialized = "\n".join(proposed_parts)
        # Count serialized JSON, all fixed prompt text, question, and framing.
        proposed_budget = estimate_context_prompt_tokens(query, serialized)
        if len(sources) >= max_chunks or proposed_budget > max_tokens:
            continue
        sources.append(source)
        serialized_parts.append(part)
        budget_used = proposed_budget
    return AuthorizedContext(tuple(sources), "\n".join(serialized_parts), budget_used)
