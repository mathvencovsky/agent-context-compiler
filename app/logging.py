"""Structured logging — structlog JSON output + request_id ASGI middleware (M0)."""

from __future__ import annotations

import logging
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = structlog.get_logger()


def configure_logging() -> None:
    """Configure structlog to emit JSON and bind it to the stdlib logging root.

    Idempotent enough for repeated calls in tests: structlog.configure replaces
    any previous configuration. stdlib logging is routed through structlog's
    ProcessorFormatter so third-party libraries share the same JSON output.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso")

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


#: Response header carrying the request id (guide §11: debuggability).
REQUEST_ID_HEADER = "X-Request-ID"
#: Which MCP client made the call, for the local usage record.
CLIENT_HEADER = "X-NINA-Client"
#: Key of the request id in ``scope["state"]`` (``request.state.request_id``).
REQUEST_ID_STATE_KEY = "request_id"


def request_id_of(scope_state: object) -> str | None:
    """Return the request id stored on ``request.state``, if any."""
    value = getattr(scope_state, REQUEST_ID_STATE_KEY, None)
    return value if isinstance(value, str) else None


class RequestIdMiddleware:
    """ASGI middleware that mints a request_id per request and logs start/end.

    The request_id (uuid4 hex) is bound to structlog contextvars so every log
    line emitted while handling the request carries it, stored on
    ``request.state.request_id`` so the error handlers can put it in the error
    envelope, and returned as the ``X-Request-ID`` response header. It is always
    minted by the server: a client-sent ``X-Request-ID`` is ignored, so a caller
    cannot forge ids into the logs. Contextvars are cleared after the response
    completes so ids never leak across requests.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})[REQUEST_ID_STATE_KEY] = request_id
        method = scope.get("method", "")
        path = scope.get("path", "")

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = MutableHeaders(scope=message)
                if REQUEST_ID_HEADER not in headers:
                    headers.append(REQUEST_ID_HEADER, request_id)
            await send(message)

        logger.info("request.start", method=method, path=path)
        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.info(
                "request.end",
                method=method,
                path=path,
                status=status_code,
                duration_ms=duration_ms,
            )
            structlog.contextvars.clear_contextvars()


__all__ = [
    "REQUEST_ID_HEADER",
    "RequestIdMiddleware",
    "configure_logging",
    "logger",
    "request_id_of",
]
