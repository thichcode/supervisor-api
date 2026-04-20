from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
import re
from typing import Optional

STOPWORDS = {
    "và", "là", "của", "cho", "the", "and", "or", "theo", "với", "mình", "tôi", "bạn", "anh", "chị",
    "cái", "này", "đó", "thì", "lại", "đang", "có", "không", "được", "đi", "nhé", "ạ", "ok", "oke",
}

CONTINUE_CUES = {
    "tiếp", "tiếp đi", "tiếp tục", "sao nữa", "rồi sao", "còn gì", "chi tiết thêm", "thêm", "nữa",
    "giải thích thêm", "nói tiếp", "continue",
}

SHIFT_CUES = {
    "nhân tiện", "chuyển qua", "vấn đề khác", "cái khác", "quay lại", "bỏ qua", "hỏi thêm", "thêm một việc",
}

CLARIFY_CUES = {
    "thế sao", "sao vậy", "ý bạn là gì", "không rõ", "chưa rõ", "what do you mean", "cụ thể hơn", "giải thích kỹ",
}

QUESTION_CUES = {
    "?", "how", "what", "why", "làm sao", "thế nào", "tại sao", "có thể", "giúp mình", "giúp tôi",
}


@dataclass
class ConversationContinuityResult:
    mode: str
    continuity_score: float
    reason: str
    matched_entities: list[str]
    should_refresh_summary: bool
    suggested_topic_title: Optional[str] = None
    new_open_loops: list[dict] = None
    closed_loops: list[str] = None

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "continuity_score": self.continuity_score,
            "reason": self.reason,
            "matched_entities": self.matched_entities,
            "should_refresh_summary": self.should_refresh_summary,
            "suggested_topic_title": self.suggested_topic_title,
            "new_open_loops": self.new_open_loops or [],
            "closed_loops": self.closed_loops or [],
        }


class ConversationContinuityEvaluator:
    def __init__(self, threshold_continue: float = 0.72, threshold_new_topic: float = 0.45):
        self.threshold_continue = threshold_continue
        self.threshold_new_topic = threshold_new_topic

    def _normalize(self, text: str) -> str:
        return re.sub(r"\s+", " ", (text or "")).strip().lower()

    def _tokens(self, text: str) -> list[str]:
        normalized = self._normalize(text)
        tokens = re.findall(r"[\wÀ-ỹ']+", normalized)
        return [tok for tok in tokens if tok not in STOPWORDS and len(tok) > 1]

    def _overlap_score(self, current_message: str, reference_text: str) -> float:
        current_tokens = set(self._tokens(current_message))
        reference_tokens = set(self._tokens(reference_text))
        if not current_tokens or not reference_tokens:
            return 0.0
        overlap = len(current_tokens & reference_tokens)
        base = max(len(current_tokens), len(reference_tokens), 1)
        return overlap / base

    def _extract_entities(self, text: str) -> list[str]:
        tokens = self._tokens(text)
        entities = []
        for token in tokens:
            if len(token) >= 4:
                entities.append(token)
        return list(dict.fromkeys(entities))[:8]

    def _loop_key(self, text: str) -> str:
        return sha1(self._normalize(text).encode("utf-8")).hexdigest()[:12]

    def evaluate(
        self,
        current_message: str,
        recent_messages: list[str],
        conversation_summary: Optional[str],
        active_topic_summary: Optional[str],
        active_topic_title: Optional[str],
        open_loops: Optional[list] = None,
        key_entities: Optional[list] = None,
    ) -> dict:
        normalized = self._normalize(current_message)
        reference_parts = [
            active_topic_title or "",
            active_topic_summary or "",
            conversation_summary or "",
            " ".join(recent_messages[-3:]) if recent_messages else "",
        ]
        reference_text = " \n ".join(part for part in reference_parts if part)

        score = 0.5
        reasons = []
        matched_entities = []

        overlap = self._overlap_score(current_message, reference_text)
        score += min(0.22, overlap * 0.9)
        if overlap >= 0.2:
            reasons.append("token_overlap")

        if any(cue in normalized for cue in SHIFT_CUES):
            score -= 0.35
            reasons.append("topic_shift_cue")
        if any(cue in normalized for cue in CONTINUE_CUES):
            score += 0.22
            reasons.append("continuation_cue")
        if any(cue in normalized for cue in CLARIFY_CUES):
            score -= 0.1
            reasons.append("clarify_cue")

        current_entities = self._extract_entities(current_message)
        known_entities = set(key_entities or [])
        if active_topic_title:
            known_entities.update(self._extract_entities(active_topic_title))
        if active_topic_summary:
            known_entities.update(self._extract_entities(active_topic_summary))
        entity_matches = [ent for ent in current_entities if ent in known_entities]
        if entity_matches:
            score += min(0.18, 0.04 * len(entity_matches))
            matched_entities.extend(entity_matches)
            reasons.append("entity_overlap")

        if len(normalized.split()) <= 4:
            score += 0.06 if any(cue in normalized for cue in CONTINUE_CUES) else 0.0
            if not any(cue in normalized for cue in CONTINUE_CUES):
                reasons.append("short_followup")

        question_like = any(cue in normalized for cue in QUESTION_CUES)
        if question_like:
            score -= 0.03
            reasons.append("question_like")

        score = max(0.0, min(1.0, score))

        if score >= self.threshold_continue:
            mode = "continuation"
            reason = ",".join(reasons) or "high_continuity"
        elif score <= self.threshold_new_topic:
            mode = "new_topic"
            reason = ",".join(reasons) or "low_continuity"
        else:
            mode = "clarify"
            reason = ",".join(reasons) or "ambiguous"

        suggested_topic_title = active_topic_title
        if mode == "new_topic":
            if current_entities:
                suggested_topic_title = " ".join(current_entities[:3]).title()
            else:
                suggested_topic_title = current_message[:60].strip() or active_topic_title

        new_open_loops = []
        if question_like or mode in {"clarify", "new_topic"}:
            new_open_loops.append(
                {
                    "key": self._loop_key(current_message),
                    "text": current_message[:240],
                    "source": "user_turn",
                }
            )

        should_refresh_summary = mode != "continuation" or score < 0.6
        closed_loops = []
        if open_loops:
            for loop in open_loops:
                loop_text = self._normalize(loop.get("text", ""))
                if loop_text and any(ent in loop_text for ent in current_entities):
                    closed_loops.append(loop.get("key"))

        return ConversationContinuityResult(
            mode=mode,
            continuity_score=score,
            reason=reason,
            matched_entities=matched_entities,
            should_refresh_summary=should_refresh_summary,
            suggested_topic_title=suggested_topic_title,
            new_open_loops=new_open_loops,
            closed_loops=closed_loops,
        ).to_dict()
