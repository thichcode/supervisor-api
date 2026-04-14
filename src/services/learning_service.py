from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import UserStyleProfile, UserStyleSignal


class LearningService:
    def __init__(self, session: AsyncSession):
        self.session = session

    def infer_style_signals(self, text: str | None, source: str = "inferred") -> list[dict]:
        if not text:
            return []

        normalized = " ".join(text.strip().lower().split())
        signals: list[dict] = []
        words = normalized.split()

        verbosity = "detailed" if len(words) > 40 else "concise" if len(words) <= 12 else "balanced"
        signals.append({"signal_type": "verbosity", "signal_value": verbosity, "signal_strength": 0.65, "source": source})

        tone = "formal" if any(token in normalized for token in ["xin vui lòng", "vui lòng", "please", "cảm ơn"]) else "casual" if any(token in normalized for token in ["ok", "oke", "haha", "lol", "bro"]) else "balanced"
        signals.append({"signal_type": "tone", "signal_value": tone, "signal_strength": 0.6, "source": source})

        fmt = "steps" if any(marker in text for marker in ["\n1.", "\n2.", "Bước 1", "Step 1"]) else "bullets" if any(marker in text for marker in ["\n-", "\n*"]) else "paragraph"
        signals.append({"signal_type": "format", "signal_value": fmt, "signal_strength": 0.55, "source": source})

        language = "mixed" if any(ch in normalized for ch in ["please", "thanks", "step", "ticket"]) and any(ch in normalized for ch in ["cảm", "vui", "bước", "hướng dẫn"]) else "en" if all(ord(c) < 128 for c in normalized) else "vi"
        signals.append({"signal_type": "language", "signal_value": language, "signal_strength": 0.55, "source": source})
        return signals

    async def add_signals(self, user_id: Optional[str], request_id: Optional[str], signals: list[dict], evidence: Optional[dict] = None) -> None:
        if not user_id or not signals:
            return
        for signal in signals:
            self.session.add(UserStyleSignal(
                user_id=user_id,
                request_id=request_id,
                signal_type=signal["signal_type"],
                signal_value=signal["signal_value"],
                signal_strength=signal.get("signal_strength", 0.5),
                source=signal.get("source", "inferred"),
                evidence=evidence or {},
            ))
        await self.session.flush()

    async def recompute_profile(self, user_id: Optional[str]) -> Optional[UserStyleProfile]:
        if not user_id:
            return None

        result = await self.session.execute(
            select(UserStyleSignal)
            .where(UserStyleSignal.user_id == user_id)
            .order_by(UserStyleSignal.created_at.desc())
            .limit(100)
        )
        signals = list(result.scalars().all())
        if not signals:
            return None

        grouped: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for signal in signals:
            grouped[signal.signal_type][signal.signal_value] += signal.signal_strength or 0.0

        def top_value(signal_type: str) -> tuple[Optional[str], float]:
            values = grouped.get(signal_type, {})
            if not values:
                return None, 0.0
            value, score = max(values.items(), key=lambda item: item[1])
            total = sum(values.values()) or 1.0
            return value, min(1.0, score / total)

        tone, tone_conf = top_value("tone")
        verbosity, verbosity_conf = top_value("verbosity")
        fmt, fmt_conf = top_value("format")
        language, language_conf = top_value("language")
        confidence = round((tone_conf + verbosity_conf + fmt_conf + language_conf) / 4, 4)

        result = await self.session.execute(select(UserStyleProfile).where(UserStyleProfile.user_id == user_id))
        profile = result.scalar_one_or_none()
        if profile is None:
            profile = UserStyleProfile(user_id=user_id)
            self.session.add(profile)

        profile.preferred_tone = tone
        profile.preferred_verbosity = verbosity
        profile.preferred_format = fmt
        profile.preferred_language = language
        profile.confidence_score = confidence
        profile.sample_count = len(signals)
        profile.last_inferred_at = datetime.utcnow()
        profile.response_persona_hint = self._build_persona_hint(tone, verbosity, fmt, language)
        await self.session.flush()
        return profile

    def _build_persona_hint(self, tone: Optional[str], verbosity: Optional[str], fmt: Optional[str], language: Optional[str]) -> str:
        parts = []
        if tone:
            parts.append(f"tone={tone}")
        if verbosity:
            parts.append(f"verbosity={verbosity}")
        if fmt:
            parts.append(f"format={fmt}")
        if language:
            parts.append(f"language={language}")
        return ", ".join(parts)
