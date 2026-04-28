"""Group chat target inference helpers.

This module implements a small rule-based router that decides whether a
message in a group/thread is likely addressed to Thuong, the workflow bot,
is ambiguous, or should be ignored.

The resolver is intentionally conservative:
- it only acts when group_chat is explicitly enabled
- it prefers thread history over the latest message
- it never guesses when there is no meaningful signal
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

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


class GroupChatTargetResolver:
    """Rule-based target inference for group chat threads."""

    THUONG_PATTERNS = (
        r"\bthuong\b",
        r"\bthương\b",
    )
    WORKFLOW_BOT_PATTERNS = (
        r"\bworkflow bot\b",
        r"\bworkflow\b",
        r"\bbot\b",
        r"\bapproval\b",
        r"\bph[êe]\s*duyệt\b",
        r"\bph[êe]\s*duyet\b",
        r"\bticket\b",
        r"\bincident\b",
        r"\bautomation\b",
        r"\btự động\b",
        r"\btu dong\b",
        r"\brequest\b",
    )
    AMBIGUOUS_PRONOUNS = (
        "anh ấy",
        "chị ấy",
        "cô ấy",
        "nó",
        "cái này",
        "bên kia",
        "người đó",
        "he",
        "she",
        "it",
        "this",
        "that",
    )

    def __init__(self, min_confidence: float = 0.35):
        self.min_confidence = min_confidence

    def resolve(
        self,
        *,
        current_text: str,
        history_texts: Sequence[str] | None = None,
        group_chat: bool = False,
    ) -> TargetDecision:
        if not group_chat:
            return TargetDecision(
                target=TargetType.IGNORE,
                confidence=0.0,
                reason="group_chat is disabled",
            )

        history_texts = history_texts or []
        current = (current_text or "").strip()
        history = "\n".join(text for text in history_texts if text).strip()
        corpus = f"{history}\n{current}".strip().lower()
        current_lower = (current or "").lower()

        score_thuong = 0.0
        score_workflow = 0.0
        signals: list[str] = []

        if self._matches_any(corpus, self.THUONG_PATTERNS):
            score_thuong += 0.8
            signals.append("explicit_thuong")
        if self._matches_any(current_lower, self.THUONG_PATTERNS):
            score_thuong += 0.4
            signals.append("current_thuong")

        if self._matches_any(corpus, self.WORKFLOW_BOT_PATTERNS):
            score_workflow += 0.5
            signals.append("workflow_context")
        if self._matches_any(current_lower, self.WORKFLOW_BOT_PATTERNS):
            score_workflow += 0.3
            signals.append("current_workflow_context")

        if self._contains_ambiguous_pronoun(current_lower):
            signals.append("ambiguous_pronoun")
            # Ambiguous references should lean on thread history.
            if score_thuong >= score_workflow and score_thuong > 0:
                score_thuong += 0.05
            elif score_workflow > score_thuong:
                score_workflow += 0.05

        # Tighten the workflow score when thread history clearly points to
        # ticketing/approval/automation style content.
        workflow_keywords = (
            "ticket",
            "approval",
            "phê duyệt",
            "phe duyet",
            "workflow",
            "automation",
            "incident",
            "request",
            "escalation",
            "tự động",
            "tu dong",
        )
        if any(kw in corpus for kw in workflow_keywords):
            score_workflow += 0.2

        # If the thread history is present, prefer it over a single new message.
        if history and not current:
            score_workflow *= 0.95
            score_thuong *= 0.95

        winner, confidence = self._pick_winner(score_thuong, score_workflow)
        if winner == TargetType.THUONG and confidence >= 0.7:
            return TargetDecision(
                target=TargetType.THUONG,
                confidence=min(confidence, 1.0),
                reason=self._reason(signals, "Thuong"),
            )

        if winner == TargetType.WORKFLOW_BOT and confidence >= 0.7:
            return TargetDecision(
                target=TargetType.WORKFLOW_BOT,
                confidence=min(confidence, 1.0),
                reason=self._reason(signals, "workflow bot"),
            )

        if confidence >= self.min_confidence:
            return TargetDecision(
                target=TargetType.UNCLEAR,
                confidence=min(confidence, 1.0),
                reason=self._reason(signals, "unclear"),
            )

        return TargetDecision(
            target=TargetType.IGNORE,
            confidence=0.0,
            reason="No meaningful signal for Thuong or workflow bot",
        )

    def _pick_winner(self, score_thuong: float, score_workflow: float) -> tuple[TargetType, float]:
        if score_thuong > score_workflow:
            return TargetType.THUONG, score_thuong
        if score_workflow > score_thuong:
            return TargetType.WORKFLOW_BOT, score_workflow
        return TargetType.UNCLEAR, score_thuong

    def _matches_any(self, text: str, patterns: Iterable[str]) -> bool:
        return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

    def _contains_ambiguous_pronoun(self, text: str) -> bool:
        return any(pronoun in text for pronoun in self.AMBIGUOUS_PRONOUNS)

    def _reason(self, signals: list[str], target_label: str) -> str:
        if not signals:
            return f"Target inferred as {target_label}"
        return f"Target inferred as {target_label} via signals: {', '.join(signals)}"


__all__ = ["GroupChatTargetResolver", "TargetDecision", "TargetType"]
