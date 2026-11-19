import json
from collections.abc import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse

from app.main import RequestBodyLimitMiddleware, RequestCorrelationMiddleware, create_app


def _scope(*, headers: list[tuple[bytes, bytes]] | None = None, path: str = "/upload") -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers or [],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
        "root_path": "",
    }


def _run_asgi(app, *, chunks: Iterable[dict], headers=None, path="/upload"):
    messages = iter(chunks)
    sent: list[dict] = []

    async def receive():
        try:
            return next(messages)
        except StopIteration:
            return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    # These test ASGI apps use only immediate async receive/send callbacks. Drive
    # their coroutine directly to avoid TestClient/Windows event-loop socket setup.
    coroutine = app(_scope(headers=headers, path=path), receive, send).__await__()
    while True:
        try:
            yielded = coroutine.send(None)
        except StopIteration:
            break
        if yielded is not None:
            raise AssertionError(f"ASGI test coroutine unexpectedly yielded {yielded!r}")
    return sent


def _response(sent: list[dict]) -> tuple[int, dict[str, str], bytes]:
    start = next(message for message in sent if message["type"] == "http.response.start")
    headers = {key.decode().lower(): value.decode() for key, value in start["headers"]}
    body = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    return start["status"], headers, body


async def _echo_body(scope, receive, send):
    body = bytearray()
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            response = JSONResponse(status_code=400, content={"detail": "Client disconnected"})
            await response(scope, receive, send)
            return
        body.extend(message.get("body", b""))
        if not message.get("more_body", False):
            response = JSONResponse(content={"body": body.decode("utf-8", errors="replace")})
            await response(scope, receive, send)
            return


def test_valid_content_length_and_multichunk_stream_below_limit():
    middleware = RequestBodyLimitMiddleware(_echo_body, max_body_bytes=8)
    sent = _run_asgi(
        middleware,
        headers=[(b"content-length", b"6")],
        chunks=[
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ],
    )
    status, _, body = _response(sent)
    assert status == 200
    assert json.loads(body) == {"body": "abcdef"}


def test_chunked_request_without_content_length_is_counted_across_chunks():
    middleware = RequestBodyLimitMiddleware(_echo_body, max_body_bytes=5)
    sent = _run_asgi(
        middleware,
        chunks=[
            {"type": "http.request", "body": b"ab", "more_body": True},
            {"type": "http.request", "body": b"cd", "more_body": True},
            {"type": "http.request", "body": b"e", "more_body": False},
        ],
    )
    status, _, body = _response(sent)
    assert status == 200
    assert json.loads(body) == {"body": "abcde"}


def test_chunked_request_exceeding_limit_stops_processing_with_sanitized_413():
    processed = False

    async def app(scope, receive, send):
        nonlocal processed
        processed = True
        await _echo_body(scope, receive, send)

    middleware = RequestBodyLimitMiddleware(app, max_body_bytes=5)
    sent = _run_asgi(
        middleware,
        chunks=[
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": True},
            {"type": "http.request", "body": b"unread", "more_body": False},
        ],
    )
    status, headers, body = _response(sent)
    assert processed is True
    assert status == 413
    assert json.loads(body) == {"detail": "Request body is too large"}
    assert len([message for message in sent if message["type"] == "http.response.start"]) == 1


def test_disconnect_is_forwarded_without_becoming_oversized():
    middleware = RequestBodyLimitMiddleware(_echo_body, max_body_bytes=5)
    sent = _run_asgi(
        middleware,
        chunks=[
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.disconnect"},
        ],
    )
    status, _, body = _response(sent)
    assert status == 400
    assert json.loads(body) == {"detail": "Client disconnected"}


def test_oversized_multipart_stream_is_rejected_before_parser_receives_later_chunks():
    parser_chunks: list[bytes] = []

    async def multipart_parser(scope, receive, send):
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            parser_chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                await JSONResponse(content={"ok": True})(scope, receive, send)
                return

    middleware = RequestBodyLimitMiddleware(multipart_parser, max_body_bytes=12)
    sent = _run_asgi(
        middleware,
        headers=[(b"content-type", b"multipart/form-data; boundary=x")],
        chunks=[
            {"type": "http.request", "body": b"--x\r\nbody", "more_body": True},
            {"type": "http.request", "body": b"123456", "more_body": True},
            {"type": "http.request", "body": b"should-not-be-read", "more_body": False},
        ],
    )
    status, _, _ = _response(sent)
    assert status == 413
    assert parser_chunks == [b"--x\r\nbody"]


def test_no_second_response_if_downstream_started_before_limit_was_crossed():
    async def streaming_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"started", "more_body": True})
        await receive()

    middleware = RequestBodyLimitMiddleware(streaming_app, max_body_bytes=2)
    sent = _run_asgi(middleware, chunks=[{"type": "http.request", "body": b"large", "more_body": False}])
    assert len([message for message in sent if message["type"] == "http.response.start"]) == 1
    assert sent[0]["status"] == 200
    assert sent[-1] == {"type": "http.response.body", "body": b"", "more_body": False}


def test_full_application_413_has_request_id_and_security_headers():
    app = create_app()
    limit = app.user_middleware[-1].kwargs["max_body_bytes"]
    sent = _run_asgi(
        app,
        headers=[(b"content-length", str(limit + 1).encode("ascii"))],
        chunks=[],
        path="/api/v1/health",
    )
    status, headers, body = _response(sent)
    assert status == 413
    assert json.loads(body) == {"detail": "Request body is too large"}
    assert headers["x-request-id"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"


def test_chunked_413_keeps_request_id_and_security_headers():
    middleware = RequestCorrelationMiddleware(
        RequestBodyLimitMiddleware(_echo_body, max_body_bytes=3), max_body_bytes=3,
    )
    sent = _run_asgi(
        middleware,
        chunks=[
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"d", "more_body": False},
        ],
    )
    status, headers, body = _response(sent)
    assert status == 413
    assert json.loads(body) == {"detail": "Request body is too large"}
    assert headers["x-request-id"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"


def test_invalid_declared_length_returns_bad_request_with_security_headers():
    app = create_app()
    sent = _run_asgi(
        app,
        headers=[(b"content-length", b"not-a-number")],
        chunks=[],
        path="/api/v1/health",
    )
    status, headers, body = _response(sent)
    assert status == 400
    assert json.loads(body) == {"detail": "Invalid Content-Length"}
    assert headers["x-request-id"]
    assert headers["x-content-type-options"] == "nosniff"


def test_normal_json_request_remains_functional_through_application_middleware():
    app = create_app()

    async def json_echo(request: Request):
        return JSONResponse(content=await request.json())

    app.add_api_route("/__security/json-echo", json_echo, methods=["POST"])
    payload = b'{"safe":true}'
    sent = _run_asgi(
        app,
        headers=[(b"content-type", b"application/json"),
                 (b"content-length", str(len(payload)).encode("ascii"))],
        chunks=[{"type": "http.request", "body": payload, "more_body": False}],
        path="/__security/json-echo",
    )
    status, _, body = _response(sent)
    assert status == 200
    assert json.loads(body) == {"safe": True}
