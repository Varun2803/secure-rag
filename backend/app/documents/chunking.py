from dataclasses import dataclass
import re
from uuid import UUID, uuid5

from app.documents.pdf import PDFPage, clean_text

_CHUNK_NAMESPACE = UUID("0a819326-0c2d-4d2b-9ab2-55454eae41c0")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n{2,}")


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    chunk_id: UUID
    document_id: UUID
    tenant_id: UUID
    chunk_index: int
    page_numbers: tuple[int, ...]
    text: str


class TokenChunker:
    def __init__(self, tokenizer, chunk_size: int = 800, overlap: int = 120) -> None:
        if chunk_size < 1 or overlap < 0 or overlap >= chunk_size:
            raise ValueError("Invalid chunk size or overlap")
        self.tokenizer = tokenizer
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk_pages(self, pages: tuple[PDFPage, ...], document_id: UUID, tenant_id: UUID) -> list[DocumentChunk]:
        result: list[DocumentChunk] = []
        for page in pages:
            cleaned = clean_text(page.text)
            if not cleaned:
                continue
            sentences = [self.tokenizer.encode(sentence, add_special_tokens=False)
                         for sentence in _SENTENCE_BOUNDARY.split(cleaned) if sentence.strip()]
            groups: list[list[int]] = []
            current: list[list[int]] = []
            current_size = 0

            def flush() -> None:
                nonlocal current, current_size
                if current:
                    groups.append([token for sentence in current for token in sentence])
                    current, current_size = [], 0

            for sentence in sentences:
                if not sentence:
                    continue
                if len(sentence) > self.chunk_size:
                    flush()
                    stride = self.chunk_size - self.overlap
                    start = 0
                    while start < len(sentence):
                        end = min(start + self.chunk_size, len(sentence))
                        groups.append(sentence[start:end])
                        if end == len(sentence):
                            break
                        start += stride
                    continue
                if current and current_size + len(sentence) > self.chunk_size:
                    previous = current
                    flush()
                    overlap_sentences: list[list[int]] = []
                    overlap_size = 0
                    if self.overlap:
                        for prior in reversed(previous):
                            if overlap_size + len(prior) > self.chunk_size - len(sentence):
                                break
                            overlap_sentences.insert(0, prior)
                            overlap_size += len(prior)
                            if overlap_size >= self.overlap:
                                break
                    current = overlap_sentences + [sentence]
                    current_size = overlap_size + len(sentence)
                else:
                    current.append(sentence)
                    current_size += len(sentence)
            flush()

            for token_ids in groups:
                text = self.tokenizer.decode(token_ids, skip_special_tokens=True).strip()
                if text:
                    index = len(result)
                    chunk_id = uuid5(_CHUNK_NAMESPACE, f"{document_id}:{index}")
                    result.append(DocumentChunk(chunk_id, document_id, tenant_id, index, (page.page_number,), text))
        if not result:
            raise ValueError("Document contains no text to chunk")
        return result
