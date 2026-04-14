"""
API Client - HTTP client with retry, circuit breaker, and rate limiting
Generic client for calling external/internal APIs
"""

import asyncio
import json
import time
import hashlib
from typing import Optional, Dict, Any, Callable, TypeVar
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import structlog

logger = structlog.get_logger()

# Try importing httpx
HTTPX_AVAILABLE = False
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    pass

T = TypeVar('T')


class CircuitState(str, Enum):
    """Circuit breaker states"""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, rejecting requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration"""
    failure_threshold: int = 5        # Failures before opening
    success_threshold: int = 2         # Successes before closing
    timeout: int = 60                  # Seconds before half-open
    half_open_max_calls: int = 3       # Max calls in half-open state
    
    # Error types to count as failures
    retryable_errors: tuple = (
        "timeout", "connection", "503", "502", "429"
    )


class CircuitBreaker:
    """
    Circuit breaker pattern implementation
    Prevents cascading failures by rejecting requests when a service is down
    """
    
    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.half_open_calls = 0
        
        self._lock = asyncio.Lock()
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection"""
        async with self._lock:
            # Check if circuit should transition
            await self._check_transition()
            
            # If open, reject immediately
            if self.state == CircuitState.OPEN:
                raise CircuitOpenError(f"Circuit {self.name} is OPEN")
            
            # If half-open, check call limit
            if self.state == CircuitState.HALF_OPEN:
                if self.half_open_calls >= self.config.half_open_max_calls:
                    raise CircuitOpenError(f"Circuit {self.name} is HALF_OPEN (max calls reached)")
                self.half_open_calls += 1
        
        # Execute function
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            
            await self._on_success()
            return result
            
        except Exception as e:
            await self._on_failure(str(e))
            raise
    
    async def _check_transition(self):
        """Check if circuit should transition states"""
        if self.state == CircuitState.OPEN:
            if self.last_failure_time:
                elapsed = (datetime.utcnow() - self.last_failure_time).total_seconds()
                if elapsed >= self.config.timeout:
                    logger.info(f"Circuit {self.name} transitioning to HALF_OPEN")
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_calls = 0
    
    async def _on_success(self):
        """Handle successful call"""
        async with self._lock:
            self.failure_count = 0
            
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.config.success_threshold:
                    logger.info(f"Circuit {self.name} closing")
                    self.state = CircuitState.CLOSED
                    self.success_count = 0
    
    async def _on_failure(self, error: str):
        """Handle failed call"""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = datetime.utcnow()
            
            # Check if should open
            if self.failure_count >= self.config.failure_threshold:
                if self.state == CircuitState.CLOSED:
                    logger.warning(f"Circuit {self.name} opening due to {self.failure_count} failures")
                    self.state = CircuitState.OPEN
            elif self.state == CircuitState.HALF_OPEN:
                logger.warning(f"Circuit {self.name} reopening from HALF_OPEN")
                self.state = CircuitState.OPEN
                self.half_open_calls = 0
    
    def get_status(self) -> Dict[str, Any]:
        """Get circuit status"""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure": self.last_failure_time.isoformat() if self.last_failure_time else None,
        }


class CircuitOpenError(Exception):
    """Raised when circuit is open"""
    pass


@dataclass
class RetryConfig:
    """Retry configuration"""
    max_attempts: int = 3
    base_delay: float = 1.0          # Base delay seconds
    max_delay: float = 60.0          # Max delay seconds
    exponential_base: float = 2.0     # Exponential multiplier
    jitter: bool = True              # Add randomness
    retryable_status_codes: tuple = (
        408, 429, 500, 502, 503, 504
    )


@dataclass
class RateLimitConfig:
    """Rate limiting configuration"""
    max_requests: int = 100           # Max requests
    window_seconds: int = 60          # Time window
    wait_on_limit: bool = True       # Wait instead of error
    max_wait_seconds: float = 30      # Max wait time


