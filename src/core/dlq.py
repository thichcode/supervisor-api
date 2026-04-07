"""
Dead Letter Queue (DLQ) for failed requests
Stores failed requests for retry or manual processing
"""
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Any
from dataclasses import dataclass, asdict
import structlog

logger = structlog.get_logger()


class DLQStatus(Enum):
    PENDING = "pending"
    RETRYING = "retrying"
    RESOLVED = "resolved"
    FAILED = "failed"


@dataclass
class DLQEntry:
    id: str
    original_request_id: str
    payload: dict
    error_message: str
    error_type: str
    retry_count: int
    max_retries: int
    status: str
    created_at: str
    updated_at: str
    next_retry_at: Optional[str] = None
    metadata: Optional[dict] = None


class DeadLetterQueue:
    """In-memory DLQ with optional persistence to Redis"""
    
    def __init__(self, max_retries: int = 3, retry_delay_seconds: int = 300):
        self._queue: dict[str, DLQEntry] = {}
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self._lock = None  # Will use asyncio.Lock if needed

    def add(
        self,
        request_id: str,
        payload: dict,
        error: Exception,
        metadata: Optional[dict] = None
    ) -> DLQEntry:
        """Add a failed request to the DLQ"""
        now = datetime.now(timezone.utc)
        entry = DLQEntry(
            id=str(uuid.uuid4()),
            original_request_id=request_id,
            payload=payload,
            error_message=str(error),
            error_type=type(error).__name__,
            retry_count=0,
            max_retries=self.max_retries,
            status=DLQStatus.PENDING.value,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            metadata=metadata
        )
        
        self._queue[entry.id] = entry
        
        logger.warning(
            "dlq_entry_added",
            entry_id=entry.id,
            request_id=request_id,
            error_type=entry.error_type,
            queue_size=len(self._queue)
        )
        
        return entry

    def get(self, entry_id: str) -> Optional[DLQEntry]:
        """Get a DLQ entry by ID"""
        return self._queue.get(entry_id)

    def get_pending(self) -> list[DLQEntry]:
        """Get all pending entries ready for retry"""
        now = datetime.now(timezone.utc)
        pending = []
        
        for entry in self._queue.values():
            if entry.status in [DLQStatus.PENDING.value, DLQStatus.RETRYING.value]:
                if entry.retry_count < entry.max_retries:
                    if entry.next_retry_at:
                        next_retry = datetime.fromisoformat(entry.next_retry_at)
                        if next_retry > now:
                            continue
                    pending.append(entry)
                    
        return sorted(pending, key=lambda e: e.created_at)

    def mark_retrying(self, entry_id: str) -> bool:
        """Mark entry as retrying"""
        entry = self._queue.get(entry_id)
        if not entry:
            return False
            
        entry.status = DLQStatus.RETRYING.value
        entry.updated_at = datetime.now(timezone.utc).isoformat()
        return True

    def mark_resolved(self, entry_id: str) -> bool:
        """Mark entry as successfully resolved"""
        entry = self._queue.get(entry_id)
        if not entry:
            return False
            
        entry.status = DLQStatus.RESOLVED.value
        entry.updated_at = datetime.now(timezone.utc).isoformat()
        
        logger.info(
            "dlq_entry_resolved",
            entry_id=entry_id,
            request_id=entry.original_request_id
        )
        return True

    def mark_failed(self, entry_id: str, reason: str) -> bool:
        """Mark entry as permanently failed"""
        entry = self._queue.get(entry_id)
        if not entry:
            return False
            
        entry.status = DLQStatus.FAILED.value
        entry.error_message = f"{entry.error_message}; Final failure: {reason}"
        entry.updated_at = datetime.now(timezone.utc).isoformat()
        
        logger.error(
            "dlq_entry_failed",
            entry_id=entry_id,
            request_id=entry.original_request_id,
            reason=reason
        )
        return True

    def increment_retry(self, entry_id: str) -> bool:
        """Increment retry count and schedule next retry"""
        entry = self._queue.get(entry_id)
        if not entry:
            return False
            
        entry.retry_count += 1
        
        if entry.retry_count >= entry.max_retries:
            return self.mark_failed(entry_id, f"Max retries ({self.max_retries}) exceeded")
        
        # Schedule next retry
        from datetime import timedelta
        next_retry = datetime.now(timezone.utc) + timedelta(seconds=self.retry_delay_seconds * entry.retry_count)
        entry.next_retry_at = next_retry.isoformat()
        entry.updated_at = datetime.now(timezone.utc).isoformat()
        
        logger.info(
            "dlq_retry_scheduled",
            entry_id=entry_id,
            retry_count=entry.retry_count,
            next_retry_at=entry.next_retry_at
        )
        return True

    def remove(self, entry_id: str) -> bool:
        """Remove an entry from the queue"""
        if entry_id in self._queue:
            del self._queue[entry_id]
            logger.info("dlq_entry_removed", entry_id=entry_id)
            return True
        return False

    def get_stats(self) -> dict:
        """Get DLQ statistics"""
        stats = {
            "total": len(self._queue),
            "pending": 0,
            "retrying": 0,
            "resolved": 0,
            "failed": 0,
        }
        
        for entry in self._queue.values():
            stats[entry.status] = stats.get(entry.status, 0) + 1
            
        return stats

    def get_all(self, status: Optional[str] = None) -> list[DLQEntry]:
        """Get all entries, optionally filtered by status"""
        entries = list(self._queue.values())
        if status:
            entries = [e for e in entries if e.status == status]
        return sorted(entries, key=lambda e: e.created_at, reverse=True)


# Global DLQ instance
dlq = DeadLetterQueue(max_retries=3, retry_delay_seconds=300)
