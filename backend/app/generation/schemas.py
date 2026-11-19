from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000, strict=True)
    top_k: int = Field(default=5, ge=1, le=20, strict=True)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Query must not be blank")
        return value


class Citation(BaseModel):
    document_id: UUID
    chunk_id: UUID
    page_numbers: list[int]
    relevance_score: float


class GenerationResponse(BaseModel):
    query: str
    answer: str
    citations: list[Citation]
    result_count: int
    grounded: bool
