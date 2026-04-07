# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v1.0.0] - 2024-01-15 - Production Ready Release

[Full Diff](https://github.com/thichcode/supervisor-api/compare/v0.9.0...v1.0.0)

### Added

#### P0 - Critical Production Features

- **Circuit Breaker Pattern** (`src/core/circuit_breaker.py`)
  - Automatic failure detection and circuit opening
  - Configurable failure/success thresholds
  - Half-open state for recovery testing
  - Comprehensive metrics tracking (failures, successes, state transitions)

- **Dead Letter Queue (DLQ)** (`src/core/dlq.py`)
  - Automatic retry mechanism with configurable max attempts
  - Status tracking: pending → retrying → resolved/failed
  - Admin endpoints for DLQ management
  - Retry delay configuration

- **Enhanced Error Handling** (`src/core/error_handler.py`)
  - Zero internal error leakage to clients
  - Structured error responses with error codes
  - Admin endpoints: `/admin/errors/dlq`, `/admin/errors/circuit-breakers`
  - Detailed logging with request tracing

#### P1 - High Priority Features

- **Structured LLM Output** (`src/llm/client_v2.py`)
  - JSON mode for reliable parsing
  - Built-in cost tracking per request
  - Circuit breaker integration
  - Confidence scoring

- **Prometheus Alert Rules** (`config/prometheus_alerts.yml`)
  - HighErrorRate alert (>5% error rate)
  - CircuitBreakerOpen monitoring
  - DLQBacklogGrowing alert
  - LLMCostSpike detection
  - Availability SLO (99.9%)
  - Latency SLO (p95 < 2s)
  - Database connection monitoring
  - Redis health checks

- **Enhanced Metrics** (`src/core/metrics.py`)
  - Circuit breaker state metrics
  - DLQ metrics (pending, retrying, resolved, failed)
  - Token usage and cost tracking
  - Rate limit metrics

#### P2 - Operational Excellence

- **OpenTelemetry Distributed Tracing** (`src/core/tracing.py`)
  - Full distributed tracing support
  - Decorators: `@traced`, `@traced_llm_call`, `@traced_agent`, `@traced_db_query`
  - Context propagation across services
  - OTLP export for Grafana Tempo / Jaeger
  - Console export for local development
  - Custom metrics: request duration, LLM latency, DB query time
  - Graceful fallback when OpenTelemetry unavailable

- **Authentication Layer** (`src/core/auth.py`)
  - JWT token validation (HS256/RS256 algorithms)
  - HMAC webhook signature verification with replay attack prevention
  - API Key authentication for service-to-service communication
  - Role-based access control (Admin/Service/User/Guest)
  - Scope-based permissions (read/write/admin)
  - Service account token generation utilities
  - FastAPI dependencies: `require_auth`, `require_role`, `require_scope`

- **Load Testing Suite** (`load_test/`)
  - k6 smoke test (1 VU, 30 seconds)
  - k6 load test with ramping VUs (50 VUs, 5 minutes)
  - Locust alternative with Web UI support
  - Quick test script for CI/CD pipelines
  - Realistic payload generation
  - Metrics: p50, p95, p99 latency, error rates, throughput

### Changed

- Improved error messages for webhook validation failures
- Enhanced logging with structured output (JSON format)
- Updated health check endpoints to include dependency status
- Refactored metrics to use Prometheus exposition format

### Fixed

- N/A (initial production release)

### Deprecated

- N/A

### Security

- HMAC signature verification prevents replay attacks (5-minute timestamp window)
- JWT audience and issuer validation
- Constant-time signature comparison to prevent timing attacks
- No internal errors leaked to API responses

---

## [v0.9.0] - 2024-01-01 - Beta Release

### Added

- Initial FastAPI application structure
- MS Teams webhook integration
- Basic n8n webhook handler
- PostgreSQL conversation storage
- Redis caching layer
- Basic LLM client (Azure OpenAI)
- Intent classification system
- Agent routing logic
- Health check endpoints
- Prometheus metrics endpoint
- Docker containerization
- Basic unit tests

### Known Issues

- No circuit breaker (cascading failures possible)
- No DLQ (failed requests lost)
- No authentication (webhook secret only)
- No distributed tracing
- No load testing

---

## [v0.1.0] - 2023-12-01 - Initial Commit

### Added

- Project scaffolding
- Basic README
- Placeholder configuration
