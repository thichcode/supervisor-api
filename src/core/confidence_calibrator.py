"""
Confidence Calibrator - Learn from historical accuracy to calibrate confidence scores.

Tracks historical accuracy per query_type, user_id, and model_name.
Applies calibration factor to raw confidence scores.
Provides trending data for monitoring accuracy over time.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import json
import structlog

logger = structlog.get_logger(__name__)


class CalibrationStats:
    """Per-category accuracy tracking with decay."""
    
    def __init__(self):
        self.total = 0
        self.correct = 0
        self.incorrect = 0
        self.recent_accuracy: list[float] = []  # last 100 accuracy samples
        self.last_updated: Optional[datetime] = None
    
    @property
    def accuracy(self) -> float:
        if self.total == 0:
            return 0.5  # Default: no data yet
        return self.correct / self.total
    
    def record_outcome(self, is_correct: bool) -> None:
        self.total += 1
        if is_correct:
            self.correct += 1
        else:
            self.incorrect += 1
        self.recent_accuracy.append(1.0 if is_correct else 0.0)
        if len(self.recent_accuracy) > 100:
            self.recent_accuracy.pop(0)
        self.last_updated = datetime.now(timezone.utc)
    
    @property
    def recent_accuracy_value(self) -> float:
        if not self.recent_accuracy:
            return 0.5
        return sum(self.recent_accuracy) / len(self.recent_accuracy)
    
    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "correct": self.correct,
            "incorrect": self.incorrect,
            "accuracy": round(self.accuracy, 4),
            "recent_accuracy": round(self.recent_accuracy_value, 4),
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }


class ConfidenceCalibrator:
    """Calibrate confidence scores based on historical feedback.
    
    How it works:
    1. Track accuracy per query_type (faq, policy, support_case, etc.)
    2. Track accuracy per user_id (some users may be harder to satisfy)
    3. Track accuracy per model_name (some models may be less reliable)
    4. Track accuracy per tool_name (for tool success rate)
    5. Apply calibration factor: calibrated = raw * calibration_factor
    
         The calibration factor is bounded [0.5, 1.5] to allow both penalty and boost.
         - If historical accuracy > 0.5 → factor > 1.0 (boost)
         - If historical accuracy < 0.5 → factor < 1.0 (penalty)
         - If historical accuracy = 0.5 → factor = 1.0 (no change)
    """
    
    def __init__(self):
        self._stats: dict[str, CalibrationStats] = {}
        self._user_stats: dict[str, CalibrationStats] = {}
        self._model_stats: dict[str, CalibrationStats] = {}
        self._tool_stats: dict[str, CalibrationStats] = {}  # NEW: tool success rate tracking
        self._decay_half_life_days = 30  # Exponential decay weight for old data
        
    def _get_or_create(self, store: dict, key: str) -> CalibrationStats:
        if key not in store:
            store[key] = CalibrationStats()
        return store[key]
    
    def record_feedback(
        self,
        query_type: str,
        user_id: Optional[str],
        model_name: Optional[str],
        raw_confidence: float,
        is_positive: bool,
    ) -> None:
        """Record a feedback outcome to update calibration stats.
        
        Args:
            query_type: Type of query (faq, policy, support_case, etc.)
            user_id: User who gave feedback (optional)
            model_name: Model that generated the response (optional)
            raw_confidence: The confidence score at time of response
            is_positive: Whether the feedback was positive (thumbs up / approved)
        """
        # Update per-query-type stats
        stats = self._get_or_create(self._stats, query_type if query_type else "unknown")
        stats.record_outcome(is_positive)
        
        # Update per-user stats
        if user_id:
            user_stats = self._get_or_create(self._user_stats, user_id)
            user_stats.record_outcome(is_positive)
        
        # Update per-model stats
        if model_name:
            model_stats = self._get_or_create(self._model_stats, model_name)
            model_stats.record_outcome(is_positive)
        
        logger.debug(
            "confidence_calibration_recorded",
            query_type=query_type,
            is_positive=is_positive,
            type_accuracy=stats.recent_accuracy_value,
        )
    
    def record_tool_outcome(self, tool_name: str, success: bool) -> None:
        """Record tool execution outcome for success rate tracking.
        
        Args:
            tool_name: Name of the tool (e.g., 'web_search', 'n8n', 'read_file')
            success: Whether the tool execution was successful
        """
        tool_stats = self._get_or_create(self._tool_stats, tool_name)
        tool_stats.record_outcome(success)
        
        logger.debug(
            "tool_success_rate_recorded",
            tool_name=tool_name,
            success=success,
            tool_accuracy=tool_stats.recent_accuracy_value,
        )
    
    def get_tool_success_rate(self, tool_name: str) -> float:
        """Get success rate for a specific tool."""
        if tool_name not in self._tool_stats:
            return 0.5  # Default: no data yet
        return self._tool_stats[tool_name].recent_accuracy_value
    
    def calibrate(self, raw_confidence: float, query_type: str = "unknown", 
                  user_id: Optional[str] = None, model_name: Optional[str] = None) -> float:
        """Calibrate a raw confidence score using historical accuracy.
        
        Formula:
        - Get accuracy for query_type (type_acc)
        - Get accuracy for user (user_acc) — if available
        - Get accuracy for model (model_acc) — if available
        - Blend: calibration_factor = 0.5 * type_acc + 0.3 * user_acc + 0.2 * model_acc
        - Actually, if no user/model data, fall back to type_acc only
        - calibrated = raw_confidence * calibration_factor
        
        The calibration factor is bounded [0.5, 1.0].
        
        Args:
            raw_confidence: Original confidence score (0.0-1.0)
            query_type: Query type for per-type calibration
            user_id: User ID for per-user calibration
            model_name: Model name for per-model calibration
            
        Returns:
            Calibrated confidence score (0.0-1.0)
        """
        # Get individual accuracy scores
        type_acc = self._get_or_create(self._stats, query_type if query_type else "unknown").recent_accuracy_value
        user_acc = self._get_or_create(self._user_stats, user_id).recent_accuracy_value if user_id else None
        model_acc = self._get_or_create(self._model_stats, model_name).recent_accuracy_value if model_name else None
        
        # Blend calibration factor
        if user_acc is not None and model_acc is not None:
            calibration_factor = 0.5 * type_acc + 0.3 * user_acc + 0.2 * model_acc
        elif user_acc is not None:
            calibration_factor = 0.6 * type_acc + 0.4 * user_acc
        elif model_acc is not None:
            calibration_factor = 0.7 * type_acc + 0.3 * model_acc
        else:
            calibration_factor = type_acc
        
        # Bounds: centered around 1.0 so calibration can both increase and decrease
        # If historical accuracy > 0.5 → factor > 1.0 (boost)
        # If historical accuracy < 0.5 → factor < 1.0 (penalty)
        # If historical accuracy = 0.5 → factor = 1.0 (no change)
        calibration_factor = max(0.5, min(1.5, calibration_factor))
        
        # Apply calibration (if factor is 1.0, no change)
        calibrated = raw_confidence * calibration_factor
        
        logger.debug(
            "confidence_calibrated",
            raw=raw_confidence,
            calibrated=round(calibrated, 4),
            calibration_factor=round(calibration_factor, 4),
            type_acc=round(type_acc, 4),
            user_acc=round(user_acc, 4) if user_acc else None,
            model_acc=round(model_acc, 4) if model_acc else None,
        )
        
        return max(0.0, min(1.0, calibrated))
    
    def get_type_accuracy(self, query_type: str) -> float:
        """Get recent accuracy for a query type."""
        if query_type not in self._stats:
            return 0.5
        return self._stats[query_type].recent_accuracy_value
    
    def get_trend_data(self) -> dict:
        """Get trending data for monitoring dashboard.
        
        Returns:
            Dict with per-type accuracy, overall accuracy, etc.
        """
        overall_correct = sum(s.correct for s in self._stats.values())
        overall_total = sum(s.total for s in self._stats.values())
        
        return {
            "overall_accuracy": round(overall_correct / overall_total, 4) if overall_total > 0 else 0.5,
            "total_feedback": overall_total,
            "by_type": {
                k: v.to_dict() for k, v in sorted(
                    self._stats.items(), 
                    key=lambda x: x[1].total, 
                    reverse=True
                )[:20]
            },
            "by_user_top5": {
                k: v.to_dict() for k, v in sorted(
                    self._user_stats.items(),
                    key=lambda x: x[1].total,
                    reverse=True
                )[:5]
            },
            "by_model": {
                k: v.to_dict() for k, v in sorted(
                    self._model_stats.items(),
                    key=lambda x: x[1].total,
                    reverse=True
                )[:10]
            },
            "by_tool": {
                k: v.to_dict() for k, v in sorted(
                    self._tool_stats.items(),
                    key=lambda x: x[1].total,
                    reverse=True
                )[:10]
            },
        }
    
    def to_state(self) -> dict:
        """Serialize state for Redis persistence."""
        return {
            "stats": {k: v.to_dict() for k, v in self._stats.items()},
            "user_stats": {k: v.to_dict() for k, v in self._user_stats.items()},
            "model_stats": {k: v.to_dict() for k, v in self._model_stats.items()},
            "tool_stats": {k: v.to_dict() for k, v in self._tool_stats.items()},
        }
    
    def load_state(self, state: dict) -> None:
        """Load state from Redis persistence."""
        for key, data in state.get("stats", {}).items():
            stats = self._get_or_create(self._stats, key)
            stats.total = data.get("total", 0)
            stats.correct = data.get("correct", 0)
            stats.incorrect = data.get("incorrect", 0)
        
        for key, data in state.get("user_stats", {}).items():
            stats = self._get_or_create(self._user_stats, key)
            stats.total = data.get("total", 0)
            stats.correct = data.get("correct", 0)
            stats.incorrect = data.get("incorrect", 0)
        
        for key, data in state.get("model_stats", {}).items():
            stats = self._get_or_create(self._model_stats, key)
            stats.total = data.get("total", 0)
            stats.correct = data.get("correct", 0)
            stats.incorrect = data.get("incorrect", 0)
        
        for key, data in state.get("tool_stats", {}).items():
            stats = self._get_or_create(self._tool_stats, key)
            stats.total = data.get("total", 0)
            stats.correct = data.get("correct", 0)
            stats.incorrect = data.get("incorrect", 0)
    
    async def persist_to_redis(self, redis_cache, ttl_seconds: int = 86400 * 7) -> bool:
        """Persist calibrator state to Redis.
        
        Args:
            redis_cache: RedisCache instance
            ttl_seconds: Time to live in seconds (default: 7 days)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            state = self.to_state()
            await redis_cache.set_json("confidence_calibrator_state", state, ttl=ttl_seconds)
            logger.info("confidence_calibrator_persisted", ttl_seconds=ttl_seconds)
            return True
        except Exception as e:
            logger.warning("confidence_calibrator_persist_failed", error=str(e))
            return False
    
    async def load_from_redis(self, redis_cache) -> bool:
        """Load calibrator state from Redis.
        
        Args:
            redis_cache: RedisCache instance
            
        Returns:
            True if loaded successfully, False otherwise
        """
        try:
            state = await redis_cache.get_json("confidence_calibrator_state")
            if state:
                self.load_state(state)
                logger.info("confidence_calibrator_loaded", keys=len(state))
                return True
            return False
        except Exception as e:
            logger.warning("confidence_calibrator_load_failed", error=str(e))
            return False


__all__ = ["ConfidenceCalibrator", "CalibrationStats"]