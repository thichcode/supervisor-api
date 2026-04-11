# CLAUDE.md - AI Coding Assistant Instructions

> This file provides instructions for AI coding assistants (Claude Code, Cline, etc.)

## 🎯 Project Overview

**Project:** Multi-Agent Supervisor System  
**Purpose:** AI agent system with long-term memory for Microsoft Teams integration  
**Stack:** Python 3.10+, FastAPI, PostgreSQL, Redis, Azure OpenAI  
**Status:** Production Ready (v1.0.0)  
**Repo:** https://github.com/thichcode/supervisor-api

---

## 🚨 Critical Rules

### Security (NEVER Violate)

1. **Never commit secrets**
   ```python
   # ❌ WRONG
   api_key = "sk-1234567890"
   
   # ✅ CORRECT
   api_key = os.getenv("OPENAI_API_KEY")
   ```

2. **Validate all inputs**
   ```python
   # Always use Pydantic for API inputs
   class WebhookPayload(BaseModel):
       request_id: str
       message: str
       # ... with field validators
   ```

3. **Log securely**
   ```python
   # ❌ Never log these
   logger.info("user_login", password=password)
   
   # ✅ Mask sensitive fields
   logger.info("user_login", user_id=user_id, masked=true)
   ```

### Error Handling (ALWAYS Implement)

```python
# ✅ Correct pattern
try:
    result = await process_request()
except ValidationError as e:
    logger.warning("validation_failed", error=str(e))
    raise HTTPException(status_code=400, detail="Invalid request")
except CircuitBreakerOpenError:
    logger.error("circuit_open", service="llm_client")
    raise HTTPException(status_code=503, detail="Service temporarily unavailable")
except Exception as e:
    logger.exception("unexpected_error")  # Includes stack trace
    raise HTTPException(status_code=500, detail="Internal server error")
```

### Async Patterns (MANDATORY)

```python
# ❌ Never block
time.sleep(5)
requests.get(url)
result = sync_database_query()

# ✅ Always async
await asyncio.sleep(5)
response = await httpx.AsyncClient().get(url)
result = await pool.fetch("SELECT * FROM users")
```

---

## 📁 Code Organization

```
supervisor-api/
├── src/
│   ├── __init__.py
│   ├── api.py              # FastAPI app + routes
│   ├── config.py           # Settings from env
│   ├── llm/
│   │   └── provider.py      # Multi-provider LLM (Ollama/OpenAI/Azure)
│   ├── agents/
│   │   ├── __init__.py
│   │   └── subagents.py     # Context, Policy, Knowledge, Draft, QA agents
│   ├── knowledge/           # Knowledge Base layer (NEW)
│   │   ├── schemas.py       # Pydantic models
│   │   ├── repository.py    # CRUD operations
│   │   └── service.py       # RAG-style retrieval
│   ├── memory/
│   │   ├── service.py       # Memory service
│   │   ├── cache.py         # Redis cache
│   │   └── repository.py   # Memory repository
│   ├── core/
│   │   ├── __init__.py
│   │   ├── supervisor.py   # Main supervisor + decision engine
│   │   ├── schemas.py       # Pydantic models (Input/Output, Chat, Approval)
│   │   ├── intent_classifier.py   # Intent classification
│   │   ├── risk_evaluator.py      # Risk evaluation
│   │   ├── approval.py           # Approval workflow
│   │   ├── auth.py          # JWT/HMAC authentication
│   │   ├── tracing.py       # OpenTelemetry tracing
│   │   ├── metrics.py       # Prometheus metrics
│   │   ├── circuit_breaker.py
│   │   ├── dlq.py           # Dead letter queue
│   │   └── error_handler.py
│   └── db/
│       ├── models.py        # SQLAlchemy models (Message, UserProfile, CaseMemory, KB, Alert, Config)
│       └── session.py      # Database session
├── tests/
├── load_test/
├── config/
│   └── prometheus_alerts.yml
├── CHANGELOG.md
├── README.md
├── CLAUDE.md               # This file
├── .clinerules             # Cline-specific rules
└── pyproject.toml
```

---

## 🛠️ Development Workflow

### 1. Understanding the Task

When given a task:
1. Read this CLAUDE.md completely
2. Check relevant source files
3. Check existing tests
4. Understand the data flow

### 2. Writing Code

Follow the patterns in `.clinerules`:
- Type hints on ALL functions
- Docstrings on public APIs
- Structured logging (structlog)
- Specific exception handling
- Async/await for I/O

### 3. Testing

```bash
# Write tests FIRST or alongside code
python -m pytest tests/test_my_feature.py -v

# Run with coverage
python -m pytest --cov=src --cov-report=term-missing

# Specific test
python -m pytest tests/test_my_feature.py::TestMyClass::test_my_method -v
```

