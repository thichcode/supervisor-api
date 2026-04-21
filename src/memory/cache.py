import redis.asyncio as redis
from redis.asyncio.connection import ConnectionPool
from src.config import get_settings
import json
from typing import Optional, Set
import structlog

settings = get_settings()
logger = structlog.get_logger()


class RedisCache:
    def __init__(self):
        self._pool: Optional[ConnectionPool] = None
        self._client: Optional[redis.Redis] = None
        self._connected = False

    async def connect(self):
        self._pool = ConnectionPool.from_url(
            settings.redis_url,
            max_connections=settings.redis_pool_size,
            decode_responses=True,
        )
        self._client = redis.Redis(connection_pool=self._pool)
        try:
            await self._client.ping()
            self._connected = True
            logger.info("Redis connected successfully", pool_size=settings.redis_pool_size)
        except Exception as e:
            logger.error("Redis connection failed", error=str(e))
            self._connected = False

    async def close(self):
        if self._client:
            await self._client.close()
        if self._pool:
            await self._pool.disconnect()
        self._connected = False
        logger.info("Redis connections closed")

    async def get(self, key: str) -> Optional[str]:
        if not self._client:
            return None
        try:
            return await self._client.get(key)
        except redis.RedisError as e:
            logger.warning("Redis GET failed", key=key, error=str(e))
            return None

    async def set(self, key: str, value: str, ttl: int = 3600) -> bool:
        if not self._client:
            return False
        try:
            return await self._client.set(key, value, ex=ttl)
        except redis.RedisError as e:
            logger.warning("Redis SET failed", key=key, error=str(e))
            return False

    async def set_if_absent(self, key: str, value: str, ttl: int = 3600) -> bool:
        if not self._client:
            return False
        try:
            return bool(await self._client.set(key, value, ex=ttl, nx=True))
        except redis.RedisError as e:
            logger.warning("Redis SETNX failed", key=key, error=str(e))
            return False

    async def delete(self, key: str) -> bool:
        if not self._client:
            return False
        try:
            return await self._client.delete(key) > 0
        except redis.RedisError as e:
            logger.warning("Redis DELETE failed", key=key, error=str(e))
            return False

    async def get_json(self, key: str) -> Optional[dict]:
        data = await self.get(key)
        if data:
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                return None
        return None

    async def set_json(self, key: str, value: dict, ttl: int = 3600) -> bool:
        try:
            serialized = json.dumps(value, default=str)
            return await self.set(key, serialized, ttl)
        except (TypeError, json.JSONDecodeError) as e:
            logger.warning("Redis JSON serialize failed", error=str(e))
            return False

    async def exists(self, key: str) -> bool:
        if not self._client:
            return False
        try:
            return await self._client.exists(key) > 0
        except redis.RedisError as e:
            logger.warning("Redis EXISTS failed", key=key, error=str(e))
            return False

    async def incr(self, key: str, amount: int = 1) -> Optional[int]:
        if not self._client:
            return None
        try:
            return await self._client.incr(key, amount)
        except redis.RedisError as e:
            logger.warning("Redis INCR failed", key=key, error=str(e))
            return None

    async def expire(self, key: str, ttl: int) -> bool:
        if not self._client:
            return False
        try:
            return await self._client.expire(key, ttl)
        except redis.RedisError as e:
            logger.warning("Redis EXPIRE failed", key=key, error=str(e))
            return False

    async def get_multi(self, keys: list[str]) -> dict[str, Optional[str]]:
        if not self._client or not keys:
            return {}
        try:
            return await self._client.mget(keys)
        except redis.RedisError as e:
            logger.warning("Redis MGET failed", keys=keys, error=str(e))
            return {}

    async def sadd(self, key: str, *values: str) -> int:
        if not self._client:
            return 0
        try:
            return await self._client.sadd(key, *values)
        except redis.RedisError as e:
            logger.warning("Redis SADD failed", key=key, error=str(e))
            return 0

    async def smembers(self, key: str) -> "Set[str]":
        if not self._client:
            return set()
        try:
            result = await self._client.smembers(key)
            return set(result) if result else set()
        except redis.RedisError as e:
            logger.warning("Redis SMEMBERS failed", key=key, error=str(e))
            return set()

    async def srem(self, key: str, *values: str) -> int:
        if not self._client:
            return 0
        try:
            return await self._client.srem(key, *values)
        except redis.RedisError as e:
            logger.warning("Redis SREM failed", key=key, error=str(e))
            return 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _session_key(self, thread_id: str) -> str:
        return f"session:{thread_id}"

    def _user_key(self, user_id: str) -> str:
        return f"user:{user_id}"

    def _case_key(self, case_id: str) -> str:
        return f"case:{case_id}"


redis_cache = RedisCache()


async def get_redis() -> RedisCache:
    return redis_cache
