from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from typing import Callable, Any
import structlog

logger = structlog.get_logger()


class RetryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Any:
        response = await call_next(request)
        return response


async def retry_on_exception(func: Callable, *args, max_attempts: int = 2, **kwargs) -> Any:
    last_exception = None
    
    for attempt in range(max_attempts):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            if attempt < max_attempts - 1:
                wait_time = min(2 ** attempt, 10)
                logger.warning(
                    "Retry attempt",
                    func=func.__name__,
                    attempt=attempt + 1,
                    max_attempts=max_attempts,
                    wait_seconds=wait_time,
                    error=str(e),
                )
                import asyncio
                await asyncio.sleep(wait_time)
            else:
                logger.error(
                    "All retry attempts failed",
                    func=func.__name__,
                    error=str(e),
                )
    
    raise last_exception


class DatabaseError(HTTPException):
    def __init__(self, detail: str = "Database error"):
        super().__init__(status_code=503, detail=detail)


class CacheError(HTTPException):
    def __init__(self, detail: str = "Cache error"):
        super().__init__(status_code=503, detail=detail)


class LLMError(HTTPException):
    def __init__(self, detail: str = "LLM service error"):
        super().__init__(status_code=503, detail=detail)


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled exception",
        path=request.url.path,
        method=request.method,
        error=str(exc),
        error_type=type(exc).__name__,
    )
    
    if isinstance(exc, HTTPException):
        raise exc
    
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc) if hasattr(exc, '__str__') else "Unknown error",
        },
    )
