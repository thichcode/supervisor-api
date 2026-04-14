"""
LRU Cache wrapper for in-memory caching
Provides O(1) access with least recently used eviction
"""

from collections import OrderedDict
from typing import Any, Optional, Dict
from datetime import datetime, timedelta
import hashlib
import structlog

logger = structlog.get_logger()


class LRUCache:
    """
    Least Recently Used (LRU) Cache implementation.
    O(1) time complexity for get and put operations.
    
    Used for:
    - Policy lookups
    - Knowledge base queries
    - User profile caching
    - Conversation context
    """
    
    def __init__(self, maxsize: int = 1000, ttl_seconds: int = 3600):
        """
        Args:
            maxsize: Maximum number of items in cache
            ttl_seconds: Default time-to-live for cache entries
        """
        self.maxsize = maxsize
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._hit_count = 0
        self._miss_count = 0
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache. Updates access order."""
        if key not in self._cache:
            self._miss_count += 1
            return None
        
        # Check TTL
        entry = self._cache[key]
        if self._is_expired(entry):
            del self._cache[key]
            self._miss_count += 1
            return None
        
        # Move to end (most recently used)
        self._cache.move_to_end(key)
        self._hit_count += 1
        
        return entry["value"]
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set value in cache. Evicts oldest if full."""
        ttl = ttl or self.ttl_seconds
        
        # If key exists, update and move to end
        if key in self._cache:
            self._cache[key] = {
                "value": value,
                "expires_at": datetime.now() + timedelta(seconds=ttl),
                "created_at": datetime.now()
            }
            self._cache.move_to_end(key)
            return
        
        # Evict oldest if at capacity
        if len(self._cache) >= self.maxsize:
            self._evict_oldest()
        
        # Add new entry
        self._cache[key] = {
            "value": value,
            "expires_at": datetime.now() + timedelta(seconds=ttl),
            "created_at": datetime.now()
        }
    
    def _is_expired(self, entry: Dict[str, Any]) -> bool:
        """Check if cache entry has expired."""
        return datetime.now() > entry["expires_at"]
    
    def _evict_oldest(self) -> Optional[str]:
        """Evict the least recently used item."""
        if not self._cache:
            return None
        
        oldest_key = next(iter(self._cache))
        del self._cache[oldest_key]
        return oldest_key
    
    def delete(self, key: str) -> bool:
        """Delete a specific key from cache."""
        if key in self._cache:
            del self._cache[key]
            return True
        return False
    
    def clear(self) -> None:
        """Clear all cache entries."""
        self._cache.clear()
        self._hit_count = 0
        self._miss_count = 0
    
    def get_stats(self) -> Dict:
        """Get cache statistics."""
        total_requests = self._hit_count + self._miss_count
        hit_rate = self._hit_count / total_requests if total_requests > 0 else 0
        
        return {
            "size": len(self._cache),
            "maxsize": self.maxsize,
            "hit_count": self._hit_count,
            "miss_count": self._miss_count,
            "hit_rate": hit_rate,
            "total_requests": total_requests
        }
    
    def keys(self) -> list:
        """Get all current keys."""
        return list(self._cache.keys())
    
    def __contains__(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        return self.get(key) is not None
    
    def __len__(self) -> int:
        return len(self._cache)


class MultiTierCache:
    """
    Multi-tier caching system combining L1 (in-memory) and L2 (Redis).
    L1 is fast but limited, L2 is slower but persistent.
    
    Flow:
    1. Check L1 (LRU) first
    2. If miss, check L2 (Redis)
    3. If hit in L2, populate L1
    4. On write, write to both tiers
    """
    
    def __init__(self, l1_maxsize: int = 500, redis_cache=None):
        """
        Args:
            l1_maxsize: Maximum size of L1 in-memory cache
            redis_cache: Optional Redis cache instance (L2)
        """
        self.l1 = LRUCache(maxsize=l1_maxsize, ttl_seconds=300)  # 5 min TTL for L1
        self.redis_cache = redis_cache
    
    async def get(self, key: str, use_l2: bool = True) -> Optional[Any]:
        """Get value from cache, checking L1 then L2."""
        # Try L1 first
        value = self.l1.get(key)
        if value is not None:
            logger.debug("cache_l1_hit", key=key)
            return value
        
        # Try L2 (Redis) if enabled
        if use_l2 and self.redis_cache:
            try:
                value = await self.redis_cache.get(key)
                if value is not None:
                    # Populate L1
                    self.l1.set(key, value, ttl=300)
                    logger.debug("cache_l2_hit", key=key)
                    return value
            except Exception as e:
                logger.warning("cache_l2_error", key=key, error=str(e))
        
        logger.debug("cache_miss", key=key)
        return None
    
    async def set(self, key: str, value: Any, ttl: int = 3600, tier: str = "both") -> None:
        """
        Set value in cache.
        
        Args:
            key: Cache key
            value: Value to store
            ttl: Time-to-live in seconds
            tier: "l1", "l2", or "both"
        """
        if tier in ("l1", "both"):
            self.l1.set(key, value, ttl=min(ttl, 300))  # L1 max 5 min
        
        if tier in ("l2", "both") and self.redis_cache:
            try:
                await self.redis_cache.set(key, value, ttl=ttl)
            except Exception as e:
                logger.warning("cache_l2_set_error", key=key, error=str(e))
    
    async def delete(self, key: str, tier: str = "both") -> bool:
        """Delete key from specified tier(s)."""
        deleted = False
        
        if tier in ("l1", "both"):
            deleted = self.l1.delete(key) or deleted
        
        if tier in ("l2", "both") and self.redis_cache:
            try:
                deleted = await self.redis_cache.delete(key) or deleted
            except Exception as e:
                logger.warning("cache_l2_delete_error", key=key, error=str(e))
        
        return deleted
    
    def get_l1_stats(self) -> Dict:
        """Get L1 (in-memory) cache statistics."""
        return self.l1.get_stats()
    
    def clear_l1(self) -> None:
        """Clear L1 cache only."""
        self.l1.clear()


class PolicyCache:
    """
    Specialized cache for policy lookups.
    Uses content-based hashing for efficient invalidation.
    """
    
    def __init__(self, maxsize: int = 200):
        self.cache = LRUCache(maxsize=maxsize, ttl_seconds=1800)  # 30 min
    
    def _make_key(self, query: str, policy_type: str) -> str:
        """Create cache key from query and policy type."""
        content = f"{query}:{policy_type}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def get_policy(self, query: str, policy_type: str) -> Optional[list]:
        """Get cached policy results."""
        key = self._make_key(query, policy_type)
        return self.cache.get(key)
    
    def set_policy(self, query: str, policy_type: str, results: list) -> None:
        """Cache policy search results."""
        key = self._make_key(query, policy_type)
        self.cache.set(key, results)
    
    def get_stats(self) -> Dict:
        """Get cache statistics."""
        return self.cache.get_stats()
    
    def invalidate(self, policy_id: str = None) -> None:
        """Invalidate cache. If policy_id provided, invalidate that specific policy."""
        if policy_id:
            # Selective invalidation would require tracking policy -> key mapping
            # For now, clear all (simple but effective)
            pass
        self.cache.clear()


class KnowledgeCache:
    """
    Specialized cache for knowledge base queries.
    Includes similarity-based lookup.
    """
    
    def __init__(self, maxsize: int = 500):
        self.cache = LRUCache(maxsize=maxsize, ttl_seconds=3600)  # 1 hour
    
    def _make_key(self, query: str, kb_type: str) -> str:
        """Create cache key."""
        content = f"{query}:{kb_type}"
        return hashlib.md5(content.encode()).hexdigest()
    
    def get_results(self, query: str, kb_type: str) -> Optional[list]:
        """Get cached knowledge base results."""
        key = self._make_key(query, kb_type)
        return self.cache.get(key)
    
    def set_results(self, query: str, kb_type: str, results: list) -> None:
        """Cache knowledge base search results."""
        key = self._make_key(query, kb_type)
        self.cache.set(key, results)
    
    def get_stats(self) -> Dict:
        """Get cache statistics."""
        return self.cache.get_stats()


# Example usage
if __name__ == "__main__":
    print("=== LRU Cache Demo ===\n")
    
    # Basic LRU test
    cache = LRUCache(maxsize=3)
    
    cache.set("a", "policy_a")
    cache.set("b", "policy_b") 
    cache.set("c", "policy_c")
    
    print(f"Initial keys: {cache.keys()}")
    
    # Access 'a' to make it recently used
    print(f"Get 'a': {cache.get('a')}")
    
    # This should evict 'b' (least recently used)
    cache.set("d", "policy_d")
    
    print(f"After adding 'd': {cache.keys()}")
    print(f"Get 'b': {cache.get('b')}")  # Should be None (evicted)
    
    # Stats
    print(f"\nCache stats: {cache.get_stats()}")
    
    # Multi-tier demo (without Redis)
    print("\n=== Multi-Tier Cache Demo ===\n")
    multi = MultiTierCache(l1_maxsize=5)
    
    # Simulate get (will miss L1, skip L2 since no Redis)
    result = multi.get("test_key")
    print(f"Get 'test_key': {result}")
    
    # Set value
    multi.set("test_key", {"data": "test"}, tier="l1")
    print(f"Set 'test_key': {multi.get('test_key')}")
    
    print(f"\nL1 stats: {multi.get_l1_stats()}")