### 4. Before Submitting

Run ALL checks:
```bash
# Format first
black src/ tests/
isort src/ tests/

# Then verify
python -m pytest -q
python -m mypy src/
bandit -r src/
pip-audit
```

---

## 📊 Quality Gates

| Check | Command | Threshold |
|-------|---------|-----------|
| Tests | `pytest -q` | 100% pass |
| Coverage | `pytest --cov` | > 75% |
| Types | `mypy src/` | No errors |
| Security | `bandit -r src/` | No HIGH/CRITICAL |
| Lint | `pylint src/` | Score > 8/10 |

---

## 🔧 Common Tasks

### Adding a New Agent

```python
# src/agents/my_agent.py
from typing import Optional
from dataclasses import dataclass
import structlog

logger = structlog.get_logger()

@dataclass
class MyAgentResult:
    success: bool
    output: Optional[str] = None
    confidence: float = 0.0

async def run_my_agent(input_data: dict) -> MyAgentResult:
    """Run my agent logic.
    
    Args:
        input_data: Input data for the agent
        
    Returns:
        MyAgentResult with output and confidence
    """
    logger.info("my_agent_started", input=input_data)
    
    try:
        # Agent logic here
        output = process(input_data)
        
        return MyAgentResult(
            success=True,
            output=output,
            confidence=0.9
        )
    except Exception as e:
        logger.exception("my_agent_failed")
        return MyAgentResult(success=False)
```

### Adding a New Endpoint

```python
# src/api.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1", tags=["my_feature"])

class MyRequest(BaseModel):
    data: str = Field(..., min_length=1, max_length=1000)

class MyResponse(BaseModel):
    status: str
    result: Optional[str] = None

@router.post("/my_endpoint", response_model=MyResponse)
async def my_endpoint(
    request: MyRequest,
    auth: AuthResult = Depends(require_auth)
) -> MyResponse:
    """My endpoint description.
    
    Args:
        request: The request payload
        auth: Authentication result
        
    Returns:
        MyResponse with status and result
    """
    try:
        result = await process(request.data)
        return MyResponse(status="success", result=result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

### Adding a Circuit Breaker

```python
from src.core.circuit_breaker import get_circuit_breaker

cb = get_circuit_breaker("my_service")

async def call_external_service():
    try:
        result = await external_call()
        await cb.record_success()
        return result
    except Exception:
        await cb.record_failure()
        raise
```

---

## 📖 Key Patterns

### Dependency Injection
```python
# Good: Pass dependencies
async def process(
    db: AsyncSession = Depends(get_db),
    cache: Redis = Depends(get_redis)
):
    ...

# Bad: Global state
global_db = None
def process():
    global_db.query()
```

### Context Managers
```python
# Async context manager
async with httpx.AsyncClient() as client:
    response = await client.get(url)

# Resource cleanup
async with pool.acquire() as conn:
    result = await conn.fetch("SELECT 1")
```

### Retry with Backoff
```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
async def call_with_retry():
    ...
```

---

## 🎨 Code Style

- **Line length:** 100 characters max
- **Indentation:** 4 spaces
- **Quotes:** Double quotes for strings, single for chars
- **Imports:** Grouped (stdlib, third-party, local), sorted alphabetically
- **Naming:** See `.clinerules` for naming conventions

---

## 📝 Documentation

Update docs when changing:
- API endpoints → `docs/api.md`
- Configuration → Environment variables in `README.md`
- Architecture → `README.md` architecture section
- Features → `CHANGELOG.md`

---

## 🐛 Debugging

### Enable debug logging:
```bash
LOG_LEVEL=DEBUG python -m src.api
```

### View structured logs:
```bash
# JSON format
tail -f logs/app.log | jq .

# Human format
tail -f logs/app.log | structlog
```

### Common Issues:

| Issue | Solution |
|-------|----------|
| `RuntimeError: Event loop is running` | Use `pytest-asyncio` fixture |
| `Connection refused` | Check Redis/Postgres running |
| `ModuleNotFoundError` | Run `pip install -e .` |
| `ImportError` | Check PYTHONPATH |

---

## 🔗 Links

- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [Pydantic Docs](https://docs.pydantic.dev/)
- [SQLModel Docs](https://sqlmodel.tiangolo.com/)
- [AsyncPG Docs](https://magicstack.github.io/asyncpg/current/)
- [Structlog Docs](https://www.structlog.org/)
- [Pytest Async](https://pytest-asyncio.readthedocs.io/)

---

**Version:** 1.0.0  
**Last Updated:** 2024-01-15
