from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response

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

# Circuit Breaker Metrics
CIRCUIT_BREAKER_STATE = Gauge(
    'supervisor_circuit_breaker_state',
    'Circuit breaker state (0=closed, 1=half_open, 2=open)',
    ['name']
)

CIRCUIT_BREAKER_FAILURES = Counter(
    'supervisor_circuit_breaker_failures_total',
    'Circuit breaker failures',
    ['name']
)

CIRCUIT_BREAKER_REJECTED = Counter(
    'supervisor_circuit_breaker_rejected_total',
    'Circuit breaker rejected calls',
    ['name']
)

# Dead Letter Queue Metrics
DLQ_ENTRIES = Gauge(
    'supervisor_dlq_entries_total',
    'DLQ entries by status',
    ['status']
)

DLQ_OPERATIONS = Counter(
    'supervisor_dlq_operations_total',
    'DLQ operations',
    ['operation']
)

# LLM Cost Metrics
LLM_COST = Counter(
    'supervisor_llm_cost_total',
    'Total LLM cost in USD',
    ['model']
)

# Rate Limiting Metrics
RATE_LIMIT_EXCEEDED = Counter(
    'supervisor_rate_limit_exceeded_total',
    'Rate limit exceeded count'
)

# Database Pool Metrics
DB_POOL_SIZE = Gauge(
    'supervisor_db_pool_size',
    'Database connection pool size'
)

DB_POOL_AVAILABLE = Gauge(
    'supervisor_db_pool_available',
    'Available database connections'
)

# Redis Metrics
REDIS_ERRORS = Counter(
    'supervisor_redis_errors_total',
    'Redis errors',
    ['error_type']
)

KB_SEARCHES = Counter(
    'supervisor_kb_searches_total',
    'Knowledge base searches and outcomes',
    ['search_type', 'outcome']
)

KB_RERANKS = Counter(
    'supervisor_kb_reranks_total',
    'Knowledge base re-rank attempts',
    ['search_type', 'status']
)

KB_CLARIFICATIONS = Counter(
    'supervisor_kb_clarifications_total',
    'Knowledge base clarification prompts',
    ['search_type', 'reason']
)

KB_FALLBACKS = Counter(
    'supervisor_kb_fallbacks_total',
    'Knowledge base fallback outcomes',
    ['search_type', 'reason']
)

KB_TEMPLATES = Counter(
    'supervisor_kb_templates_total',
    'Knowledge base template detections',
    ['template_id', 'search_type', 'outcome']
)

APPROVAL_ACTIONS = Counter(
    'supervisor_approval_actions_total',
    'Approval lifecycle actions',
    ['status']
)

DELIVERY_ACTIONS = Counter(
    'supervisor_delivery_actions_total',
    'Response delivery actions',
    ['channel', 'status']
)

EXTERNAL_MEMORY_OPERATIONS = Counter(
    'supervisor_external_memory_operations_total',
    'External memory provider operations',
    ['provider', 'operation', 'status']
)

REASONING_LOOP_ROLLOUT = Counter(
    'supervisor_reasoning_loop_rollout_total',
    'Reasoning loop rollout decisions',
    ['scope', 'outcome']
)

REASONING_LOOP_OUTCOMES = Counter(
    'supervisor_reasoning_loop_outcomes_total',
    'Reasoning loop outcomes by status',
    ['status']
)

REASONING_LOOP_LATENCY = Histogram(
    'supervisor_reasoning_loop_latency_seconds',
    'Reasoning loop latency in seconds',
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

REASONING_LOOP_FALLBACKS = Counter(
    'supervisor_reasoning_loop_fallbacks_total',
    'Reasoning loop fallback events',
    ['reason']
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

    @staticmethod
    def record_circuit_breaker(name: str, state: int, failures: int = 0, rejected: int = 0):
        state_map = {"closed": 0, "half_open": 1, "open": 2}
        CIRCUIT_BREAKER_STATE.labels(name=name).set(state_map.get(state, state))
        if failures > 0:
            CIRCUIT_BREAKER_FAILURES.labels(name=name).inc(failures)
        if rejected > 0:
            CIRCUIT_BREAKER_REJECTED.labels(name=name).inc(rejected)

    @staticmethod
    def record_dlq(status: str, count: int = 1):
        DLQ_ENTRIES.labels(status=status).set(count)

    @staticmethod
    def record_dlq_operation(operation: str):
        DLQ_OPERATIONS.labels(operation=operation).inc()

    @staticmethod
    def record_llm_cost(model: str, cost_usd: float):
        LLM_COST.labels(model=model).inc(cost_usd)

    @staticmethod
    def record_rate_limit_exceeded():
        RATE_LIMIT_EXCEEDED.inc()

    @staticmethod
    def record_db_pool(size: int, available: int):
        DB_POOL_SIZE.set(size)
        DB_POOL_AVAILABLE.set(available)

    @staticmethod
    def record_redis_error(error_type: str):
        REDIS_ERRORS.labels(error_type=error_type).inc()

    @staticmethod
    def record_kb_search(search_type: str, outcome: str):
        KB_SEARCHES.labels(search_type=search_type, outcome=outcome).inc()

    @staticmethod
    def record_kb_rerank(search_type: str, status: str):
        KB_RERANKS.labels(search_type=search_type, status=status).inc()

    @staticmethod
    def record_kb_clarification(search_type: str, reason: str):
        KB_CLARIFICATIONS.labels(search_type=search_type, reason=reason).inc()

    @staticmethod
    def record_kb_fallback(search_type: str, reason: str):
        KB_FALLBACKS.labels(search_type=search_type, reason=reason).inc()

    @staticmethod
    def record_kb_template(template_id: str, search_type: str, outcome: str):
        KB_TEMPLATES.labels(template_id=template_id, search_type=search_type, outcome=outcome).inc()

    @staticmethod
    def record_approval_action(status: str):
        APPROVAL_ACTIONS.labels(status=status).inc()

    @staticmethod
    def record_delivery_action(channel: str, status: str):
        DELIVERY_ACTIONS.labels(channel=channel, status=status).inc()

    @staticmethod
    def record_external_memory(provider: str, operation: str, status: str):
        EXTERNAL_MEMORY_OPERATIONS.labels(
            provider=provider,
            operation=operation,
            status=status,
        ).inc()

    @staticmethod
    def record_reasoning_loop_rollout(scope: str, outcome: str):
        REASONING_LOOP_ROLLOUT.labels(scope=scope, outcome=outcome).inc()

    @staticmethod
    def record_reasoning_loop_outcome(status: str):
        REASONING_LOOP_OUTCOMES.labels(status=status).inc()

    @staticmethod
    def record_reasoning_loop_latency(duration_seconds: float):
        REASONING_LOOP_LATENCY.observe(max(0.0, duration_seconds))

    @staticmethod
    def record_reasoning_loop_fallback(reason: str):
        REASONING_LOOP_FALLBACKS.labels(reason=reason).inc()


metrics = MetricsCollector()


def get_metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
