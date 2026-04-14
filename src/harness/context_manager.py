"""
Context Manager - Manage agent context with compaction

Provides:
- Context injection
- Automatic compaction/summarization
- State offloading
- Message window management
"""

import json
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

import logging
from src.config import get_settings

settings = get_settings()
logger = logging.getLogger("harness.context_manager")


@dataclass
class ContextConfig:
    """Configuration for context management"""
    max_messages: int = 50
    max_tokens: int = 8000
    compaction_threshold: float = 0.8  # Compact when 80% full
    summary_model: str = "llama"
    preserve_system: bool = True
    preserve_last_n: int = 3  # Always keep last N messages


@dataclass
class CompactionRecord:
    """Record of a context compaction"""
    timestamp: datetime
    original_count: int
    compacted_count: int
    tokens_saved: int
    summary: str


class ContextManager:
    """
    Manages agent context with automatic compaction
    
    Features:
    - Inject external context into messages
    - Monitor context size
    - Auto-compact when threshold reached
    - Offload state to external storage
    """
    
    def __init__(
        self,
        config: Optional[ContextConfig] = None,
        enable_compaction: bool = True,
    ):
        self.config = config or ContextConfig()
        self.enable_compaction = enable_compaction
        self._compact_count = 0
        self._tokens_processed = 0
        self._compaction_history: List[CompactionRecord] = []
        self._offloaded_state: Dict[str, Any] = {}
        self._cache: Dict[str, str] = {}  # LRU cache for summaries
    
    def inject_context(
        self,
        context: Dict[str, Any],
        messages: Optional[List[Dict]] = None,
    ) -> Dict[str, str]:
        """Inject external context as a system message"""
        context_parts = []
        
        # Add knowledge context
        if "knowledge" in context:
            kb = context["knowledge"]
            if isinstance(kb, list):
                for item in kb[:5]:  # Limit to 5 items
                    context_parts.append(f"**Knowledge:** {item.get('content', '')}")
            elif isinstance(kb, dict):
                context_parts.append(f"**Knowledge:** {kb.get('content', str(kb))}")
        
        # Add policy context
        if "policy" in context:
            policy = context["policy"]
            if isinstance(policy, str):
                context_parts.append(f"**Policy:** {policy}")
            elif isinstance(policy, dict):
                context_parts.append(f"**Policy:** {policy.get('content', str(policy))}")
        
        # Add user context
        if "user" in context:
            user = context["user"]
            context_parts.append(f"**User Info:** {json.dumps(user, ensure_ascii=False)}")
        
        # Add tool context
        if "tools" in context:
            context_parts.append(f"**Available Tools:** {', '.join(context['tools'])}")
        
        # Add offloaded state
        if self._offloaded_state:
            offload_summary = json.dumps(self._offloaded_state, ensure_ascii=False)
            if len(offload_summary) < 2000:  # Only include if small
                context_parts.append(f"**Previous State:** {offload_summary}")
        
        if not context_parts:
            return {"role": "system", "content": ""}
        
        content = "\n\n".join(context_parts)
        return {"role": "system", "content": content}
    
    def compact(self, messages: List[Dict]) -> List[Dict]:
        """
        Compact messages to stay within context window
        
        Strategy:
        1. Identify messages to keep (system, last N)
        2. Summarize middle messages
        3. Replace with summary
        """
        if not self.enable_compaction:
            return messages
        
        # Calculate current size
        current_size = self._estimate_tokens(messages)
        max_size = self.config.max_tokens
        
        if current_size < max_size * self.config.compaction_threshold:
            return messages  # Not yet at threshold
        
        logger.info(f"Compacting context: {current_size} tokens, max: {max_size}")
        
        # Identify messages to keep
        system_messages = [m for m in messages if m.get("role") == "system"]
        recent_messages = messages[-self.config.preserve_last_n:]
        
        # Middle messages to summarize
        middle_messages = messages[len(system_messages):-self.config.preserve_last_n]
        
        if not middle_messages:
            return messages
        
        # Create summary
        summary = self._summarize_messages(middle_messages)
        
        # Build compacted messages
        compacted = system_messages.copy()
        compacted.append({
            "role": "system",
            "content": f"[Previous conversation summarized - {len(middle_messages)} messages]: {summary}",
        })
        compacted.extend(recent_messages)
        
        # Record compaction
        record = CompactionRecord(
            timestamp=datetime.now(),
            original_count=len(messages),
            compacted_count=len(compacted),
            tokens_saved=current_size - self._estimate_tokens(compacted),
            summary=summary[:200],
        )
        self._compaction_history.append(record)
        self._compact_count += 1
        
        logger.info(
            f"Context compacted: {record.original_count} -> {record.compacted_count} messages, "
            f"saved {record.tokens_saved} tokens"
        )
        
        return compacted
    
    def _estimate_tokens(self, messages: List[Dict]) -> int:
        """Estimate token count for messages"""
        # Simple estimation: ~4 chars per token
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content) // 4
            # Add overhead for role
            total += 10
        return total
    
    def _summarize_messages(self, messages: List[Dict]) -> str:
        """Summarize a list of messages"""
        # Extract key information
        tool_calls = []
        user_messages = []
        assistant_messages = []
        
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            
            if role == "tool":
                tool_calls.append(content[:100] if content else "")
            elif role == "user":
                user_messages.append(content[:150] if content else "")
            elif role == "assistant":
                assistant_messages.append(content[:150] if content else "")
        
        summary_parts = []
        
        if tool_calls:
            summary_parts.append(f"Used {len(tool_calls)} tools")
        
        if user_messages:
            summary_parts.append(f"User asked about: {user_messages[-1][:100]}")
        
        if assistant_messages:
            summary_parts.append(f"Last assistant response: {assistant_messages[-1][:100]}")
        
        if not summary_parts:
            return f"Conversation with {len(messages)} messages"
        
        return "; ".join(summary_parts)
    
    def offload_state(
        self,
        key: str,
        value: Any,
        persist: bool = False,
    ) -> None:
        """Offload state to external storage"""
        self._offloaded_state[key] = {
            "value": value,
            "timestamp": datetime.now().isoformat(),
            "persisted": persist,
        }
        
        # TODO: If persist=True, save to database
        
        logger.debug(f"State offloaded: {key}")
    
    def restore_state(self, key: str) -> Optional[Any]:
        """Restore offloaded state"""
        if key in self._offloaded_state:
            return self._offloaded_state[key]["value"]
        return None
    
    def cache_set(self, key: str, value: str, ttl: int = 3600) -> None:
        """Set value in LRU cache"""
        self._cache[key] = value
        # Limit cache size
        if len(self._cache) > 1000:
            # Remove oldest (first 100 items)
            keys_to_remove = list(self._cache.keys())[:100]
            for k in keys_to_remove:
                del self._cache[k]
    
    def cache_get(self, key: str) -> Optional[str]:
        """Get value from cache"""
        return self._cache.get(key)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get context management statistics"""
        return {
            "compact_count": self._compact_count,
            "tokens_processed": self._tokens_processed,
            "offloaded_keys": len(self._offloaded_state),
            "cache_size": len(self._cache),
            "compaction_history": [
                {
                    "timestamp": r.timestamp.isoformat(),
                    "original": r.original_count,
                    "compacted": r.compacted_count,
                    "saved": r.tokens_saved,
                }
                for r in self._compaction_history[-10:]
            ],
        }
    
    def reset(self) -> None:
        """Reset context manager state"""
        self._compact_count = 0
        self._tokens_processed = 0
        self._compaction_history.clear()
        self._offloaded_state.clear()
        self._cache.clear()
        logger.info("Context manager reset")
