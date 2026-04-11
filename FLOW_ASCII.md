# Supervisor API - Complete Flow (ASCII)

## Main Flow: Teams → Supervisor → User

```
┌──────────────┐     ┌───────────────┐     ┌──────────────┐     ┌──────────────┐
│   Teams      │────→│ Power         │────→│    n8n       │────→│  Supervisor  │
│   User       │     │ Automate      │     │  Webhook    │     │    API       │
└──────────────┘     └───────────────┘     └──────────────┘     └──────┬───────┘
                                                                            │
                          ┌─────────────────────────────────────────────┘
                          │
                          ▼
                ┌─────────────────────┐
                │ 1. Sanitize Input    │ (PII masking, validation)
                └──────────┬────────────┘
                           │
                           ▼
                ┌─────────────────────┐
                │ 2. Memory Retrieval │ (Redis + Postgres)
                └──────────┬────────────┘
                           │
                           ▼
                ┌─────────────────────┐
                 │ 3. Intent Classify   │ (FAQ/Policy/Case/Analysis/Executive)
                 └──────────┬────────────┘
                            │
                            ▼
                 ┌─────────────────────┐
                 │ 4. Risk Evaluation  │ (legal/financial/vip/executive)
                 └──────────┬────────────┘
                            │
                            ▼
                 ┌─────────────────────┐
                 │ 5. Model Selection  │ (llama3 for all intents)
                 └──────────┬────────────┘
                            │
                            ▼
                 ┌─────────────────────┐
                 │ 6. Decision Engine  │
                 └──────────┬────────────┘
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
     ┌───────────────┐       ┌───────────────────┐
     │ Direct Answer │       │ Subagent Pipeline │
     └───────────────┘       └─────────┬─────────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    │                    │                    │
                    ▼                    ▼                    ▼
             ┌────────────┐        ┌────────────┐       ┌────────────┐
             │ Context   │        │ Policy     │       │ Knowledge  │
             │ Agent     │        │ Agent      │       │ Agent      │
             └────────────┘        └──────┬─────┘       └──────┬─────┘
                                          │                    │
                                          │ (Guide detect)    │ (Query detect)
                                          └────────┬───────────┘
                                                   │
                                                   ▼
                                          ┌────────────┐
                                          │ Draft + QA │
                                          └──────┬─────┘
                                                 │
                                                 ▼
                 ┌──────────────────────────────┴──────────────────────────┐
                 │ 7. Approval Check (confidence < 90%)                    │
                └──────────────────────────────┬──────────────────────────┘
                                               │
                          ┌──────────────────────┴──────────────────────┐
                          │                                               │
                          ▼                                               ▼
               ┌─────────────────────┐                        ┌─────────────────────┐
               │ Confidence >= 90%   │                        │ Confidence < 90%    │
               │ Continue           │                        │ Create Approval     │
               └────────┬────────────┘                        └──────────┬──────────┘
                        │                                         │
                        ▼                                         ▼
               ┌─────────────────────┐                        ┌─────────────────────┐
               │ 7. Memory Commit    │                        │ Return pending_     │
               └────────┬────────────┘                        │ approval status     │
                        │                                    └──────────┬──────────┘
                        ▼                                             │
               ┌─────────────────────┐                                  ▼
               │ 8. Send Webhook     │◄────────────────── Manager Review
               │ to Power Automate   │                    /approvals/{id}/action
               └─────────┬───────────┘                    (approve/reject)
                         │
                         ▼
               ┌─────────────────────┐
               │   Teams User        │
               │   receives message  │
               └─────────────────────┘
```

## Approval Flow

```
User message ──→ AI Response (85%) ──→ Create Approval (pending)
                                              │
                                              ▼
                                     ┌─────────────────┐
                                     │ GET /approvals │
                                     │ (Manager view) │
                                     └────────┬────────┘
                                              │
                                              ▼
                            ┌─────────────────────────────────┐
                            │ POST /approvals/{id}/action    │
                            │ { "action": "approve",         │
                            │   "reviewed_by": "manager" }  │
                            └────────────────┬────────────────┘
                                             │
                      ┌──────────────────────┴──────────────────────┐
                      │                                                 │
                      ▼                                                 ▼
            ┌─────────────────────┐                        ┌─────────────────────┐
            │ APPROVE             │                        │ REJECT              │
            │ → Send to user     │                        │ → Discard           │
            └─────────────────────┘                        └─────────────────────┘
```

## Auto-Detection Flows

### Guide Detection
```
User: "cho tôi xem hướng dẫn cách làm..."
         │
         ▼
Policy Agent detects: guide_requested = true
         │
         ▼
Extract guide_id/title from LLM
         │
         ▼
Check confidence → Send immediately or queue for approval
         │
         ▼
Response includes: "📖 Hướng dẫn: ..."
```

### System Query Detection
```
User: "case của tôi đang ở đâu?"
         │
         ▼
Knowledge Agent detects: system_query_requested = true
         │
         ▼
Query Postgres: get_case_memory(case_id)
         │
         ▼
Format system info response
         │
         ▼
Response: "📊 Case #123: status=open, owner=agent-001"
```

## API Endpoints Summary

| Endpoint | Purpose | Auth |
|----------|---------|------|
| `POST /webhook/n8n` | Teams → n8n input | Webhook secret |
| `POST /chat` | Direct chat | Optional |
| `POST /system/query` | Query info | Optional |
| `POST /guide/deliver` | Send guide | Optional |
| `GET /approvals` | List approvals | Required |
| `POST /approvals/{id}/action` | Approve/Reject | Required |
| `POST /callback/send` | Async callback | Optional |

## Key Metrics

- **Direct response**: < 2s
- **Multi-agent**: < 8s
- **Approval threshold**: 90% confidence
- **Memory TTL**: 24h (conversation), 7d (summary)
- **Rate limit**: 100 req/60s