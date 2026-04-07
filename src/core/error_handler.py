"""
Enhanced Error Handling
- No internal error leakage to clients
- Structured error responses
- Automatic DLQ integration
"""
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Callable
import structlog
import traceback
import uuid
import time

from src.core.dlq import dlq
from src.core.circuit_breaker import get_all_circuit_breakers_status

logger = structlog.get_logger()


class AppError(Exception):
    """Base application error"""
    def __init__(self, message: str, code: str, status_code: int = 500):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)


class ValidationError(AppError):
    """Input validation error"""
    def __init__(self, message: str):
        super().__init__(message, "VALIDATION_ERROR", 400)


class AuthenticationError(AppError):
    """Authentication error"""
    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, "AUTH_ERROR", 401)


class RateLimitError(AppError):
    """Rate limit exceeded"""
    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__(message, "RATE_LIMIT", 429)


class ServiceUnavailableError(AppError):
    """Service temporarily unavailable"""
    def __init__(self, message: str = "Service temporarily unavailable"):
        super().__init__(message, "SERVICE_UNAVAILABLE", 503)


class CircuitBreakerOpenError(AppError):
    """Circuit breaker is open"""
    def __init__(self, service: str):
        super().__init__(
            f"Service {service} is temporarily unavailable. Please try again later.",
            "CIRCUIT_BREAKER_OPEN",
            503
        )


def create_error_response(
    code: str,
    message: str,
    request_id: str,
    details: dict = None
) -> dict:
    """Create a structured error response"""
    response = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "timestamp": time.time(),
        }
    }
    if details:
        response["error"]["details"] = details
    return response


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Middleware for global error handling"""
    
    async def dispatch(self, request: Request, call_next: Callable):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        
        start_time = time.time()
        
        try:
            response = await call_next(request)
            
            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id
            
            return response
            
        except HTTPException:
            raise
            
        except Exception as e:
            elapsed = time.time() - start_time
            error_id = str(uuid.uuid4())
            
            logger.error(
                "unhandled_exception",
                error_id=error_id,
                request_id=request_id,
                path=request.url.path,
                method=request.method,
                elapsed_ms=int(elapsed * 1000),
                error_type=type(e).__name__,
                error_message=str(e),
                traceback=traceback.format_exc()
            )
            
            return JSONResponse(
                status_code=500,
                content=create_error_response(
                    code="INTERNAL_ERROR",
                    message="An unexpected error occurred. Please try again later.",
                    request_id=request_id,
                    details={"error_id": error_id} if request.app.debug else None
                ),
                headers={"X-Request-ID": request_id}
            )


async def handle_processing_error(
    error: Exception,
    request_id: str,
    payload: dict,
    metadata: dict = None
) -> JSONResponse:
    """Handle processing errors with DLQ integration"""
    
    error_type = type(error).__name__
    error_message = str(error)
    
    # Log the error
    logger.error(
        "processing_error",
        request_id=request_id,
        error_type=error_type,
        error_message=error_message
    )
    
    # Check for circuit breaker errors
    if "CircuitBreaker" in error_type:
        logger.warning("circuit_breaker_error", request_id=request_id)
        return JSONResponse(
            status_code=503,
            content=create_error_response(
                code="SERVICE_UNAVAILABLE",
                message="The service is temporarily unavailable. Please try again later.",
                request_id=request_id
            )
        )
    
    # Add to DLQ for retry
    dlq_entry = dlq.add(
        request_id=request_id,
        payload=payload,
        error=error,
        metadata=metadata
    )
    
    # Return appropriate response based on retry count
    if dlq_entry.retry_count >= dlq_entry.max_retries:
        # Max retries exceeded - return error
        logger.error(
            "max_retries_exceeded",
            request_id=request_id,
            dlq_entry_id=dlq_entry.id
        )
        
        return JSONResponse(
            status_code=500,
            content=create_error_response(
                code="PROCESSING_FAILED",
                message="The request could not be processed after multiple attempts.",
                request_id=request_id,
                details={"error_id": dlq_entry.id} if metadata.get("debug") else None
            )
        )
    
    # Return accepted with retry info
    logger.info(
        "request_queued_for_retry",
        request_id=request_id,
        dlq_entry_id=dlq_entry.id,
        retry_count=dlq_entry.retry_count,
        next_retry_at=dlq_entry.next_retry_at
    )
    
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "request_id": request_id,
            "message": "Request queued for processing",
            "retry_info": {
                "attempt": dlq_entry.retry_count + 1,
                "max_attempts": dlq_entry.max_retries,
                "next_retry_at": dlq_entry.next_retry_at
            }
        }
    )


def add_error_handling_routes(app):
    """Add error handling related routes"""
    from fastapi import APIRouter
    
    router = APIRouter(prefix="/admin", tags=["admin"])
    
    @router.get("/errors/dlq")
    async def get_dlq_stats():
        """Get DLQ statistics"""
        return {
            "stats": dlq.get_stats(),
            "pending": [
                {
                    "id": e.id,
                    "request_id": e.original_request_id,
                    "error_type": e.error_type,
                    "retry_count": e.retry_count,
                    "status": e.status,
                    "created_at": e.created_at,
                    "next_retry_at": e.next_retry_at
                }
                for e in dlq.get_pending()
            ]
        }
    
    @router.get("/errors/circuit-breakers")
    async def get_circuit_breakers():
        """Get circuit breaker status"""
        return {"circuit_breakers": get_all_circuit_breakers_status()}
    
    @router.post("/errors/dlq/{entry_id}/retry")
    async def retry_dlq_entry(entry_id: str):
        """Manually retry a DLQ entry"""
        entry = dlq.get(entry_id)
        if not entry:
            return {"error": "Entry not found"}
        
        dlq.mark_retrying(entry_id)
        return {
            "status": "retrying",
            "entry_id": entry_id,
            "retry_count": entry.retry_count
        }
    
    @router.delete("/errors/dlq/{entry_id}")
    async def delete_dlq_entry(entry_id: str):
        """Delete a DLQ entry"""
        if dlq.remove(entry_id):
            return {"status": "deleted", "entry_id": entry_id}
        return {"error": "Entry not found"}
    
    app.include_router(router)
