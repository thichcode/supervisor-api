# Complete Flow Diagram: Teams → Supervisor → User

## Overview Flow

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                              Microsoft Teams                                              │
│                                                                                             │
│  User sends message                                                                         │
│         │                                                                                  │
│         ↓                                                                                  │
│  ┌──────────────────┐                                                                       │
│  │ Power Automate  │ ← Trigger: "When a message is received"                              │
│  │   (Incoming)     │                                                                       │
│  └────────┬─────────┘                                                                       │
│           │                                                                                  │
│           │ Transform to JSON                                                                │
│           │                                                                                  │
│           ↓                                                                                  │
└───────────┼───────────────────────────────────────────────────────────────────────────────┘
            │
            ↓ (HTTP POST)
┌───────────┼───────────────────────────────────────────────────────────────────────────────┐
│           │                              n8n Webhook                                         │
│           │                                                                                  │
│           │  ┌──────────────┐                                                                │
│           └─→│ /webhook/n8n │ ← Webhook URL from Power Automate                              │
│              └───────┬──────┘                                                                │
│                      │                                                                       │
│                      ↓                                                                       │
└─────────────────────┼───────────────────────────────────────────────────────────────────────┘
                      │
┌─────────────────────┼───────────────────────────────────────────────────────────────────────┐
│                     │                         Supervisor API v2 (FastAPI)                  │
│                     │                                                                             │
│                     │  ┌──────────────────────────────────────────────────────────────────┐   │
│                     └──→│ 0. v2 Query Cache (LRU)                                          │   │
│                        │    - Check cache first: cache_key = user_id:query[:100]          │   │
│                        │    - TTL: 600 seconds (10 min)                                    │   │
│                        │    - If HIT → Return cached response immediately                  │   │
│                        └────┬────────────────────────────────────────────────────────────────┘   │
│                             │ (cache miss)                                                  │
│                             ↓                                                                    │
│                        ┌────┴────────────────────────────────────────────────────────────────┐   │
│                        │ 1. Input Normalizer & Sanitizer                                 │   │
│                        │    - Validate payload                                              │   │
│                        │    - Sanitize PII (email, phone, etc.)                             │   │
│                        │    - Check webhook secret                                          │   │
│                        └────┬────────────────────────────────────────────────────────────────┘   │
│                             │                                                                    │
│                             ↓                                                                    │
│                        ┌────┴────────────────────────────────────────────────────────────────┐   │
│                        │ 2. Memory Retrieval                                                │   │
│                        │    - Check Redis cache (memory:{thread_id})                         │   │
│                        │    - Query Postgres (messages, summaries, profiles)               │   │
│                        │    - Build MemoryContext                                           │   │
│                        └────┬────────────────────────────────────────────────────────────────┘   │
│                             │                                                                    │
│                             ↓                                                                    │
│                        ┌────┴────────────────────────────────────────────────────────────────┐   │
│                        │ 3. Intent Classification                                          │   │
│                        │    - FAQ, Policy, Support Case, Analysis, Executive               │   │
│                        │    - Role-based context boost (PM→Analysis, HR→Policy, IT→Case) │   │
│                        │    - 150+ keyword patterns (EN+VI)                                │   │
│                        └────┬────────────────────────────────────────────────────────────────┘   │
│                             │                                                                    │
│                             ↓                                                                    │
│                        ┌────┴────────────────────────────────────────────────────────────────┐   │
│                        │ 4. Risk Evaluation                                                │   │
│                        │    - Flags: legal, financial, vip, executive, commitment        │   │
│                        │    - Risk Level: low, medium, high                               │   │
│                        │    - 50+ keywords for outsourcing company                        │   │
│                        └────┬────────────────────────────────────────────────────────────────┘   │
│                             │                                                                    │
│                             ↓                                                                    │
│                        ┌────┴────────────────────────────────────────────────────────────────┐   │
│                        │ 5. Agent Router (v2 Enhancement)                                  │   │
│                        │    - Determine optimal agent path based on query type            │   │
│                        │    - policy→[policy], support→[context,qa],                       │   │
│                        │      general→[context,policy,knowledge,draft,qa]                 │   │
│                        └────┬────────────────────────────────────────────────────────────────┘   │
│                             │                                                                    │
│                             ↓                                                                    │
│                        ┌────┴────────────────────────────────────────────────────────────────┐   │
│                        │ 6. Decision Engine                                                │   │
│                        │                                                                       │   │
│                        │    ┌────────────────┐    ┌─────────────────────┐                 │   │
│                        │    │ Direct Answer  │    │ Subagent Pipeline  │                 │   │
│                        │    └────────┬───────┘    └──────────┬──────────┘                 │   │
│                        │             │                      │                               │   │
│                        │             │                      ↓                               │   │
│                        │             │               ┌──────────────┐                       │   │
│                        │             │               │ Context Agent│                       │   │
│                        │             │               └───────┬──────┘                       │   │
│                        │             │                       │                               │   │
│                        │             │               ┌───────┴───────┐                       │   │
│                        │             │               │               │                       │   │
│                        │             │               ↓               ↓                       │   │
│                        │             │        ┌────────────┐  ┌─────────────┐              │   │
│                        │             │        │Policy Agent│  │Knowledge    │              │   │
│                        │             │        │(Guide Det.)│  │Agent(BM25) │              │   │
│                        │             │        └────────────┘  └─────────────┘              │   │
│                        │             │               │               │                       │   │
│                        │             │               └───────┬───────┘                       │   │
│                        │             │                       │                               │   │
│                        │             │               ┌───────┴───────┐                       │   │
│                        │             │               │               │                       │   │
│                        │             │               ↓               ↓                       │   │
│                        │             │        ┌────────────┐  ┌─────────────┐              │   │
│                        │             │        │Draft Agent │  │ QAAgent     │              │   │
│                        │             │        └────────────┘  │(Bayesian)  │              │   │
│                        │             │                       └─────────────┘              │   │
│                        │             │               │               │                       │   │
│                        │             └───────────────┼───────────────┘                       │   │
│                        │                             │                                       │   │
│                        └─────────────────────────────┼───────────────────────────────────────┘   │
│                                                     │                                            │
│                                                     ↓                                            │
│                        ┌────────────────────────────┴────────────────────────────────────┐   │
│                        │ 7. Bayesian Confidence Validation (v2 Enhancement)              │   │
│                        │    - Calculate confidence using Bayesian inference               │   │
│                        │    - Factors: context_relevance, policy_match,                   │   │
│                        │      knowledge_freshness, user_satisfaction                     │   │
│                        │    - More accurate than rule-based scoring                       │   │
│                        └────┬────────────────────────────────────────────────────────────────┘   │
│                             │                                                                    │
│                             ↓                                                                    │
│                        ┌────┴────────────────────────────────────────────────────────────────┐   │
│                        │ 8. Approval Check (Confidence < 90%)                              │   │
│                        │                                                                       │   │
│                        │    ┌─────────────────────────────────────────┐                   │   │
│                        │    │ Confidence >= 90%?                        │                   │   │
│                        │    │              │                           │                   │   │
│                        │    │             Yes                          No                  │   │
│                        │    │              ↓                           ↓                  │   │
│                        │    │    ┌──────────────────┐   ┌───────────────────────┐             │   │
│                        │    │    │ Continue to     │   │ Create Approval     │             │   │
│                        │    │    │ Output          │   │ (status: pending)    │             │   │
│                        │    │    └──────────────────┘   └──────────┬──────────┘             │   │
│                        │    │                                      │                        │   │
│                        │    └──────────────────────────────────────┼────────────────────────┘   │
│                        │                                         │                            │
│                        └─────────────────────────────────────────┼────────────────────────────┘   │
│                                                                  │                              │
│                                                                  ↓                              │
│                        ┌────────────────────────────────────────┴──────────────────────────┐   │
│                        │ 9. Memory Commit                                               │   │
│                        │    - Save message to Postgres                                    │   │
│                        │    - Update conversation summary                                 │   │
│                        │    - Update user profile                                        │   │
│                        │    - Invalidate Redis cache                                      │   │
│                        │    - Log to audit                                               │   │
│                        └────────────────────────────────────────┬──────────────────────────┘   │
│                                                                 │                               │
│                                                                 ↓                               │
│                        ┌────────────────────────────────────────┴──────────────────────────┐   │
│                        │ 10. v2 Cache & Output                                           │   │
│                        │     - Cache response (confidence >= 60%)                        │   │
│                        │     - AUTO-SEND to Power Automate (v2 Enhancement)               │   │
│                        └────────────────────────────────────────┬──────────────────────────┘   │
│                                                                     │                           │
└─────────────────────────────────────────────────────────────────────┼───────────────────────────┘
                                                                      │
