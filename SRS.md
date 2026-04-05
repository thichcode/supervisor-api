# Multi-Agent Supervisor System with Long-term Memory

## 1. Overview

> **Implementation status note:** this SRS now reflects the current repo more closely after multiple stabilization fixes. The system is in a stronger staging-ready state, with packaging fixed, tests passing, and several production-hardening improvements completed. Estimated current production-readiness is **8/10**, not final production yet.

### 1.1 Objective
Build an AI agent system that:
- Receives requests from Microsoft Teams (via Power Automate → n8n)
- Analyzes intent and risk
- Decides: Direct response OR delegate to subagents
- Uses long-term memory
- Aggregates results
- Sends output via Power Automate webhook

### 1.2 Use Cases
- Executive (manager/boss) queries
- Support guideline handling
- Multi-step request processing
- Context-aware conversations across sessions

## 2. Architecture

```
Microsoft Teams
↓
Power Automate
↓
n8n Webhook
↓
Supervisor API
├── Input Normalizer
├── Memory Retriever
├── Intent Classifier
├── Risk Evaluator
├── Decision Engine
│   ├── Direct Answer
│   └── Subagent Orchestration
│       ├── Context Agent
│       ├── Policy Agent
│       ├── Knowledge Agent
│       ├── Draft Agent
│       └── QA Agent
├── Aggregator
├── Memory Writer
└── Webhook Output
```

## 3. Memory System

### 3.1 Memory Types
- **Conversation Memory**: Thread-level context, rolling summary, recent messages
- **User Memory**: Role, preferences, VIP/manager flag, communication style
- **Case Memory**: Support case state, action items, SLA/ownership
- **Episodic Memory**: Learned patterns, successful responses, escalation rules

### 3.2 Memory Storage
- **Required**: Postgres (source of truth), Redis (cache/session)
- **Optional**: pgvector (semantic search)

### 3.3 Core Tables
- `messages`: id, request_id, user_id, thread_id, message_text, direction, created_at
- `conversation_summaries`: conversation_id, summary_text, unresolved_points, updated_at
- `user_profiles`: user_id, role, team, preferences, vip_flag
- `case_memory`: case_id, status, owner, summary, open_items
- `memory_items`: memory_scope, scope_id, content, embedding, confidence_score, ttl_at
- `audit_logs`: request_id, decision, risk_level, agents_used

## 4. Input/Output Schemas

### Input (n8n → Supervisor)
```json
{
  "request_id": "string",
  "source": "ms_teams",
  "timestamp": "ISO",
  "user": {"id": "string", "display_name": "string"},
  "conversation": {"thread_id": "string", "message_id": "string"},
  "case": {"case_id": "string", "priority": "low|medium|high"},
  "message": {"text": "string"}
}
```

### Output (→ Power Automate)
```json
{
  "request_id": "string",
  "status": "completed|needs_review",
  "answer": "string",
  "confidence": 0.0,
  "risk_level": "low|medium|high",
  "metadata": {
    "intent": "",
    "agents_used": [],
    "processing_time_ms": 0
  }
}
```

## 5. Core Components

### 5.1 Intent Classifier
Classifies into: faq, policy, support_case, analysis, executive_request

### 5.2 Risk Evaluator
Flags: legal, financial, vip, executive, commitment, high_priority_case

### 5.3 Decision Rules
**Use Subagents** if:
- intent in [policy, support_case, analysis]
- OR risk >= medium
- OR multi-step request (>50 words)
- OR low confidence (<0.7)

**Human Review** if:
- executive_request
- risk = high
- confidence < 0.7
- contains commitment keywords

## 6. Processing Flow

1. Receive request
2. Validate & sanitize input
3. Retrieve memory
4. Classify intent
5. Evaluate risk
6. Decision: Direct OR Subagents
7. Aggregate result
8. QA validation
9. Memory commit
10. Send webhook

## 7. Non-Functional Requirements

### Performance
- Direct response: < 2s
- Multi-agent: < 8s

### Reliability
- Retry 1 lần với exponential backoff
- Timeout agent: 3-10s
- Connection pooling for DB & Redis
- Build/package install must work via `pip install .`
- Readiness should validate DB, Redis, and LLM client availability

### Observability
- Prometheus metrics endpoint
- Structured JSON logging
- Audit logs for all decisions

### Security
- Input sanitization (PII masking)
- Webhook secret authentication
- Rate limiting
- Restrict CORS in non-debug environments

## 8.1 Current Hardening Improvements

- Fixed Python packaging/build with Hatch
- Fixed test configuration and async test compatibility
- Fixed runtime import/config blockers
- Added dynamic confidence propagation in supervisor output
- Added LLM client health-check support
- Switched schema defaults toward timezone-aware datetime handling
- Extended unit tests for supervisor direct path and LLM readiness behavior

## 8.2 Remaining Gaps Before 9/10 Production Readiness

- Integration tests for Postgres/Redis/API runtime
- End-to-end deployment validation on Docker/Kubernetes
- Unified config source of truth between `config/config.yaml` and `src/config.py`
- More resilient DB/Redis/LLM failure-path handling and verification
- Better Windows-compatible developer tooling in `Makefile`

## 8. Tech Stack

- **API**: Python 3.11 + FastAPI
- **Database**: Postgres 16 + SQLAlchemy (async)
- **Cache**: Redis 7
- **LLM**: OpenAI GPT-4
- **Orchestration**: n8n, Power Automate
- **Monitoring**: Prometheus + Grafana
- **Deployment**: Docker, Kubernetes

## 9. Design Principles

1. Supervisor = deterministic logic (NOT LLM-driven flow)
2. Agents = task executors
3. Memory = structured + scoped
4. Always QA before output
5. Always log decisions (audit trail)
6. Supervisor is the "true brain"
7. Agents are just tools

## 10. MVP Scope

### Included
- Supervisor core
- Basic memory system
- Context, Draft, QA agents
- Intent classification
- Risk evaluation
- Prometheus metrics

### Excluded (Future)
- Complex orchestration
- Auto-learning
- Semantic search (pgvector)
- Feedback learning
- Agent ranking
- Cost optimization

## 11. API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/health/ready` | Readiness check (DB, Redis, LLM) |
| GET | `/metrics` | Prometheus metrics |
| POST | `/webhook/n8n` | Receive requests |
| POST | `/output/power-automate` | Send to Power Automate |
