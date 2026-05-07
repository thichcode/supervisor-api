"""
Request ID tracking for structured logging.
Injects request_id into log context for traceability.
"""

import uuid
from contextvars import ContextVar
from typing import Optional

import structlog

# Context variable for current request ID
_current_request_id: ContextVar[Optional[str]] = ContextVar("current_request_id", default=None)


def get_current_request_id() -> Optional[str]:
    """Get the current request ID from context."""
    return _current_request_id.get()


def set_current_request_id(request_id: Optional[str]) -> None:
    """Set the current request ID in context."""
    _current_request_id.set(request_id)


def generate_request_id() -> str:
    """Generate a new unique request ID."""
    return str(uuid.uuid4())[:8]


def add_request_id_processor(logger, method_name, event_dict):
    """Structlog processor that adds request_id to log events."""
    request_id = get_current_request_id()
    if request_id:
        event_dict["request_id"] = request_id
    return event_dict


# Register the processor globally
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        add_request_id_processor,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)


class RequestIDMiddleware:
    """FastAPI middleware to inject request_id into log context."""

    async def __call__(self, request, call_next):
        request_id = request.headers.get("X-Request-ID", generate_request_id())
        token = _current_request_id.set(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            _current_request_id.reset(token)