class RateLimiter:
    """Token bucket rate limiter"""
    
    def __init__(self, config: Optional[RateLimitConfig] = None):
        self.config = config or RateLimitConfig()
        self.tokens = self.config.max_requests
        self.last_update = time.time()
        self._lock = asyncio.Lock()
    
    async def acquire(self) -> bool:
        """Acquire a token (wait if necessary)"""
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_update
            
            # Refill tokens
            tokens_to_add = elapsed * (self.config.max_requests / self.config.window_seconds)
            self.tokens = min(self.config.max_requests, self.tokens + tokens_to_add)
            self.last_update = now
            
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            
            # Not enough tokens
            if not self.config.wait_on_limit:
                return False
            
            # Calculate wait time
            wait_time = (1 - self.tokens) / (self.config.max_requests / self.config.window_seconds)
            wait_time = min(wait_time, self.config.max_wait_seconds)
            
            if wait_time > 0:
                await asyncio.sleep(wait_time)
                self.tokens = 0
                self.last_update = time.time()
                return True
            
            return False


@dataclass
class APIResponse:
    """Standard API response"""
    status_code: int
    data: Any
    headers: Dict[str, str]
    elapsed_ms: float
    cached: bool = False
    
    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300
    
    @property
    def error(self) -> Optional[str]:
        if not self.ok:
            return f"HTTP {self.status_code}: {self.data}"
        return None


