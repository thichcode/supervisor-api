"""
Hindsight Agent Memory Service for supervisor-api.

Integrates Hindsight (https://github.com/vectorize-io/hindsight) as an additional
memory layer alongside the existing KB system.

Hindsight provides:
- retain: Store agent experiences and user interactions
- recall: Search memories with hybrid semantic + BM25 + graph search
- reflect: Generate insights from memories

KBs are still used for technical answers, Hindsight supplements with:
- Conversation context (who is this user, what did they ask before)
- User preferences learned over time
- Entity relationships
"""

from typing import Optional
import os
import structlog

try:
    from hindsight_client import Hindsight
    HINDSIGHT_AVAILABLE = True
except ImportError:
    HINDSIGHT_AVAILABLE = False

from src.config import get_settings

settings = get_settings()
logger = structlog.get_logger(__name__)


class HindsightService:
    """
    Hindsight memory service wrapper for supervisor-api.
    
    Hindsight provides:
    - retain: Store agent experiences and user interactions
    - recall: Search memories with hybrid semantic + BM25 + graph search
    - reflect: Generate insights from memories
    
    KBs are still used for technical answers, Hindsight supplements with:
    - Conversation context (who is this user, what did they ask before)
    - User preferences learned over time
    - Entity relationships
    
    Usage:
        from src.memory.hindsight_service import get_hindsight_service
        
        hindsight = get_hindsight_service()
        if hindsight.enabled:
            # Store
            await hindsight.retain(
                content="Anh Son reported VPN issues",
                metadata={"user": "anh-son", "type": "incident"}
            )
            # Recall
            results = await hindsight.recall("VPN issues")
            # Enrich response
    """
    
    def __init__(
        self,
        base_url: str = "http://localhost:8888",
        bank_id: str = "supervisor-api",
        enabled: bool = True,
        timeout: int = 30,
    ):
        self.base_url = base_url
        self.bank_id = bank_id
        self.enabled = enabled and HINDSIGHT_AVAILABLE
        self.timeout = timeout
        self._client: Optional[Hindsight] = None
        
        if self.enabled:
            self._check_connection()
    
    def _check_connection(self) -> None:
        """Check if Hindsight API is available."""
        if not HINDSIGHT_AVAILABLE:
            logger.warning("hindsight_client not installed, Hindsight disabled")
            self.enabled = False
            return
        
        try:
            import httpx
            response = httpx.get(f"{self.base_url}/health", timeout=5)
            if response.status_code == 200:
                logger.info(f"Hindsight connected at {self.base_url}")
            else:
                logger.warning(f"Hindsight health check failed: {response.status_code}")
                self.enabled = False
        except Exception as e:
            logger.warning(f"Hindsight not available: {e}")
            self.enabled = False
    
    @property
    def client(self) -> Optional[Hindsight]:
        """Lazy initialization of Hindsight client."""
        if not self.enabled:
            return None
        
        if self._client is None:
            self._client = Hindsight(base_url=self.base_url)
        
        return self._client
    
    async def retain(
        self,
        content: str,
        metadata: Optional[dict] = None,
    ) -> bool:
        """
        Store a memory in Hindsight.
        
        Args:
            content: The text content to remember
            metadata: Optional metadata (user_id, conversation_id, etc.)
        
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled or not self.client:
            return False
        
        try:
            import asyncio
            # hindsight_client calls are blocking, run in executor
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.client.retain(
                    bank_id=self.bank_id,
                    content=content,
                    metadata=metadata or {},
                )
            )
            logger.debug(f"Hindsight retain: {content[:50]}...")
            return True
        except Exception as e:
            logger.error(f"Hindsight retain failed: {e}")
            return False
    
    async def recall(
        self,
        query: str,
        limit: int = 5,
    ) -> list[dict]:
        """
        Search memories in Hindsight.
        
        Args:
            query: The search query
            limit: Maximum number of results
        
        Returns:
            List of memory results with content and metadata
        """
        if not self.enabled or not self.client:
            return []
        
        try:
            import asyncio
            # hindsight_client calls are blocking, run in executor
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                lambda: self.client.recall(
                    bank_id=self.bank_id,
                    query=query,
                    limit=limit,
                )
            )
            logger.debug(f"Hindsight recall: '{query}' -> {len(results)} results")
            return [
                {
                    "content": r.get("content", ""),
                    "metadata": r.get("metadata", {}),
                    "score": r.get("score", 0),
                }
                for r in results
            ]
        except Exception as e:
            logger.error(f"Hindsight recall failed: {e}")
            return []
    
    async def reflect(
        self,
        query: str,
    ) -> Optional[str]:
        """
        Generate insights from memories.
        
        Args:
            query: The question to reflect on
            
        Returns:
            Reflected response or None
        """
        if not self.enabled or not self.client:
            return None
        
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self.client.reflect(
                    bank_id=self.bank_id,
                    query=query,
                )
            )
            logger.debug(f"Hindsight reflect: '{query}'")
            return result.get("content") if result else None
        except Exception as e:
            logger.error(f"Hindsight reflect failed: {e}")
            return None


def get_hindsight_service() -> HindsightService:
    """Factory function to get Hindsight service instance."""
    # Read from environment
    base_url = os.environ.get("HINDSIGHT_BASE_URL", "http://hindsight:8000")
    bank_id = os.environ.get("HINDSIGHT_BANK_ID", "supervisor-api")
    enabled = os.environ.get("HINDSIGHT_ENABLED", "true").lower() == "true"
    
    return HindsightService(
        base_url=base_url,
        bank_id=bank_id,
        enabled=enabled,
    )