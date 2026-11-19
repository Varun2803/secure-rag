from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings


class InvalidPDFError(ValueError):
    """The upload is not a safe, text-bearing PDF within configured limits."""


@dataclass(frozen=True, slots=True)
class PDFPage:
    page_number: int
    text: str


@dataclass(frozen=True, slots=True)
class ExtractedPDF:
    pages: tuple[PDFPage, ...]
    page_count: int


def extract_pdf(path: Path, original_filename: str, content_type: str | None) -> ExtractedPDF:
    if Path(original_filename).suffix.casefold() != ".pdf" or (content_type or "").casefold() != "application/pdf":
        raise InvalidPDFError("Only PDF files are supported")
    if path.stat().st_size == 0:
        raise InvalidPDFError("The uploaded file is empty")
    with path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise InvalidPDFError("The uploaded file is not a valid PDF")
    try:
        import pymupdf as fitz

        with fitz.open(path) as pdf:
            if pdf.needs_pass:
                raise InvalidPDFError("Encrypted PDFs are not supported")
            if pdf.page_count < 1:
                raise InvalidPDFError("The PDF contains no pages")
            if pdf.page_count > get_settings().max_pdf_pages:
                raise InvalidPDFError("The PDF exceeds the page limit")
            pages = tuple(PDFPage(i + 1, page.get_text("text")) for i, page in enumerate(pdf))
    except InvalidPDFError:
        raise
    except Exception as exc:
        raise InvalidPDFError("The PDF is malformed or could not be read") from exc
    if not any(clean_text(page.text) for page in pages):
        raise InvalidPDFError("The PDF contains no extractable text; scanned PDFs are not supported")
    return ExtractedPDF(pages=pages, page_count=len(pages))


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    paragraphs: list[str] = []
    for line in lines:
        if line:
            paragraphs.append(line)
        elif paragraphs and paragraphs[-1] != "":
            paragraphs.append("")
    return "\n".join(paragraphs).strip()