┌─────────────────────────────────────────────────────────────────────┼───────────────────────────┐
│                                                                     │                           │
│                                    ┌────────────────────────────────┴────────────────────────┐   │
│                                    │ Output Response (AUTO-SEND)                              │   │
│                                    │                                                           │   │
│         ┌──────────────┐          │    ┌─────────────────────────────────────────┐       │   │
│         │ Power        │←────────┤────│ POST to Power Automate Webhook           │       │   │
│         │ Automate     │          │    │ (AUTO - no manual trigger needed)        │       │   │
│         │ (Outgoing)  │          │    └─────────────────────────────────────────┘       │   │
│         └──────────────┘          │                                                           │   │
│                                   │    OR (if pending_approval):                           │   │
│                                   │    ┌─────────────────────────────────────────┐       │   │
│                                   │    │ Return to client: status=pending_approval│       │   │
│                                   │    │ + approval_id                            │       │   │
│                                   │    └─────────────────────────────────────────┘       │   │
│                                   └───────────────────────────────────────────────────────┘   │
│                                                                                                │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ↓
                         ┌────────────────────────────────────────┐
                         │        Microsoft Teams                  │
                         │                                         │
                         │  User receives message                  │
                         │  (or approval notification)              │
                         └────────────────────────────────────────┘
```

## Approval Flow (Manual Intervention)

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                              Manager Approval Flow                                            │
│                                                                                               │
│   User Message → AI Response (confidence < 90%)                                             │
│         │                                                                                     │
│         ↓                                                                                      │
│   ┌─────────────────┐                                                                          │
│   │ Approval Created│                                                                          │
│   │ (pending)       │                                                                          │
│   └────────┬────────┘                                                                          │
│            │                                                                                   │
│            ↓                                                                                   │
│   ┌─────────────────────────────────────────────────────────────────────────────┐             │
│   │ GET /approvals → Manager views pending approvals                          │             │
│   └────────────────────────────────────────┬────────────────────────────────┘             │
│                                              │                                                │
│                                              ↓                                                │
│   ┌─────────────────────────────────────────────────────────────────────────────┐             │
│   │ POST /approvals/{id}/action                                              │             │
│   │ { "action": "approve", "reviewed_by": "manager-001" }                  │             │
│   └────────────────────┬─────────────────────────────────────────────────────┘             │
│                        │                                                                    │
│              ┌─────────┴─────────┐                                                            │
│              │                    │                                                            │
│         Approve              Reject                                                           │
│              │                    │                                                            │
│              ↓                    ↓                                                            │
│   ┌──────────────────┐    ┌──────────────────┐                                                │
│   │ Send to user    │    │ Discard message  │                                                │
│   │ via webhook     │    │ Log rejection    │                                                │
│   └──────────────────┘    └──────────────────┘                                                │
│                                                                                               │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Real-time Chat Flow (Direct API)

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────────────┐     ┌──────────────┐
│  User App/   │     │  POST /chat    │     │ Supervisor Process  │     │  Response    │
│  Teams Bot   │────→│  (Direct API)  │────→│ (Same as above)     │────→│  + Approval  │
└─────────────┘     └─────────────────┘     └─────────────────────┘     └──────────────┘

If approval required:
┌─────────────┐     ┌─────────────────┐     ┌─────────────────────┐     ┌──────────────┐
│  User App   │←────│ Status: pending │     │ Wait for approval   │     │  /approvals  │
│  (waiting)  │     │ approval_id     │     │ GET/PPOST            │     │  {action}    │
└─────────────┘     └─────────────────┘     └─────────────────────┘     └──────────────┘
        │
        ↓ (after approval)
┌─────────────┐
│  User gets  │
│  message    │
└─────────────┘
```

