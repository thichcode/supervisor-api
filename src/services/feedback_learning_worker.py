from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from src.config import get_settings
from src.core.bayesian_confidence import BayesianConfidence
from src.db import async_session
from src.db.models import ResponseLearningEvent
from src.memory.cache import redis_cache
from src.services.learning_service import LearningService

logger = structlog.get_logger()
settings = get_settings()


class FeedbackReplayWorker:
    """Replay persisted feedback events into live learning state."""

    def __init__(
        self,
        session_factory: Callable[[], Any] = None,
        supervisor: Any = None,
        learning_service_factory: Optional[Callable[[AsyncSession], Any]] = None,
        state_key: str = "learning:bayesian_state",
    ):
        self.session_factory = session_factory or async_session
        self.supervisor = supervisor
        self.learning_service_factory = learning_service_factory or (lambda session: LearningService(session))
        self.state_key = state_key
        self._state_loaded = False

    async def start(self, interval_seconds: int = 30):
        """Run the replay loop forever until cancelled."""
        while True:
            try:
                await self.replay_once()
            except Exception as exc:
                logger.error("feedback_replay_loop_failed", error=str(exc))
            await self._sleep(interval_seconds)

    async def _sleep(self, interval_seconds: int):
        import asyncio

        await asyncio.sleep(interval_seconds)

    async def replay_once(self, limit: Optional[int] = None) -> int:
        """Replay all pending events once and persist the updated state."""
        async with self.session_factory() as session:
            await self._load_state_if_needed()
            pending_events = await self._get_pending_events(session, limit=limit)
            if not pending_events:
                return 0

            learning_service = self.learning_service_factory(session)
            processed = 0
            for event in pending_events:
                await self._process_event(session, event, learning_service)
                processed += 1

            await session.commit()
            await self._save_state()
            return processed

    async def _load_state_if_needed(self) -> None:
        if self._state_loaded or not self.supervisor:
            return

        state = await redis_cache.get_json(self.state_key)
        if state:
            bayes = getattr(self.supervisor, "bayesian_confidence", None)
            if bayes and hasattr(bayes, "load_state"):
                bayes.load_state(state)
            validator = getattr(self.supervisor, "response_validator", None)
            if validator and hasattr(validator, "confidence_calculator"):
                calculator = validator.confidence_calculator
                if calculator and hasattr(calculator, "load_state"):
                    calculator.load_state(state)
            logger.info("feedback_learning_state_loaded", state_key=self.state_key)

        self._state_loaded = True

    async def _save_state(self) -> None:
        if not self.supervisor:
            return

        bayes = getattr(self.supervisor, "bayesian_confidence", None)
        if not bayes or not hasattr(bayes, "to_state"):
            return

        await redis_cache.set_json(self.state_key, bayes.to_state(), ttl=86400 * 30)

    async def _get_pending_events(self, session: AsyncSession, limit: Optional[int] = None) -> list[ResponseLearningEvent]:
        query = select(ResponseLearningEvent).where(ResponseLearningEvent.processed == False).order_by(
            ResponseLearningEvent.created_at.asc(),
            ResponseLearningEvent.id.asc(),
        )
        if limit:
            query = query.limit(limit)

        result = await session.execute(query)
        return list(result.scalars().all())

    async def _process_event(self, session: AsyncSession, event: ResponseLearningEvent, learning_service: Any) -> None:
        payload = event.event_payload or {}
        user_id = event.user_id or payload.get("user_id")
        request_id = event.request_id
        model_name = payload.get("model_name") or settings.llm_model

        is_positive = self._derive_feedback_signal(event.event_type, payload)
        if is_positive is not None:
            self._apply_feedback_signal(user_id=user_id or request_id, request_id=request_id, is_positive=is_positive, model_name=model_name)

        await self._apply_style_learning(
            learning_service=learning_service,
            user_id=user_id,
            request_id=request_id,
            payload=payload,
            event_type=event.event_type,
        )

        self._apply_routing_feedback(payload)

        event.processed = True
        event.processed_at = datetime.now(timezone.utc)
        session.add(event)

    def _apply_feedback_signal(self, *, user_id: str, request_id: str, is_positive: bool, model_name: str) -> None:
        calculators = []
        bayes = getattr(self.supervisor, "bayesian_confidence", None)
        if bayes:
            calculators.append(bayes)
        validator = getattr(self.supervisor, "response_validator", None)
        if validator and getattr(validator, "confidence_calculator", None) is not bayes:
            calculators.append(validator.confidence_calculator)

        for calculator in calculators:
            if hasattr(calculator, "update_with_feedback"):
                calculator.update_with_feedback(user_id, request_id, is_positive, model_name)

    async def _apply_style_learning(
        self,
        *,
        learning_service: Any,
        user_id: Optional[str],
        request_id: str,
        payload: dict,
        event_type: str,
    ) -> None:
        if not user_id:
            return

        style_source_text = payload.get("edited_output_text") or payload.get("feedback_text") or payload.get("style_text")
        if not style_source_text:
            return

        signals = self._infer_style_signals(style_source_text, source=event_type)
        if not signals:
            return

        await learning_service.add_signals(
            user_id=user_id,
            request_id=request_id,
            signals=signals,
            evidence={
                "event_type": event_type,
                "feedback_label": payload.get("feedback_label"),
                "approval_status": payload.get("approval_status"),
            },
        )
        await learning_service.recompute_profile(user_id)

    def _apply_routing_feedback(self, payload: dict) -> None:
        router = getattr(getattr(self.supervisor, "decision_engine", None), "router", None)
        path = payload.get("agent_path")
        if router and path:
            try:
                router.record_feedback(
                    query=payload.get("original_message", ""),
                    path=list(path),
                    user_satisfied=self._derive_routing_satisfaction(payload),
                    issues=payload.get("issues", []),
                )
            except Exception as exc:
                logger.warning("routing_feedback_update_failed", error=str(exc))

    def _derive_routing_satisfaction(self, payload: dict) -> bool:
        signal = self._derive_feedback_signal(payload.get("event_type", ""), payload)
        return bool(signal) if signal is not None else True

    def _derive_feedback_signal(self, event_type: str, payload: dict) -> Optional[bool]:
        if event_type == "approval_decision":
            status = (payload.get("approval_status") or "").lower()
            if status == "approved":
                return True
            if status == "rejected":
                return False
            return None

        feedback_type = (payload.get("feedback_type") or "").lower()
        feedback_label = (payload.get("feedback_label") or "").lower()
        vote = (payload.get("vote") or "").lower()
        score = payload.get("feedback_score")

        if vote == "skip":
            return None
        if vote == "change" or feedback_label in {"edited", "change", "negative"} or feedback_type == "rejection":
            return False
        if vote == "agree" or feedback_label in {"accepted", "agree", "positive"} or feedback_type == "approval":
            return True
        if isinstance(score, (int, float)):
            return score >= 0.5
        return None


    def _infer_style_signals(self, text: str, source: str = "inferred") -> list[dict]:
        normalized = " ".join(text.strip().lower().split())
        words = normalized.split()
        signals: list[dict] = []

        verbosity = "detailed" if len(words) > 40 else "concise" if len(words) <= 12 else "balanced"
        signals.append({"signal_type": "verbosity", "signal_value": verbosity, "signal_strength": 0.65, "source": source})

        tone = (
            "formal"
            if any(token in normalized for token in ["xin vui lòng", "vui lòng", "please", "cảm ơn"])
            else "casual"
            if any(token in normalized for token in ["ok", "oke", "haha", "lol", "bro"])
            else "balanced"
        )
        signals.append({"signal_type": "tone", "signal_value": tone, "signal_strength": 0.6, "source": source})

        fmt = (
            "steps"
            if any(marker in text for marker in ["\n1.", "\n2.", "Bước 1", "Step 1"])
            else "bullets"
            if any(marker in text for marker in ["\n-", "\n*"])
            else "paragraph"
        )
        signals.append({"signal_type": "format", "signal_value": fmt, "signal_strength": 0.55, "source": source})

        language = (
            "mixed"
            if any(ch in normalized for ch in ["please", "thanks", "step", "ticket"]) and any(ch in normalized for ch in ["cảm", "vui", "bước", "hướng dẫn"])
            else "en"
            if all(ord(c) < 128 for c in normalized)
            else "vi"
        )
        signals.append({"signal_type": "language", "signal_value": language, "signal_strength": 0.55, "source": source})
        return signals


__all__ = ["FeedbackReplayWorker"]
