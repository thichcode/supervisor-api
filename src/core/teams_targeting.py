"""Microsoft Teams target inference helpers.

This module resolves who a Teams message is addressed to using platform-native
signals first, then light text fallback.

Priority order:
1. Explicit mention targets
2. Reply-chain target hints
3. Conversation type defaults
4. Bot sender / channel policy hints
5. Text fallback
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

import re


class TargetType(str, Enum):
    THUONG = "Thuong"
    WORKFLOW_BOT = "workflow_bot"
    UNCLEAR = "unclear"
    IGNORE = "ignore"


@dataclass(slots=True)
class TargetDecision:
    target: TargetType
    confidence: float
    reason: str

    @property
    def should_respond(self) -> bool:
        return self.target in {TargetType.THUONG, TargetType.WORKFLOW_BOT}

    @property
    def should_clarify(self) -> bool:
        return self.target == TargetType.UNCLEAR

    @property
    def should_skip(self) -> bool:
        return self.target == TargetType.IGNORE


@dataclass(slots=True)
class TeamsSignal:
    conversation_type: str = "personal"  # personal | groupChat | channel
    mention_targets: list[str] | None = None
    reply_target: str | None = None
    sender_is_bot: bool = False
    team_id: str | None = None
    channel_id: str | None = None
    thread_id: str | None = None
    channel_policy_target: str | None = None


class TeamsTargetResolver:
    """Rule-based target inference for Microsoft Teams."""

    THUONG_PATTERNS = (
        r"\bthuong\b",
        r"\bthương\b",
    )
    WORKFLOW_PATTERNS = (
        r"\bworkflow bot\b",
        r"\bworkflow\b",
        r"\bapproval\b",
        r"\bph[êe]\s*duyệt\b",
        r"\bph[êe]\s*duyet\b",
        r"\bticket\b",
        r"\bincident\b",
        r"\brequest\b",
        r"\bautomation\b",
        r"\btự động\b",
        r"\btu dong\b",
    )

    def __init__(self, min_confidence: float = 0.35):
        self.min_confidence = min_confidence

    def resolve(
        self,
        *,
        current_text: str,
        signal: TeamsSignal,
        history_texts: Iterable[str] | None = None,
    ) -> TargetDecision:
        if signal.sender_is_bot:
            return TargetDecision(
                target=TargetType.IGNORE,
                confidence=0.0,
                reason="Sender is bot",
            )

        mention_targets = [m.lower() for m in (signal.mention_targets or []) if m]
        current_lower = (current_text or "").lower()
        history = "\n".join(t for t in (history_texts or []) if t).lower()
        corpus = f"{history}\n{current_lower}".strip()

        # 1) Explicit mention wins.
        if self._contains_target(mention_targets, "thuong"):
            return TargetDecision(TargetType.THUONG, 1.0, "Explicit mention to Thuong")
        if self._contains_target(mention_targets, "workflow bot") or self._contains_target(mention_targets, "workflow_bot"):
            return TargetDecision(TargetType.WORKFLOW_BOT, 1.0, "Explicit mention to workflow bot")

        # 2) Reply-chain / parent target hint.
        if signal.reply_target:
            normalized_reply = signal.reply_target.lower()
            if self._contains_target([normalized_reply], "thuong"):
                return TargetDecision(TargetType.THUONG, 0.95, "Inherited from reply target")
            if self._contains_target([normalized_reply], "workflow bot") or self._contains_target([normalized_reply], "workflow_bot"):
                return TargetDecision(TargetType.WORKFLOW_BOT, 0.95, "Inherited from reply target")

        # 3) Conversation type defaults.
        if signal.conversation_type == "personal":
            return TargetDecision(TargetType.THUONG, 0.75, "Personal chat defaults to Thuong")

        # 4) Channel policy override.
        if signal.channel_policy_target:
            policy = signal.channel_policy_target.lower()
            if self._contains_target([policy], "thuong"):
                return TargetDecision(TargetType.THUONG, 0.85, "Channel policy matched Thuong")
            if self._contains_target([policy], "workflow bot") or self._contains_target([policy], "workflow_bot"):
                return TargetDecision(TargetType.WORKFLOW_BOT, 0.85, "Channel policy matched workflow bot")

        # 5) Lightweight text fallback.
        score_thuong = 0.0
        score_workflow = 0.0
        signals: list[str] = []

        if self._matches_any(corpus, self.THUONG_PATTERNS):
            score_thuong += 0.8
            signals.append("text_mentions_thuong")
        if self._matches_any(corpus, self.WORKFLOW_PATTERNS):
            score_workflow += 0.6
            signals.append("text_mentions_workflow")

        if signal.conversation_type == "channel":
            score_workflow += 0.1
        elif signal.conversation_type == "groupChat":
            score_workflow += 0.05

        if score_thuong > score_workflow and score_thuong >= 0.7:
            return TargetDecision(TargetType.THUONG, min(score_thuong, 1.0), self._reason(signals, "Thuong"))
        if score_workflow > score_thuong and score_workflow >= 0.7:
            return TargetDecision(TargetType.WORKFLOW_BOT, min(score_workflow, 1.0), self._reason(signals, "workflow bot"))

        best = max(score_thuong, score_workflow)
        if best >= self.min_confidence:
            return TargetDecision(TargetType.UNCLEAR, best, self._reason(signals, "unclear"))

        return TargetDecision(TargetType.IGNORE, 0.0, "No meaningful Teams signal")

    def _contains_target(self, targets: list[str], target: str) -> bool:
        needle = target.lower()
        return any(needle in t for t in targets)

    def _matches_any(self, text: str, patterns: tuple[str, ...]) -> bool:
        return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

    def _reason(self, signals: list[str], target_label: str) -> str:
        if not signals:
            return f"Target inferred as {target_label}"
        return f"Target inferred as {target_label} via signals: {', '.join(signals)}"


def extract_teams_signal(metadata: dict[str, Any] | None) -> TeamsSignal:
    metadata = metadata or {}
    mention_targets = metadata.get("mention_targets") or metadata.get("mentions") or []
    if isinstance(mention_targets, str):
        mention_targets = [mention_targets]

    return TeamsSignal(
        conversation_type=metadata.get("conversation_type", metadata.get("conversationType", "personal")),
        mention_targets=list(mention_targets),
        reply_target=metadata.get("reply_target") or metadata.get("replyToTarget") or metadata.get("reply_target_name"),
        sender_is_bot=bool(metadata.get("sender_is_bot", metadata.get("from_bot", False))),
        team_id=metadata.get("team_id") or metadata.get("teamId"),
        channel_id=metadata.get("channel_id") or metadata.get("channelId"),
        thread_id=metadata.get("thread_id") or metadata.get("threadId"),
        channel_policy_target=metadata.get("channel_policy_target"),
    )


__all__ = ["TeamsSignal", "TeamsTargetResolver", "TargetDecision", "TargetType", "extract_teams_signal"]