## Guide Delivery Flow

```
User requests: "hướng dẫn", "cách làm", "quy trình"
        │
        ↓
┌─────────────────────────────┐
│ Policy Agent detects        │
│ guide_requested = true       │
└────────────┬────────────────┘
             │
             ↓
┌─────────────────────────────┐
│ Check confidence            │
│ (< 90% → approval required) │
└────────────┬────────────────┘
             │
    ┌────────┴────────┐
    │                 │
 Approval        No Approval
    │                 │
    ↓                 ↓
┌─────────┐    ┌──────────────────┐
│ Queue   │    │ POST /guide/     │
│ for      │    │ deliver (auto)    │
│ approve │    └────────┬──────────┘
    │                    │
    ↓                    ↓
┌─────────┐    ┌──────────────────┐
│ Manager │    │ Send guide to     │
│ review  │    │ user via webhook │
└────┬────┘    └──────────────────┘
     │
     ↓
┌─────────────────────────────────┐
│ /approvals/{id}/action (approve)│→ Send to user
└─────────────────────────────────┘
```

## System Query Flow

```
User asks: "case của tôi", "ai đang xử lý", "trạng thái"
        │
        ↓
┌─────────────────────────────┐
│ Knowledge Agent detects     │
│ system_query_requested = true│
│ query_type = "case_info"     │
└────────────┬────────────────┘
             │
             ↓
┌─────────────────────────────┐
│ SystemQueryAgent processes  │
│ - Query Postgres for case   │
│ - Get user profile          │
│ - Format response           │
└────────────┬────────────────┘
             │
             ↓
┌─────────────────────────────┐
│ Check confidence            │
│ (< 90% → approval required) │
└────────────┬────────────────┘
             │
             ↓
┌─────────────────────────────────────────┐
│ Return system info in response          │
│ (e.g., "Case #123: status=open,         │
│  owner=agent-001")                       │
└─────────────────────────────────────────┘
```

## Summary Table

| Trigger | Detection | Processing | Output |
|---------|-----------|------------|--------|
| Teams → Power Automate → n8n | `/webhook/n8n` | Full pipeline v2 (Cache→Router→BM25→LLM→Bayesian) | AUTO-SEND to Power Automate |
| Direct chat | `/chat` | Full pipeline v2 | AUTO-SEND to Power Automate |
| Guide request | `guide_requested` flag | Priority processing | Guide delivery |
| System query | `system_query` flag | Direct query | System info |
| Low confidence (<90%) | Approval check | Queue for approval | Pending status |

## v2 Enhancements Summary

### Supervisor v2 Modules:
1. **LRU Cache** - Query response caching (TTL: 600s)
2. **BM25 Search** - Hybrid search (BM25 70% + TF-IDF 30%)
3. **Bayesian Confidence** - Probabilistic confidence scoring
4. **Agent Router** - Dynamic agent path selection

### Auto-Send to Power Automate:
- Enabled by default for `/chat` and `/webhook/n8n` endpoints
- Configured via `POWER_AUTOMATE_WEBHOOK_URL` environment variable
- Retry 3 times with exponential backoff
- Formats response for Power Automate consumption