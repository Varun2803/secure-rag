from functools import lru_cache
from ipaddress import ip_address
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def validate_llm_endpoint(url: str, app_env: str) -> None:
    """Require TLS remotely; permit plain HTTP only for dev loopback endpoints."""
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.casefold()
        hostname = parsed.hostname
        # Accessing port validates malformed/non-numeric/out-of-range ports.
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("LLM_BASE_URL must be a valid HTTPS URL") from exc
    if (not hostname or any(char.isspace() for char in url) or
            parsed.username is not None or parsed.password is not None or
            parsed.query or parsed.fragment or scheme not in {"https", "http"}):
        raise ValueError("LLM_BASE_URL must be a valid HTTPS URL")
    try:
        address = ip_address(hostname)
    except ValueError:
        try:
            ascii_hostname = hostname.rstrip(".").encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("LLM_BASE_URL hostname is invalid") from exc
        labels = ascii_hostname.split(".")
        if (len(ascii_hostname) > 253 or any(
                not label or len(label) > 63 or label.startswith("-") or label.endswith("-") or
                any(not (char.isalnum() or char == "-") for char in label)
                for label in labels
        )):
            raise ValueError("LLM_BASE_URL hostname is invalid")
        is_loopback = hostname.casefold() == "localhost"
    else:
        is_loopback = address.is_loopback
    if scheme == "https":
        return
    if app_env.strip().casefold() != "development" or not is_loopback:
        raise ValueError("HTTP LLM endpoints are allowed only for development loopback hosts")


class Settings(BaseSettings):
    app_name: str = "SecureRAG"
    app_env: str = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    database_url: str | None = None
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr | None = None
    secret_key: SecretStr | None = None
    access_token_expire_minutes: int = 30
    jwt_issuer: str = "securerag"
    jwt_audience: str = "securerag-api"
    log_level: str = "INFO"
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_pool_timeout: int = 30
    database_pool_recycle: int = 1800
    document_storage_path: str = "./var/documents"
    max_upload_size_bytes: int = 20 * 1024 * 1024
    max_pdf_pages: int = 500
    max_filename_length: int = 255
    embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_device: str = "cpu"
    embedding_batch_size: int = 32
    embedding_chunk_size: int = 800
    embedding_chunk_overlap: int = 120
    qdrant_collection_name: str = "securerag_chunks"
    llm_provider: str = "openai_compatible"
    llm_api_key: SecretStr | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.0
    llm_max_output_tokens: int = 800
    llm_timeout_seconds: float = 30.0
    rag_max_context_tokens: int = 6000
    rag_max_context_chunks: int = 8
    rag_max_answer_tokens: int = 800

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False)

    @field_validator("api_v1_prefix")
    @classmethod
    def normalize_api_prefix(cls, value: str) -> str:
        value = "/" + value.strip("/")
        return value if value != "/" else "/api/v1"

    @model_validator(mode="after")
    def validate_token_settings(self) -> "Settings":
        if self.app_env.strip().casefold() in {"production", "prod"} and self.debug:
            raise ValueError("DEBUG must be disabled in production")
        if self.access_token_expire_minutes < 1 or self.access_token_expire_minutes > 60:
            raise ValueError("ACCESS_TOKEN_EXPIRE_MINUTES must be between 1 and 60")
        if not self.jwt_issuer.strip() or not self.jwt_audience.strip():
            raise ValueError("JWT issuer and audience must not be empty")
        if self.max_upload_size_bytes < 1 or self.max_pdf_pages < 1 or self.max_filename_length < 1:
            raise ValueError("Document upload limits must be positive")
        if self.embedding_batch_size < 1 or self.embedding_chunk_size < 1:
            raise ValueError("Embedding batch and chunk sizes must be positive")
        if self.embedding_chunk_overlap < 0 or self.embedding_chunk_overlap >= self.embedding_chunk_size:
            raise ValueError("Embedding chunk overlap must be smaller than the chunk size")
        if not self.embedding_model_name.strip() or not self.document_storage_path.strip():
            raise ValueError("Embedding model and document storage path must not be empty")
        if self.llm_provider.strip().casefold() != "openai_compatible":
            raise ValueError("LLM_PROVIDER must be openai_compatible")
        validate_llm_endpoint(self.llm_base_url, self.app_env)
        if not self.llm_model.strip():
            raise ValueError("LLM model must be configured")
        if not 0 <= self.llm_temperature <= 2 or not 1 <= self.llm_max_output_tokens <= 16384:
            raise ValueError("LLM generation settings are outside allowed limits")
        if not 1 <= self.llm_timeout_seconds <= 300:
            raise ValueError("LLM_TIMEOUT_SECONDS must be between 1 and 300")
        if not 1 <= self.rag_max_context_tokens <= 100000 or not 1 <= self.rag_max_context_chunks <= 100:
            raise ValueError("RAG context limits are outside allowed limits")
        if not 1 <= self.rag_max_answer_tokens <= 16384:
            raise ValueError("RAG_MAX_ANSWER_TOKENS must be between 1 and 16384")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
