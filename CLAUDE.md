# CLAUDE.md - AI Coding Assistant Instructions

> This file provides instructions for AI coding assistants (Claude Code, Cline, etc.)

## Project Overview

**Project:** Multi-Agent Supervisor System  
**Purpose:** AI agent system with long-term memory for Microsoft Teams integration  
**Stack:** Python 3.10+, FastAPI, PostgreSQL, Redis, Ollama/Azure OpenAI  
**Status:** Production Ready (v1.2.0)  
**Repo:** https://github.com/thichcode/supervisor-api

---

## Critical Rules

### Security (NEVER Violate)

1. **Never commit secrets**
   ```python
   # ❌ WRONG
   api_key = "sk-1234567890"
   
   # ✅ CORRECT
   api_key = os.getenv("OPENAI_API_KEY")
   ```

2. **Validate all inputs** - Always use Pydantic for API inputs

3. **Log securely** - Never log passwords or secrets

### Error Handling (ALWAYS Implement)

```python
try:
    result = await process_request()
except ValidationError as e:
    logger.warning("validation_failed", error=str(e))
    raise HTTPException(status_code=400, detail="Invalid request")
except Exception as e:
    logger.exception("unexpected_error")
    raise HTTPException(status_code=500, detail="Internal server error")
```

### Async Patterns (MANDATORY)

```python
# ❌ Never block
time.sleep(5)
requests.get(url)

# ✅ Always async
await asyncio.sleep(5)
response = await httpx.AsyncClient().get(url)
```

---

## Code Organization

```
supervisor-api/
├── src/
│   ├── api/
│   │   ├── app.py              # FastAPI entry point
│   │   └── routers/           # API endpoints
│   ├── agents/
│   │   ├── simple_agent.py     # Unified agent (v1.2) - USE THIS
│   │   └── subagents.py        # Legacy agents (for backward compat)
│   ├── services/
│   │   ├── chat_service.py     # Chat handling
│   │   ├── feedback_service.py  # Feedback handling
│   │   ├── pattern_learning_service.py  # Pattern learning (NEW)
│   │   └── feedback_learning_worker.py # Background learning
│   ├── core/
│   │   ├── supervisor.py       # Main orchestrator
│   │   └── approval.py         # Approval system (Telegram + Power Automate)
│   ├── db/
│   │   └── models.py           # SQLAlchemy models (includes ResponsePattern)
│   └── llm/
│       └── provider.py          # Multi-provider LLM (Ollama/Azure/OpenAI)
├── tests/                      # 170 tests passing
└── README.md                   # Main documentation
```

---

## Architecture (v1.2)

### SimpleAgent Flow

```
InputPayload → _check_patterns() → Match >90%?
                                    ├── YES → Return stored answer
                                    └── NO → LLM.generate() → Return response
```

### Pattern Learning Flow

```
Approve → Store Q&A → Next question → Match >90% → Use stored answer (no LLM)
```

### Approval Flow

```
Confidence < 30% → Telegram notification → Manager approves
                                                ├── YES → Store pattern → Send to Teams
                                                └── NO → Log rejection
```

---

## Development Workflow

### Testing

```bash
# Run all tests
python -m pytest -q

# Run with coverage
python -m pytest --cov=src --cov-report=term-missing
```

### Before Submitting

```bash
python -m pytest -q
python -m mypy src/
bandit -r src/
```

---

## Quality Gates

| Check | Command | Threshold |
|-------|---------|-----------|
| Tests | `pytest -q` | 100% pass |
| Types | `mypy src/` | No errors |
| Security | `bandit -r src/` | No HIGH/CRITICAL |

---

## Common Tasks

### Adding Pattern Learning

```python
# Use PatternLearningService
from src.services.pattern_learning_service import PatternLearningService

# Store pattern after approval
await pattern_service.store_pattern(
    question=original_message,
    answer=ai_response,
    user_id=user_id,
    team_id=team_id,
)

# Check for similar patterns
result = await pattern_service.find_similar_pattern(question=text)
if result:
    pattern, similarity = result
```

### Adding SimpleAgent Answer

```python
# SimpleAgent checks patterns first, then calls LLM
answer, confidence = await simple_agent.answer(payload, memory, llm)
```

### Adding Telegram Approval

```bash
# Config
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_APPROVAL_CHAT_IDS=123456
```

---

## Key Patterns

### Dependency Injection
```python
async def process(db: AsyncSession = Depends(get_db)):
    ...
```

### Async Context Manager
```python
async with httpx.AsyncClient() as client:
    response = await client.get(url)
```

---

## Code Style

- **Line length:** 100 characters max
- **Type hints:** Required on all functions
- **Async/await:** Mandatory for I/O

---

## Debugging

```bash
LOG_LEVEL=DEBUG python -m src.api.app
```

### Common Issues

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError` | Run `pip install -e .` |
| `ImportError` | Check PYTHONPATH |

---

**Version:** 1.2.0  
**Last Updated:** 2026-04-16
