# Logic Analysis: Thread-Based Information Aggregation

## Overview
This document explains how the Multi-Agent Supervisor System aggregates information within the same `thread_id` to maintain conversational context and provide coherent responses.

## Core Mechanism

The system uses `thread_id` as the primary key for aggregating conversation context across multiple interactions. When processing any request, the system extracts the `thread_id` from the payload and uses it to retrieve, aggregate, and store all relevant information for that conversation thread.

## Detailed Implementation

### 1. Memory Retrieval Process (`MemoryService.retrieve()`)

When a request comes in:
- Extract `thread_id` from `payload.conversation.thread_id`
- Check Redis cache first using key pattern `memory:{thread_id}`
- On cache miss, retrieve from PostgreSQL database:
  - Conversation summary: `get_conversation_summary(thread_id)`
  - Recent messages: `get_recent_messages(thread_id, limit=10)` 
  - User profile: `get_user_profile(user_id)` (user_id from payload)
  - Case memory: `get_case_memory(case_id)` (if case exists in payload)
  - Episodic memory: Global scope items (not thread-specific)
  - External memory: From configured providers (MemPalace/file), scoped by thread_id where applicable

### 2. Data Models and Storage

All persistent storage is designed around thread_id scoping:

**Message Table** (`src/db/models.py`):
- `thread_id` column with index: `idx_messages_thread_created`
- Stores every message in the conversation thread
- Used for retrieving recent message history

**ConversationSummary Table**:
- `conversation_id` (maps to thread_id) as unique index
- Stores summarized conversation state
- Contains `unresolved_points` as JSON array

**UserProfile Table**:
- Scoped by `user_id` (not thread_id, but retrieved per thread)
- Persistent user information across threads

**CaseMemory Table**:
- Scoped by `case_id` 
- Case-specific information that may span multiple threads

**MemoryItem Table**:
- For episodic/external memory
- Scoped by `memory_scope` and `scope_id`
- Global scope for learned patterns, thread-scoped for external memories

### 3. MemoryContext Object

The aggregated information is structured as a `MemoryContext` object containing:

- `conversation_summary`: Long-term thread summary
- `recent_messages`: Chronologically ordered last 10 messages
- `user_profile`: Persistent user attributes
- `case_memory`: Case status and details (if applicable)
- `episodic_memory`: Learned patterns with confidence scores
- `external_memory`: Information from external knowledge sources

### 4. Caching Layer

- **Redis Cache**: Key pattern `memory:{thread_id}`
- **TTL**: Configured via `settings.memory_conversation_ttl`
- **Invalidation**: Cleared on each `commit()` operation
- **Fallback**: Database retrieval when cache misses

### 5. Storage Process (`MemoryService.commit()`)

After processing:
- Save new inbound message: `save_message()` with thread_id
- Update case memory if state changed: `upsert_case_memory()`
- Update conversation summary if new context: `upsert_conversation_summary()`
- Store reusable insights: `add_memory_item()` (episodic scope)
- Store user preferences: `upsert_user_profile()`
- Write to external memory providers
- Delete cache entry to force refresh on next retrieval

### 6. Supervisor Integration

The `Supervisor.process()` method:
1. Receives `MemoryContext` from memory service
2. Passes it to:
   - Intent classifier (`_classify_intent`)
   - Risk evaluator (`_evaluate_risk`)
   - Subagents (Context, Policy, Knowledge, Draft, QA)
3. Uses context in LLM prompts for response generation
4. Returns result while triggering memory updates via commit()

### 7. Key Design Benefits

**Thread Isolation**: Each `thread_id` maintains completely separate context preventing cross-contamination between conversations.

**Hierarchical Memory**: 
- Short-term: Recent messages (immediate context)
- Medium-term: Conversation summary (thread state)
- Long-term: User/profile/case/external memory (persistent knowledge)

**Performance Optimization**:
- Redis caching reduces database load
- Indexed database queries for fast retrieval
- Selective memory updates minimize write operations

**Extensibility**:
- External memory providers can leverage thread_id scoping
- Memory structure easily extensible with new context types
- Cache TTL configurable per deployment environment

**Consistency Guarantees**:
- All memory operations atomic within session
- Cache/database synchronization on commit
- Thread-scoped operations prevent data leakage

## Data Flow Summary

1. **Request Arrival**: Extract `thread_id` from payload
2. **Memory Retrieval**: 
   - Check Redis cache (`memory:{thread_id}`)
   - Fallback to DB queries scoped by thread_id
   - Construct `MemoryContext` object
3. **Processing**: 
   - Supervisor uses context for classification/routing
   - Subagents access relevant context portions
   - LLM generation incorporates full context
4. **Storage**:
   - Save new message to DB with thread_id
   - Update derived structures (summary, profiles, etc.)
   - Write to external knowledge bases
   - Invalidate Redis cache (`memory:{thread_id}`)
5. **Next Request**: Repeat from step 1

This architecture ensures that every AI agent interaction has access to the complete historical context of its conversation thread while maintaining performance through intelligent caching and database indexing."\n\n## Recently Implemented Improvement\n\n- **Optimized get_recent_messages query**: Changed from sorting DESC then reversing in Python to sorting ASC directly in SQL, eliminating unnecessary list reversal operation." 
