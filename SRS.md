# Multi-Agent Supervisor System with Long-term Memory

## 1. Overview

> **Implementation status note:** this SRS reflects the current repo with full feature set including real-time chat, approval system, and multi-channel support. Estimated production-readiness is **9/10**.

### 1.1 Objective
Build an AI agent system that:
- Receives requests from Microsoft Teams (via Power Automate → n8n)
- Receives direct user chat messages via REST API
- Analyzes intent and risk
- Decides: Direct response OR delegate to subagents
- Auto-detects guide requests and system queries
- Uses approval workflow for low-confidence responses
- Uses long-term memory
- Aggregates results
- Sends output via Power Automate webhook or callback

### 1.2 Use Cases
- Executive (manager/boss) queries
- Support guideline handling
- Multi-step request processing
- Context-aware conversations across sessions
- Real-time chat with users
- System information queries (user info, case status)
- Guide delivery to users

## 2. Architecture

```
Microsoft Teams
↓
Power Automate
↓
n8n Webhook
↓
Supervisor API
├── Input Normalizer & Sanitizer
├── Memory Retriever
├── Intent Classifier
├── Risk Evaluator
├── Decision Engine
│   ├── Direct Answer
│   └── Subagent Orchestration
│       ├── Context Agent
│       ├── Policy Agent (with Guide Detection)
│       ├── Knowledge Agent (with System Query Detection)
│       ├── Draft Agent
│       └── QA Agent
├── Approval Queue (confidence < 90%)
├── Aggregator
├── Memory Writer
└── Webhook/Callback Output
```

## 3. Memory System

### 3.1 Memory Types
- **Conversation Memory**: Thread-level context, rolling summary, recent messages
- **User Memory**: Role, preferences, VIP/manager flag, communication style
- **Case Memory**: Support case state, action items, SLA/ownership
- **Episodic Memory**: Learned patterns, successful responses, escalation rules

### 3.2 Memory Storage
- **Required**: Postgres (source of truth), Redis (cache/session/approvals)
- **Optional**: pgvector (semantic search), MemPalace (external memory)

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
  "confidence": 0.85,
  "risk_level": "low|medium|high",
  "metadata": {
    "intent": "",
    "agents_used": [],
    "processing_time_ms": 0
  }
}
```

### Chat API Input
```json
{
  "user_id": "string",
  "display_name": "string",
  "message": "string",
  "thread_id": "string (optional)",
  "case_id": "string (optional)",
  "message_type": "text|guideline|system_query|notification"
}
```

### Chat API Output (with Approval)
```json
{
  "request_id": "string",
  "status": "completed|pending_approval",
  "message": "string",
  "confidence": 0.85,
  "metadata": {
    "approval_id": "string (if pending)",
    "approval_required": true,
    "threshold": 0.9
  }
}
```

## 5. Core Components

### 5.1 Intent Classifier
Classifies into: faq, policy, support_case, analysis, executive_request

### 5.2 Risk Evaluator
Flags: legal, financial, vip, executive, commitment, high_priority_case

### 5.3 Guide Detection (Policy Agent)
Auto-detects guide requests from keywords:
- "hướng dẫn", "guideline", "manual", "tài liệu", "cách làm", "quy trình"

### 5.4 System Query Detection (Knowledge Agent)
Auto-detects system queries from keywords:
- "case của tôi", "trạng thái", "ai đang xử lý", "tra cứu", "thông tin"

### 5.5 Decision Rules
**Use Subagents** if:
- intent in [policy, support_case, analysis]
- OR risk >= medium
- OR multi-step request (>50 words)
- OR low confidence (<0.7)
- OR guide_requested
- OR system_query_requested

**Approval Required** if:
- confidence < 0.9 (90%)

**Human Review** if:
- executive_request
- risk = high
- confidence < 0.7
- contains commitment keywords

## 6. Processing Flow

1. Receive request (webhook or chat API)
2. Validate & sanitize input
3. Retrieve memory
4. Classify intent
5. Evaluate risk
6. Auto-detect guide requests
7. Auto-detect system queries
8. Decision: Direct OR Subagents
9. QA validation
10. Check approval threshold (90%)
    - If confidence < 90%: create approval request, return pending_approval
    - If confidence >= 90%: proceed
11. Memory commit
12. Send webhook/callback

## 7. Approval System

### 7.1 Approval Flow
```
AI generates response
      ↓
Confidence >= 90%? ──No──→ Create Approval (pending)
      ↓Yes                     ↓
Send to user           Manager reviews
                       /approvals/{id}/action
                            ↓
                    Approve → Send to user
                    Reject → Discard
```

### 7.2 Approval Endpoints
- `GET /approvals` - List all approvals
- `GET /approvals/{id}` - Get approval details
- `POST /approvals/{id}/action` - Approve or reject

### 7.3 Approval Request Schema
```json
{
  "id": "string",
  "request_id": "string",
  "user_id": "string",
  "original_message": "string",
  "ai_response": "string",
  "confidence": 0.85,
  "threshold": 0.9,
  "status": "pending|approved|rejected",
  "action_type": "send_message|deliver_guide|system_query",
  "reviewed_by": "string (nullable)",
  "review_comment": "string (nullable)"
}
```

## 8. Non-Functional Requirements

### Performance
- Direct response: < 2s
- Multi-agent: < 8s
- Chat API: < 3s

### Reliability
- Retry 1 lần với exponential backoff
- Timeout agent: 3-10s
- Connection pooling for DB & Redis
- Circuit breaker for LLM and external services

### Observability
- Prometheus metrics endpoint (`/metrics`)
- Structured JSON logging
- Health checks (`/health`, `/health/ready`)
- Audit logs for all decisions

### Security
- Input sanitization (PII masking)
- Webhook secret authentication
- Rate limiting
- CORS configuration

## 9. API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/health/ready` | Readiness check (DB, Redis, LLM) |
| GET | `/metrics` | Prometheus metrics |
| POST | `/webhook/n8n` | Receive requests from n8n |
| POST | `/output/power-automate` | Send to Power Automate |
| POST | `/chat` | Direct user chat |
| POST | `/system/query` | Query user/case info |
| POST | `/guide/deliver` | Deliver guideline to user |
| POST | `/callback/send` | Send async callback |
| GET | `/approvals` | List all approvals |
| GET | `/approvals/{id}` | Get approval details |
| POST | `/approvals/{id}/action` | Approve or reject |

## 10. Design Principles

1. Supervisor = deterministic logic (NOT LLM-driven flow)
2. Agents = task executors
3. Memory = structured + scoped
4. Always QA before output
5. Always log decisions (audit trail)
6. Supervisor is the "true brain"
7. Agents are just tools
8. **All sensitive actions require approval if confidence < 90%**

## 11. Tech Stack

- **API**: Python 3.11 + FastAPI
- **Database**: Postgres 16 + SQLAlchemy (async)
- **Cache**: Redis 7
- **LLM**: OpenAI GPT-4, Ollama, Azure OpenAI (multi-provider)
- **Orchestration**: n8n, Power Automate
- **Monitoring**: Prometheus + Grafana
- **Deployment**: Docker, Kubernetes
