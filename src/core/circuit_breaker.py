"""
Circuit Breaker Pattern Implementation
Prevents cascading failures by stopping calls to failing services
"""
import asyncio
import time
from enum import Enum
from typing import Callable, TypeVar, Optional
from dataclasses import dataclass, field
from functools import wraps
import structlog

logger = structlog.get_logger()

T = TypeVar('T')


class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject all calls
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5        # Failures before opening
    success_threshold: int = 2        # Successes to close from half-open
    timeout: float = 30.0             # Seconds before trying half-open
    half_open_max_calls: int = 3      # Max concurrent calls in half-open


@dataclass
class CircuitBreakerMetrics:
    failures: int = 0
    successes: int = 0
    state_changes: int = 0
    rejected_calls: int = 0
    total_calls: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open"""
    def __init__(self, name: str, message: str):
        self.name = name
        super().__init__(message)


class CircuitBreaker:
    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._last_failure_time: Optional[float] = None
        self._successes_in_half_open = 0
        self._lock = asyncio.Lock()
        self.metrics = CircuitBreakerMetrics()

    @property
    def state(self) -> CircuitState:
        return self._state

    async def _check_state_transition(self):
        """Check if we should transition states"""
        current_time = time.time()
        
        if self._state == CircuitState.OPEN:
            # Check if timeout has passed
            if self._last_failure_time:
                elapsed = current_time - self._last_failure_time
                if elapsed >= self.config.timeout:
                    logger.info(
                        "circuit_breaker_half_open",
                        name=self.name,
                        elapsed_seconds=elapsed
                    )
                    self._state = CircuitState.HALF_OPEN
                    self._successes_in_half_open = 0
                    self.metrics.state_changes += 1

    async def record_success(self):
        """Record a successful call"""
        async with self._lock:
            self.metrics.last_success_time = time.time()
            
            if self._state == CircuitState.HALF_OPEN:
                self._successes_in_half_open += 1
                self.metrics.successes += 1
                
                if self._successes_in_half_open >= self.config.success_threshold:
                    logger.info(
                        "circuit_breaker_closed",
                        name=self.name,
                        successes=self._successes_in_half_open
                    )
                    self._state = CircuitState.CLOSED
                    self.metrics.state_changes += 1
                    self.metrics.failures = 0
                    
            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success
                self.metrics.failures = max(0, self.metrics.failures - 1)

    async def record_failure(self):
        """Record a failed call"""
        async with self._lock:
            self.metrics.last_failure_time = time.time()
            self.metrics.failures += 1
            
            if self._state == CircuitState.HALF_OPEN:
                # Any failure in half-open goes back to open
                logger.warning(
                    "circuit_breaker_reopened",
                    name=self.name,
                    failures=self.metrics.failures
                )
                self._state = CircuitState.OPEN
                self.metrics.state_changes += 1
                
            elif self._state == CircuitState.CLOSED:
                if self.metrics.failures >= self.config.failure_threshold:
                    self._last_failure_time = self.metrics.last_failure_time
                    logger.warning(
                        "circuit_breaker_opened",
                        name=self.name,
                        failures=self.metrics.failures
                    )
                    self._state = CircuitState.OPEN
                    self.metrics.state_changes += 1

    async def can_execute(self) -> bool:
        """Check if a call can be executed"""
        await self._check_state_transition()
        
        if self._state == CircuitState.OPEN:
            self.metrics.rejected_calls += 1
            return False
            
        if self._state == CircuitState.HALF_OPEN:
            # Allow limited concurrent calls
            return self._successes_in_half_open < self.config.half_open_max_calls
            
        return True

    def get_status(self) -> dict:
        """Get circuit breaker status"""
        return {
            "name": self.name,
            "state": self._state.value,
            "failures": self.metrics.failures,
            "rejected_calls": self.metrics.rejected_calls,
            "total_calls": self.metrics.total_calls,
            "state_changes": self.metrics.state_changes,
            "last_failure": self.metrics.last_failure_time,
            "last_success": self.metrics.last_success_time,
        }


def circuit_breaker(circuit: CircuitBreaker):
    """Decorator to add circuit breaker to async functions"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            circuit.metrics.total_calls += 1
            
            if not await circuit.can_execute():
                logger.warning(
                    "circuit_breaker_rejected",
                    name=circuit.name,
                    function=func.__name__
                )
                raise CircuitBreakerError(
                    circuit.name,
                    f"Circuit breaker is OPEN for {circuit.name}"
                )
            
            try:
                result = await func(*args, **kwargs)
                await circuit.record_success()
                return result
            except Exception as e:
                await circuit.record_failure()
                raise
                
        return wrapper
    return decorator


# Global circuit breakers for different services
circuit_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(name: str, config: Optional[CircuitBreakerConfig] = None) -> CircuitBreaker:
    """Get or create a circuit breaker"""
    if name not in circuit_breakers:
        circuit_breakers[name] = CircuitBreaker(name, config)
    return circuit_breakers[name]


def get_all_circuit_breakers_status() -> dict:
    """Get status of all circuit breakers"""
    return {
        name: cb.get_status() 
        for name, cb in circuit_breakers.items()
    }
