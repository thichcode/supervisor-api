from __future__ import annotations

import html
import re
from typing import Any, Iterable

from src.core.kb_templates import KBCategoryTemplateMapper


_BULLET_RE = re.compile(r"^(?:[-*•]|\d+[.)]|[a-zA-Z][.)])\s+(.+)$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_TAG_RE = re.compile(r"<[^>]+>")


def _pick(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _clean(text: Any) -> str:
    raw = html.unescape(str(text or ""))
    raw = _TAG_RE.sub(" ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _is_action_like(line: str) -> bool:
    lowered = line.lower()
    verbs = (
        "open",
        "go to",
        "click",
        "check",
        "verify",
        "ensure",
        "use",
        "install",
        "update",
        "restart",
        "login",
        "sign in",
        "reset",
        "create",
        "contact",
        "kiểm tra",
        "mở",
        "chọn",
        "bấm",
        "nhấn",
        "vào",
        "xác nhận",
        "đổi",
        "khởi động lại",
        "đăng nhập",
        "liên hệ",
        "gửi",
        "tạo",
    )
    return lowered.startswith(verbs) or any(lowered.startswith(prefix) for prefix in verbs)


def _extract_steps_from_metadata(metadata: dict | None) -> list[str]:
    if not metadata:
        return []
    raw_steps = metadata.get("steps") or metadata.get("action_items") or []
    if isinstance(raw_steps, str):
        raw_steps = [raw_steps]
    steps: list[str] = []
    for item in raw_steps:
        if isinstance(item, dict):
            candidate = item.get("text") or item.get("title") or item.get("label") or item.get("step")
        else:
            candidate = item
        cleaned = _clean(candidate)
        if cleaned:
            steps.append(cleaned)
    return steps


def _extract_steps_from_text(text: str) -> list[str]:
    steps: list[str] = []
    for raw_line in text.splitlines():
        line = _clean(raw_line)
        if not line:
            continue
        match = _BULLET_RE.match(line)
        if match:
            candidate = _clean(match.group(1))
            if candidate:
                steps.append(candidate)
            continue
        if _is_action_like(line):
            steps.append(line)
    if steps:
        return steps

    sentences = [segment.strip() for segment in _SENTENCE_SPLIT_RE.split(text) if segment.strip()]
    for sentence in sentences:
        cleaned = _clean(sentence)
        if cleaned and _is_action_like(cleaned):
            steps.append(cleaned)
        if len(steps) >= 5:
            break
    return steps


def _extract_summary(text: str) -> str:
    text = _clean(text)
    if not text:
        return ""
    sentences = [segment.strip() for segment in _SENTENCE_SPLIT_RE.split(text) if segment.strip()]
    if sentences:
        summary = " ".join(sentences[:2])
    else:
        summary = text
    if len(summary) > 240:
        summary = summary[:237].rstrip() + "..."
    return summary


def build_kb_card(result: Any, query: str | None = None) -> dict[str, Any]:
    """Create a user-friendly KB card from a search result."""
    metadata = _pick(result, "metadata", None) or {}
    title = _clean(_pick(result, "title", None) or metadata.get("title") or "KB")
    content = _clean(_pick(result, "content", None) or "")
    knowledge_type = _pick(result, "knowledge_type", None)
    if isinstance(knowledge_type, str):
        kb_type = knowledge_type
    else:
        kb_type = _pick(knowledge_type, "value", None) or metadata.get("kind") or metadata.get("type") or "kb"
    category = _clean(_pick(result, "category", None) or metadata.get("category") or "")
    similarity = _pick(result, "similarity", None)
    summary = _extract_summary(metadata.get("summary") or content)
    steps = _extract_steps_from_metadata(metadata) or _extract_steps_from_text(content)
    if not steps and summary:
        steps = [summary]

    steps = steps[:5]
    template_match = KBCategoryTemplateMapper.detect(query)
    if query and summary and query.lower() not in summary.lower():
        relevance = f"Phù hợp với: {query}"
    else:
        relevance = ""

    source_hint = f"{str(kb_type).upper()}"
    if category:
        source_hint += f" · {category}"
    if isinstance(similarity, (int, float)):
        source_hint += f" · score {similarity:.2f}"

    return {
        "id": _pick(result, "id", None) or metadata.get("id") or "",
        "title": title,
        "summary": summary,
        "steps": steps,
        "relevance": relevance,
        "source_hint": source_hint,
        "kind": kb_type,
        "category": category,
        "similarity": similarity,
        "template_id": template_match.template_id if template_match else "",
        "template_label": template_match.label if template_match else "",
        "template_hint": template_match.action_hint if template_match else "",
    }


def format_kb_card_text(card: dict[str, Any], *, header: str = "KB phù hợp") -> str:
    lines = [f"{header}: {card.get('title') or 'KB'}"]
    if card.get("id"):
        lines.append(f"ID: {card['id']}")
    if card.get("template_label"):
        lines.append(f"Mẫu KB: {card['template_label']}")
    if card.get("template_hint"):
        lines.append(f"Gợi ý: {card['template_hint']}")
    if card.get("relevance"):
        lines.append(f"Mức phù hợp: {card['relevance']}")
    if card.get("summary"):
        lines.append(f"Tóm tắt: {card['summary']}")
    steps = card.get("steps") or []
    if steps:
        lines.append("Làm theo:")
        for idx, step in enumerate(steps[:5], start=1):
            lines.append(f"{idx}. {step}")
    if card.get("source_hint"):
        lines.append(f"Nguồn: {card['source_hint']}")
    return "\n".join(lines).strip()


def format_kb_response(results: Iterable[Any], query: str | None = None, *, max_results: int = 3) -> dict[str, Any]:
    results_list = list(results)
    if not results_list:
        return {
            "text": "Mình chưa tìm thấy KB phù hợp. Bạn thử bổ sung từ khóa, hệ thống, môi trường, hoặc mã lỗi nhé.",
            "summary": "",
            "action_items": [],
            "sources": [],
        }

    template_match = KBCategoryTemplateMapper.detect(query)
    cards = [build_kb_card(result, query=query) for result in results_list[:max_results]]
    primary = cards[0]
    source_lines = [f"- {card['title']} ({card.get('source_hint', '')})".strip() for card in cards]
    text_parts = [format_kb_card_text(primary)]
    if len(cards) > 1:
        text_parts.append("Nguồn tham khảo khác:")
        text_parts.extend(source_lines[1:])
    if query:
        text_parts.append(f"Nếu vẫn chưa khớp, thử thêm: {query}")

    return {
        "text": "\n\n".join(part for part in text_parts if part),
        "summary": primary.get("summary", ""),
        "action_items": primary.get("steps", []),
        "sources": cards,
        "template_label": template_match.label if template_match else "",
        "template_hint": template_match.summary_hint if template_match else "",
    }