class APIClient:
    """
    Generic HTTP client with:
    - Automatic retry with exponential backoff
    - Circuit breaker pattern
    - Rate limiting
    - Caching
    - Request/response logging
    """
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        retry_config: Optional[RetryConfig] = None,
        circuit_config: Optional[CircuitBreakerConfig] = None,
        rate_limit_config: Optional[RateLimitConfig] = None,
        enable_cache: bool = True,
        cache_ttl: int = 300,
    ):
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx required for APIClient: pip install httpx")
        
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.retry_config = retry_config or RetryConfig()
        self.circuit_config = circuit_config
        self.rate_limit_config = rate_limit_config
        self.enable_cache = enable_cache
        self.cache_ttl = cache_ttl
        
        # Components
        self._client: Optional[httpx.AsyncClient] = None
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._rate_limiter: Optional[RateLimiter] = None
        self._cache: Dict[str, tuple] = {}  # key -> (response, expiry)
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client"""
        if self._client is None:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout,
            )
        return self._client
    
    async def close(self):
        """Close HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    def _get_cache_key(self, method: str, url: str, params: Dict) -> str:
        """Generate cache key"""
        key_data = f"{method}:{url}:{json.dumps(params or {}, sort_keys=True)}"
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def _get_cached(self, cache_key: str) -> Optional[APIResponse]:
        """Get cached response"""
        if cache_key in self._cache:
            response, expiry = self._cache[cache_key]
            if time.time() < expiry:
                response.cached = True
                return response
            del self._cache[cache_key]
        return None
    
    def _set_cached(self, cache_key: str, response: APIResponse):
        """Cache response"""
        if response.ok:
            self._cache[cache_key] = (response, time.time() + self.cache_ttl)
    
    def _get_circuit(self, name: str) -> CircuitBreaker:
        """Get or create circuit breaker"""
        if name not in self._circuit_breakers:
            self._circuit_breakers[name] = CircuitBreaker(name, self.circuit_config)
        return self._circuit_breakers[name]
    
    def _get_rate_limiter(self) -> RateLimiter:
        """Get or create rate limiter"""
        if self._rate_limiter is None:
            self._rate_limiter = RateLimiter(self.rate_limit_config)
        return self._rate_limiter
    
    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay for retry with exponential backoff"""
        delay = self.retry_config.base_delay * (self.retry_config.exponential_base ** attempt)
        delay = min(delay, self.retry_config.max_delay)
        
        if self.retry_config.jitter:
            import random
            delay *= (0.5 + random.random())
        
        return delay
    
    async def _execute_request(
        self,
        method: str,
        url: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        circuit_name: Optional[str] = None,
        use_cache: bool = True,
    ) -> APIResponse:
        """Execute HTTP request with retry"""
        start_time = time.time()
        
        # Check cache for GET requests
        if method.upper() == "GET" and use_cache and self.enable_cache:
            cache_key = self._get_cache_key(method, url, params)
            cached = self._get_cached(cache_key)
            if cached:
                logger.debug("Cache hit", url=url)
                return cached
        
        # Rate limiting
        if self.rate_limit_config:
            rate_limiter = self._get_rate_limiter()
            await rate_limiter.acquire()
        
        # Circuit breaker
        circuit = self._get_circuit(circuit_name or url)
        
        last_error = None
        for attempt in range(self.retry_config.max_attempts):
            try:
                async def _do_request():
                    client = await self._get_client()
                    return await client.request(
                        method=method,
                        url=url,
                        params=params,
                        json=json_data,
                        headers=headers,
                    )
                
                response = await circuit.call(_do_request)
                
                elapsed_ms = (time.time() - start_time) * 1000
                
                # Check if retryable
                if response.status_code in self.retry_config.retryable_status_codes:
                    last_error = f"Retryable status: {response.status_code}"
                    if attempt < self.retry_config.max_attempts - 1:
                        delay = self._calculate_delay(attempt)
                        logger.warning(f"Retrying {url}", 
                                     attempt=attempt + 1, 
                                     delay=delay,
                                     status=response.status_code)
                        await asyncio.sleep(delay)
                        continue
                
                # Parse response
                try:
                    data = response.json()
                except Exception:
                    data = response.text
                
                api_response = APIResponse(
                    status_code=response.status_code,
                    data=data,
                    headers=dict(response.headers),
                    elapsed_ms=elapsed_ms,
                )
                
                # Cache successful GET
                if method.upper() == "GET" and use_cache and self.enable_cache:
                    self._set_cached(cache_key, api_response)
                
                return api_response
                
            except CircuitOpenError:
                raise
            except httpx.TimeoutException as e:
                last_error = f"Timeout: {str(e)}"
                logger.warning("Request timeout", url=url, attempt=attempt + 1)
            except httpx.ConnectError as e:
                last_error = f"Connection error: {str(e)}"
                logger.warning("Connection error", url=url, attempt=attempt + 1)
            except Exception as e:
                last_error = str(e)
                logger.error("Request failed", url=url, error=str(e))
            
            # Retry
            if attempt < self.retry_config.max_attempts - 1:
                delay = self._calculate_delay(attempt)
                await asyncio.sleep(delay)
        
        # All retries failed
        elapsed_ms = (time.time() - start_time) * 1000
        return APIResponse(
            status_code=0,
            data={"error": last_error or "Request failed"},
            headers={},
            elapsed_ms=elapsed_ms,
        )
    
    async def get(
        self,
        url: str,
        params: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        circuit_name: Optional[str] = None,
    ) -> APIResponse:
        """Send GET request"""
        return await self._execute_request(
            method="GET",
            url=url,
            params=params,
            headers=headers,
            circuit_name=circuit_name,
        )
    
    async def post(
        self,
        url: str,
        json_data: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        circuit_name: Optional[str] = None,
    ) -> APIResponse:
        """Send POST request"""
        return await self._execute_request(
            method="POST",
            url=url,
            json_data=json_data,
            headers=headers,
            circuit_name=circuit_name,
            use_cache=False,
        )
    
    async def put(
        self,
        url: str,
        json_data: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        circuit_name: Optional[str] = None,
    ) -> APIResponse:
        """Send PUT request"""
        return await self._execute_request(
            method="PUT",
            url=url,
            json_data=json_data,
            headers=headers,
            circuit_name=circuit_name,
            use_cache=False,
        )
    
    async def delete(
        self,
        url: str,
        headers: Optional[Dict] = None,
        circuit_name: Optional[str] = None,
    ) -> APIResponse:
        """Send DELETE request"""
        return await self._execute_request(
            method="DELETE",
            url=url,
            headers=headers,
            circuit_name=circuit_name,
            use_cache=False,
        )
    
    def get_circuit_status(self, name: Optional[str] = None) -> Dict[str, Any]:
        """Get circuit breaker status"""
        if name:
            if name in self._circuit_breakers:
                return self._circuit_breakers[name].get_status()
            return {}
        
        return {
            name: cb.get_status() 
            for name, cb in self._circuit_breakers.items()
        }
    
    def clear_cache(self):
        """Clear response cache"""
        self._cache.clear()


# Convenience function
def create_api_client(
    base_url: str,
    api_key: Optional[str] = None,
    **kwargs
) -> APIClient:
    """Create configured API client"""
    return APIClient(
        base_url=base_url,
        api_key=api_key,
        **kwargs
    )
