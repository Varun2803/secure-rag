import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.health import router as health_router
from app.auth.routes import router as auth_router
from app.tenants.routes import router as tenants_router
from app.config import get_settings
from app.database.postgres import dispose_database_engine
from app.database.qdrant import close_qdrant_client
from app.documents.routes import router as documents_router
from app.retrieval.routes import router as retrieval_router
from app.generation.routes import router as generation_router
from app.permissions.routes import router as permissions_router

_REQUEST_BODY_OVERHEAD_BYTES = 1024 * 1024


def request_body_limit_status(content_length: str | None, max_body_bytes: int) -> int | None:
    """Return a sanitized HTTP status for invalid or oversized declared bodies."""
    if content_length is None:
        return None
    if len(content_length) > 20 or not content_length.isascii() or not content_length.isdigit():
        return 400
    size = int(content_length)
    if size > max_body_bytes:
        return 413
    return None


class RequestBodyLimitMiddleware:
    """Count request bytes as ASGI delivers them without buffering the body."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        received_bytes = 0
        response_started = False
        response_completed = False
        exceeded = False

        async def send_counted(message: Message) -> None:
            nonlocal response_started, response_completed
            # Once an oversized request has been detected, suppress responses
            # emitted by error middleware while the downstream request unwinds.
            if exceeded:
                return
            if message["type"] == "http.response.start":
                response_started = True
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                response_completed = True
            await send(message)

        async def receive_counted() -> Message:
            nonlocal received_bytes, exceeded, response_started, response_completed
            message = await receive()
            if message["type"] != "http.request":
                # In particular, preserve http.disconnect unchanged.
                return message

            received_bytes += len(message.get("body", b""))
            if received_bytes > self.max_body_bytes:
                exceeded = True
                if not response_started:
                    body = b'{"detail":"Request body is too large"}'
                    await send({
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode("ascii")),
                        ],
                    })
                    response_started = True
                    await send({"type": "http.response.body", "body": body, "more_body": False})
                    response_completed = True
                elif not response_completed:
                    # A response had already started; never send a second status.
                    # End the existing response and stop accepting request bytes.
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                    response_completed = True
                raise _RequestBodyLimitExceeded
            return message

        try:
            await self.app(scope, receive_counted, send_counted)
        except Exception:
            if not exceeded:
                raise


class _RequestBodyLimitExceeded(Exception):
    """Internal signal used to stop downstream parsing after the limit is hit."""


class RequestCorrelationMiddleware:
    """Pure ASGI correlation/header middleware; safe for streaming bodies."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid4()
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([
                    (b"x-request-id", str(request_id).encode("ascii")),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                ])
                message = {**message, "headers": headers}
            await send(message)

        raw_lengths = [
            value.decode("latin-1") for key, value in scope.get("headers", [])
            if key.lower() == b"content-length"
        ]
        if len(set(raw_lengths)) > 1:
            response = JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})
            await response(scope, receive, send_with_headers)
            return
        declared_length = raw_lengths[0] if raw_lengths else None
        status_code = request_body_limit_status(declared_length, self.max_body_bytes)
        if status_code is not None:
            detail = "Invalid Content-Length" if status_code == 400 else "Request body is too large"
            response = JSONResponse(status_code=status_code, content={"detail": detail})
            await response(scope, receive, send_with_headers)
            return
        await self.app(scope, receive, send_with_headers)


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logging.getLogger(__name__).info("Starting %s", settings.app_name)
        try:
            yield
        finally:
            logger = logging.getLogger(__name__)
            try:
                close_qdrant_client()
            except Exception:
                logger.exception("Failed to close Qdrant client")
            try:
                dispose_database_engine()
            except Exception:
                logger.exception("Failed to dispose PostgreSQL engine")
            logger.info("Stopped %s", settings.app_name)

    production = settings.app_env.strip().casefold() in {"production", "prod"}
    documentation_path = None if production else f"{settings.api_v1_prefix}/openapi.json"
    application = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="Permission-aware multi-tenant RAG foundation",
        debug=settings.debug,
        lifespan=lifespan,
        openapi_url=documentation_path,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
    )
    trusted_hosts = [host.strip() for host in settings.trusted_hosts.split(",") if host.strip()]
    if trusted_hosts:
        application.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)
    application.include_router(health_router, prefix=settings.api_v1_prefix)
    application.include_router(auth_router, prefix=settings.api_v1_prefix)
    application.include_router(tenants_router, prefix=settings.api_v1_prefix)
    application.include_router(documents_router, prefix=settings.api_v1_prefix)
    application.include_router(retrieval_router, prefix=settings.api_v1_prefix)
    application.include_router(generation_router, prefix=settings.api_v1_prefix)
    application.include_router(permissions_router, prefix=settings.api_v1_prefix)
    # Middleware is nested in reverse registration order: correlation wraps the
    # byte counter so early and streamed 413 responses keep the same headers.
    application.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=settings.max_upload_size_bytes + _REQUEST_BODY_OVERHEAD_BYTES,
    )
    application.add_middleware(
        RequestCorrelationMiddleware,
        max_body_bytes=settings.max_upload_size_bytes + _REQUEST_BODY_OVERHEAD_BYTES,
    )

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logging.getLogger(__name__).error("Unhandled error for %s: %s", request.url.path, type(exc).__name__)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    @application.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        logging.getLogger(__name__).info("Invalid request for %s", request.url.path)
        return JSONResponse(status_code=422, content={"detail": "Invalid request"})

    return application


app = create_app()
