from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response
import time
from typing import Callable
from functools import wraps

REQUESTS_TOTAL = Counter(
    'supervisor_requests_total',
    'Total requests',
    ['method', 'endpoint', 'status']
)

REQUESTS_DURATION = Histogram(
    'supervisor_request_duration_seconds',
    'Request duration',
    ['method', 'endpoint'],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

ACTIVE_REQUESTS = Gauge(
    'supervisor_active_requests',
    'Number of active requests'
)

LLM_REQUESTS = Counter(
    'supervisor_llm_requests_total',
    'Total LLM requests',
    ['model', 'status']
)

LLM_TOKENS = Counter(
    'supervisor_llm_tokens_total',
    'Total LLM tokens',
    ['model', 'type']
)

LLM_DURATION = Histogram(
    'supervisor_llm_duration_seconds',
    'LLM request duration',
    ['model'],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
)

MEMORY_OPERATIONS = Counter(
    'supervisor_memory_operations_total',
    'Memory operations',
    ['operation', 'status']
)

DECISIONS_TOTAL = Counter(
    'supervisor_decisions_total',
    'Decision outcomes',
    ['decision_type', 'intent', 'risk_level']
)

ERRORS_TOTAL = Counter(
    'supervisor_errors_total',
    'Total errors',
    ['error_type', 'endpoint']
)


class MetricsCollector:
    @staticmethod
    def record_request(method: str, endpoint: str, status: int, duration: float):
        REQUESTS_TOTAL.labels(method=method, endpoint=endpoint, status=status).inc()
        REQUESTS_DURATION.labels(method=method, endpoint=endpoint).observe(duration)

    @staticmethod
    def record_llm(model: str, status: str, tokens: int = 0, duration: float = 0):
        LLM_REQUESTS.labels(model=model, status=status).inc()
        if tokens > 0:
            LLM_TOKENS.labels(model=model, type='total').inc(tokens)
        if duration > 0:
            LLM_DURATION.labels(model=model).observe(duration)

    @staticmethod
    def record_memory(operation: str, status: str):
        MEMORY_OPERATIONS.labels(operation=operation, status=status).inc()

    @staticmethod
    def record_decision(decision_type: str, intent: str, risk_level: str):
        DECISIONS_TOTAL.labels(
            decision_type=decision_type,
            intent=intent,
            risk_level=risk_level
        ).inc()

    @staticmethod
    def record_error(error_type: str, endpoint: str):
        ERRORS_TOTAL.labels(error_type=error_type, endpoint=endpoint).inc()

    @staticmethod
    def increment_active():
        ACTIVE_REQUESTS.inc()

    @staticmethod
    def decrement_active():
        ACTIVE_REQUESTS.dec()


metrics = MetricsCollector()


def get_metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
