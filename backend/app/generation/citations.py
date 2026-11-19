import re
from dataclasses import dataclass

from app.generation.context import AuthorizedContext, ContextSource
from app.generation.schemas import Citation

_REFERENCE = re.compile(r"\[(S\d+)\]")
_BRACKET_CONTENT = re.compile(r"\[([^\[\]\r\n]*)\]")
_VALID_LABEL = re.compile(r"S[1-9][0-9]*\Z")
_LABEL_SHAPE = re.compile(r"[A-Za-z][0-9]+\Z")
_UUID_TEXT = re.compile(r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b")
_PAGE_REFERENCE = re.compile(
    r"\bpages?\s*(?:no\.?\s*|[:#]\s*)?(\d+(?:\s*(?:,|and|&|-|–|to)\s*\d+)*)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ValidatedAnswer:
    answer: str
    citations: tuple[Citation, ...]
    grounded: bool
    insufficient_information: bool


def validate_answer_payload(payload: object, context: AuthorizedContext) -> ValidatedAnswer:
    """Map model labels to trusted metadata; never accept model-supplied IDs/pages."""
    if not isinstance(payload, dict) or set(payload) != {"answer", "citations", "insufficient_information"}:
        raise ValueError("Invalid structured generation response")
    answer = payload["answer"]
    labels = payload["citations"]
    insufficient = payload["insufficient_information"]
    if not isinstance(answer, str) or not answer.strip() or not isinstance(labels, list):
        raise ValueError("Invalid structured generation response")
    if not isinstance(insufficient, bool) or any(not isinstance(label, str) for label in labels):
        raise ValueError("Invalid structured generation response")
    if insufficient:
        return ValidatedAnswer("The available documents do not provide sufficient information to answer this question.", (), False, True)

    available: dict[str, ContextSource] = {source.reference: source for source in context.sources}
    if any(not _VALID_LABEL.fullmatch(label) for label in labels) or len(set(labels)) != len(labels):
        return ValidatedAnswer("The available documents do not provide sufficient information to answer this question.", (), False, True)
    bracketed = [match.group(1).strip() for match in _BRACKET_CONTENT.finditer(answer)]
    if any((_LABEL_SHAPE.fullmatch(value) or value.casefold().startswith("s"))
           and not _VALID_LABEL.fullmatch(value) for value in bracketed):
        return ValidatedAnswer("The available documents do not provide sufficient information to answer this question.", (), False, True)
    references = list(_REFERENCE.finditer(answer))
    mentioned = {match.group(1) for match in references}
    requested = set(labels)
    if mentioned != requested or not requested:
        return ValidatedAnswer("The available documents do not provide sufficient information to answer this question.", (), False, True)
    if mentioned - available.keys() or requested - available.keys():
        return ValidatedAnswer("The available documents do not provide sufficient information to answer this question.", (), False, True)
    if _UUID_TEXT.search(answer):
        return ValidatedAnswer("The available documents do not provide sufficient information to answer this question.", (), False, True)
    for page_match in _PAGE_REFERENCE.finditer(answer):
        sentence_start = max(answer.rfind(mark, 0, page_match.start()) for mark in ".!?\n") + 1
        sentence_ends = [position for mark in ".!?\n" if (position := answer.find(mark, page_match.end())) >= 0]
        sentence_end = min(sentence_ends) if sentence_ends else len(answer)
        local_refs = [ref for ref in references if sentence_start <= ref.start() < sentence_end]
        if not local_refs:
            return ValidatedAnswer("The available documents do not provide sufficient information to answer this question.", (), False, True)
        nearest = min(local_refs, key=lambda ref: min(abs(ref.start() - page_match.end()), abs(ref.end() - page_match.start())))
        explicit_pages = [int(value) for value in re.findall(r"\d+", page_match.group(1))]
        operators = re.findall(r"\d+\s*(,|and|&|-|–|to)\s*\d+", page_match.group(1), re.IGNORECASE)
        referenced_pages = {explicit_pages[0]}
        for index, operator in enumerate(operators):
            left, right = explicit_pages[index], explicit_pages[index + 1]
            if operator in {"-", "–", "to"}:
                if right < left or right - left > 1000:
                    return ValidatedAnswer("The available documents do not provide sufficient information to answer this question.", (), False, True)
                referenced_pages.update(range(left, right + 1))
            else:
                referenced_pages.add(right)
        if not referenced_pages.issubset(set(available[nearest.group(1)].page_numbers)):
            return ValidatedAnswer("The available documents do not provide sufficient information to answer this question.", (), False, True)
    citations = tuple(
        Citation(document_id=available[label].document_id, chunk_id=available[label].chunk_id,
                 page_numbers=list(available[label].page_numbers), relevance_score=available[label].relevance_score)
        for label in sorted(requested, key=lambda ref: int(ref[1:]))
    )
    return ValidatedAnswer(answer.strip(), citations, True, False)
