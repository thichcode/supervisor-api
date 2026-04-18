# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [v1.2.3] - 2026-04-10 - Ollama/llama.cpp Timeout Fix

[Full Diff](https://github.com/thichcode/supervisor-api/compare/v1.2.2...v1.2.3)

### Fixed

- Use `OLLAMA_TIMEOUT` env var for httpx client timeout (was using `agent_timeout` instead)
- Compatible with llama.cpp server

---

## [v1.2.2] - 2026-04-10 - CompletionUsage Object Fix

[Full Diff](https://github.com/thichcode/supervisor-api/compare/v1.2.1...v1.2.2)

### Fixed

- Handle `CompletionUsage` object from llama.cpp (convert to dict properly)
- Add `_extract_usage()` helper for safe dict conversion
- Set `_active_provider` correctly in `__init__` from `_explicit_provider`
- All `usage.get()` calls now handle CompletionUsage object

### Changed

- Compatible with llama.cpp OpenAI-compatible API

---

## [v1.2.1] - 2026-04-10 - LLM Provider Config Fix

[Full Diff](https://github.com/thichcode/supervisor-api/compare/v1.2.0...v1.2.1)

### Fixed

- Model detection bug (gemma2 was using wrong provider)
- Add explicit `LLM_PROVIDER` env var support

---

## [v1.2.0] - 2026-04-10 - Multi-Provider LLM Support

[Full Diff](https://github.com/thichcode/supervisor-api/compare/v1.1.4...v1.2.0)

### Added

- **Multi-Provider LLM Client** (`src/llm/provider.py`)
  - Support for Ollama, llama.cpp, OpenAI, Azure OpenAI
  - Automatic provider detection from model name
  - Explicit provider override via `LLM_PROVIDER` env var
  - Circuit breaker pattern for all providers
  - Cost tracking for cloud providers
  - Vietnamese-optimized defaults

- **GitHub Actions Auto-Tagging Workflow**
  - Automatic version bump on main branch push
  - Semantic versioning (major/minor/patch)
  - Git tag creation and push

### Changed

- `src/llm/` module restructured
- `MultiProviderLLMClient` is now the default client

---

## [v1.1.4] - 2026-04-09 - Ollama Integration

[Full Diff](https://github.com/thichcode/supervisor-api/compare/v1.1.3...v1.1.4)

### Added

- Ollama server support for Vietnamese models
- Configurable Ollama base URL
- Ollama timeout configuration

---

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

- **Multi-Provider LLM** (`src/llm/provider.py`, `src/llm/ensemble.py`)
  - Ollama, Azure OpenAI, OpenAI support
  - Model switching and fallback
  - Cost tracking per request
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

---

## [Unreleased] - 2026-04-08

### Added

- **Canonical API entrypoint** (`src/api/app.py`)
  - Introduced a stable FastAPI application module
  - Reduced architectural ambiguity between `src/api.py` and `src/api/`

- **MemPalace external memory integration (prototype v1-v7)**
  - Added `src/memory/mempalace_adapter.py`
  - Added optional external memory retrieval into `MemoryService.retrieve()`
  - Added optional write-back for reusable insights and detected user preferences
  - Added provider abstraction via `src/memory/providers.py`
  - Added provider factory and injectable provider support in `MemoryService`
  - Added external provider readiness/health participation in `/health/ready`
  - Added timeout/retry/circuit-breaker protection for MemPalace provider operations
  - Added mapping policy for supervisor-api domain → MemPalace wing/room resolution
  - Added multi-backend registry support with null provider and JSON file-based provider
  - Added backend routing policy to choose providers dynamically by request shape

- **External memory observability**
  - Added `supervisor_external_memory_operations_total`
  - Added structured logs for MemPalace search, write, and health-check flows

- **Documentation**
  - Added `CONFIGURATION_GUIDE.md`
  - Added `PRODUCT_SCORECARD.md`
  - Added CI workflow in `.github/workflows/ci.yml`

### Changed

- Updated project metadata to `version = "1.0.0"` in `pyproject.toml`
- README now documents:
  - canonical app entrypoint
  - external memory env vars
  - multi-backend provider registry
  - backend routing heuristics
  - external memory metrics
  - current readiness score and scorecard links
- `MemoryContext` now supports `external_memory`

### Fixed

- Fixed circular import/module shadowing between `src/api.py` and `src/api/`
- Fixed incorrect `logging.Record` annotation to `logging.LogRecord`
- Fixed circuit breaker state transition timing behavior
- Fixed JWT fallback behavior when `PyJWT` is unavailable
- Fixed role/scope dependency factory behavior to avoid coroutine misuse
- Fixed webhook secret behavior causing unexpected 500s in tests
- Fixed deprecated UTC timestamp usage in logging and memory modules

### Testing

- Expanded test coverage for MemPalace adapter, resilience, mapping, provider injection, provider registry, and provider routing behavior
- Current validated test status: `68 passed`

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
