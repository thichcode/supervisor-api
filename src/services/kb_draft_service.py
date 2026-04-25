from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Optional

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.cli_tools import fetch_url, web_search
from src.config import get_settings
from src.db import async_session
from src.db.models import InteractionLog, KnowledgeCandidate
from src.llm import llm_client

logger = structlog.get_logger()


@dataclass
class MissSample:
    request_id: str
    created_at: str | None
    thread_id: str
    user_id: str
    intent: str
    confidence_score: float | None
    kb_hit_count: int
    input_text: str
    output_text: str


@dataclass
class MissPattern:
    pattern: str
    normalized_pattern: str
    count: int
    samples: list[MissSample]


@dataclass
class DraftResult:
    candidate_id: str
    source_request_id: str
    title: str
    category: str
    tags: list[str]
    summary: str
    kb_content: str
    questions_for_user: list[str]
    confidence: float
    rationale: str
    sources: list[dict[str, Any]]
    pattern: str
    miss_count: int
    status: str = "pending_review"


def normalize_text(text: str | None) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"[^\wÀ-ỹ]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def compact(text: str | None, limit: int = 280) -> str:
    if not text:
        return ""
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


def candidate_source_request_id(candidate_id: str) -> str:
    candidate_id = (candidate_id or "").strip()
    if candidate_id.startswith("kb-draft-"):
        suffix = candidate_id.removeprefix("kb-draft-")
        return f"daily-kb-draft:{suffix}"
    return candidate_id


def candidate_id_from_source_request_id(source_request_id: str) -> str:
    source_request_id = (source_request_id or "").strip()
    if source_request_id.startswith("daily-kb-draft:"):
        suffix = source_request_id.split(":", 1)[1]
        return f"kb-draft-{suffix}"
    return source_request_id


def safe_json_loads(text: str) -> dict[str, Any]:
    candidate = (text or "").strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        return json.loads(candidate)
    except Exception:
        match = re.search(r"\{.*\}", candidate, re.S)
        if match:
            return json.loads(match.group(0))
        raise


class KBDraftService:
    """Nightly KB draft generator for top service-like misses."""

    def __init__(self, session: AsyncSession, telegram_bot_token: str = "", telegram_chat_ids: str = ""):
        self.session = session
        self.telegram_bot_token = telegram_bot_token.strip()
        self.telegram_chat_ids = [chat.strip() for chat in telegram_chat_ids.split(",") if chat.strip()]

    async def collect_top_miss_patterns(
        self,
        days: int = 30,
        top_n: int = 5,
        min_count: int = 2,
        max_rows: int = 4000,
    ) -> list[MissPattern]:
        cutoff = datetime.utcnow() - timedelta(days=days)
        query = (
            select(InteractionLog)
            .where(
                InteractionLog.created_at >= cutoff,
                InteractionLog.traffic_class == "service_like",
                (InteractionLog.kb_hit_count == None) | (InteractionLog.kb_hit_count == 0),
            )
            .order_by(InteractionLog.created_at.desc())
            .limit(max_rows)
        )
        rows = (await self.session.execute(query)).scalars().all()

        buckets: dict[str, list[InteractionLog]] = {}
        for row in rows:
            normalized = normalize_text(row.input_text)
            if not normalized:
                continue
            buckets.setdefault(normalized, []).append(row)

        patterns: list[MissPattern] = []
        for normalized, items in sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True):
            if len(items) < min_count:
                continue
            samples = [
                MissSample(
                    request_id=item.request_id,
                    created_at=item.created_at.isoformat() if item.created_at else None,
                    thread_id=item.thread_id,
                    user_id=item.user_id,
                    intent=item.intent or "unknown",
                    confidence_score=item.confidence_score,
                    kb_hit_count=item.kb_hit_count or 0,
                    input_text=compact(item.input_text, 220),
                    output_text=compact(item.output_text, 220),
                )
                for item in items[:3]
            ]
            patterns.append(
                MissPattern(
                    pattern=items[0].input_text,
                    normalized_pattern=normalized,
                    count=len(items),
                    samples=samples,
                )
            )
            if len(patterns) >= top_n:
                break

        return patterns

    async def build_draft(self, miss: MissPattern) -> DraftResult | None:
        pattern_hash = sha256(miss.normalized_pattern.encode("utf-8")).hexdigest()[:12]
        candidate_id = f"kb-draft-{pattern_hash}"
        source_request_id = f"daily-kb-draft:{pattern_hash}"

        existing_result = await self.session.execute(
            select(KnowledgeCandidate).where(KnowledgeCandidate.source_request_id == source_request_id)
        )
        existing = existing_result.scalar_one_or_none()
        if existing and (existing.status or "").lower() in {"promoted", "approved", "rejected"}:
            logger.info("kb_draft_skip_existing_finalized", candidate_id=candidate_id, status=existing.status)
            return None

        web_evidence = await self._collect_web_evidence(miss.pattern)
        llm_result = await self._infer_draft_from_evidence(miss, web_evidence)
        if not llm_result:
            return None

        draft = DraftResult(
            candidate_id=candidate_id,
            source_request_id=source_request_id,
            title=llm_result.get("title") or miss.pattern[:120],
            category=llm_result.get("category") or self._guess_category(miss.pattern),
            tags=self._normalize_tags(llm_result.get("tags") or []),
            summary=llm_result.get("summary") or compact(miss.pattern, 220),
            kb_content=llm_result.get("kb_content") or llm_result.get("content") or compact(miss.pattern, 400),
            questions_for_user=llm_result.get("questions_for_user") or [],
            confidence=float(llm_result.get("confidence") or 0.5),
            rationale=llm_result.get("rationale") or "",
            sources=web_evidence,
            pattern=miss.pattern,
            miss_count=miss.count,
        )
        await self._upsert_candidate(draft)
        await self._write_artifacts(draft)
        await self._notify_telegram(draft)
        return draft

    async def get_candidate_by_id(self, candidate_id_or_source_id: str) -> KnowledgeCandidate | None:
        lookup_ids = {candidate_id_or_source_id, candidate_source_request_id(candidate_id_or_source_id)}
        result = await self.session.execute(
            select(KnowledgeCandidate).where(KnowledgeCandidate.source_request_id.in_(lookup_ids))
        )
        return result.scalar_one_or_none()

    async def review_candidate(
        self,
        candidate_id_or_source_id: str,
        action: str,
        reviewer_id: str,
        note: str | None = None,
    ) -> KnowledgeCandidate | None:
        candidate = await self.get_candidate_by_id(candidate_id_or_source_id)
        if not candidate:
            return None

        action = (action or "").strip().lower()
        now = datetime.utcnow()
        candidate.reviewer_id = reviewer_id or candidate.reviewer_id
        candidate.review_note = note or candidate.review_note
        candidate.reviewed_at = now
        if action == "approve":
            candidate.status = "approved"
            candidate.promoted_at = now
        elif action == "revise":
            candidate.status = "needs_revision"
            candidate.promoted_at = None
        else:
            raise ValueError(f"Unsupported review action: {action}")

        await self.session.commit()
        await self.session.refresh(candidate)
        logger.info(
            "kb_candidate_reviewed",
            candidate_id=candidate_id_from_source_request_id(candidate.source_request_id),
            action=action,
            reviewer_id=reviewer_id,
        )
        return candidate

    async def _collect_web_evidence(self, pattern: str) -> list[dict[str, Any]]:
        queries = [pattern]
        normalized = normalize_text(pattern)
        if normalized and normalized != pattern.lower():
            queries.append(f'"{pattern}"')
        if len(pattern.split()) <= 6:
            queries.append(f"{pattern} IT support")
            queries.append(f"{pattern} how to fix")

        seen_urls: set[str] = set()
        evidence: list[dict[str, Any]] = []
        for query in queries[:4]:
            search_result = await asyncio.to_thread(web_search, query, 5)
            for item in search_result.get("results", []) or []:
                url = str(item.get("url", "")).strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                snippet = compact(item.get("snippet") or "", 260)
                title = compact(item.get("title") or "", 160)
                fetched = await asyncio.to_thread(fetch_url, url, 20)
                fetched_content = compact(fetched.get("content") or "", 320)
                evidence.append(
                    {
                        "query": query,
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "fetched_excerpt": fetched_content,
                        "content_type": fetched.get("content_type", ""),
                    }
                )
                if len(evidence) >= 6:
                    return evidence
        return evidence

    async def _infer_draft_from_evidence(self, miss: MissPattern, evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not evidence:
            logger.warning("kb_draft_no_evidence", pattern=miss.pattern)
            return None

        evidence_lines = []
        for idx, item in enumerate(evidence[:6], start=1):
            evidence_lines.append(
                f"[{idx}] {item['title']}\nURL: {item['url']}\nSnippet: {item['snippet']}\nFetched: {item['fetched_excerpt']}"
            )
        sample_lines = []
        for idx, sample in enumerate(miss.samples, start=1):
            sample_lines.append(
                f"Sample {idx}: intent={sample.intent}, confidence={sample.confidence_score}, text={sample.input_text}"
            )

        system_prompt = """
Bạn là KB writer cho supervisor-api.
Nhiệm vụ: từ các pattern miss service-like và evidence web, viết một draft KB ngắn, thực dụng, tiếng Việt.

Yêu cầu:
- Không bịa chi tiết nếu evidence không đủ.
- Ưu tiên nội dung có thể chuyển thành FAQ/guide nội bộ.
- Nếu còn thiếu thông tin, đưa vào questions_for_user để xin bổ sung.
- Trả về CHỈ JSON hợp lệ, không markdown, không code block.

Schema JSON:
{
  "title": "...",
  "category": "faq|guide|policy|document",
  "tags": ["..."],
  "summary": "...",
  "kb_content": "...",
  "questions_for_user": ["..."],
  "confidence": 0.0,
  "rationale": "..."
}
""".strip()

        user_message = f"""
Top miss pattern:
{miss.pattern}

Normalized pattern:
{miss.normalized_pattern}

Miss count: {miss.count}

Sample service-like queries:
{chr(10).join(sample_lines)}

Web evidence:
{chr(10).join(evidence_lines)}

Hãy viết draft KB theo schema JSON ở trên.
""".strip()

        try:
            response = await llm_client.complete(system_prompt=system_prompt, user_message=user_message, temperature=0.2, max_tokens=1600)
            return safe_json_loads(response.content)
        except Exception as exc:
            logger.warning("kb_draft_llm_failed", error=str(exc), pattern=miss.pattern)
            fallback = self._fallback_draft(miss, evidence)
            return fallback

    def _fallback_draft(self, miss: MissPattern, evidence: list[dict[str, Any]]) -> dict[str, Any]:
        first = evidence[0] if evidence else {}
        title = miss.pattern[:120]
        category = self._guess_category(miss.pattern)
        questions = ["Cần thêm ngữ cảnh cụ thể nào để khớp KB tốt hơn?"]
        return {
            "title": title,
            "category": category,
            "tags": self._normalize_tags([category, "daily-draft"]),
            "summary": compact(first.get("snippet") or miss.pattern, 180),
            "kb_content": self._build_kb_content(miss, evidence),
            "questions_for_user": questions,
            "confidence": 0.35,
            "rationale": "LLM unavailable, generated from web snippets and miss patterns",
        }

    def _build_kb_content(self, miss: MissPattern, evidence: list[dict[str, Any]]) -> str:
        lines = [
            f"# {miss.pattern}",
            "",
            "## Dấu hiệu",
            f"- Query service-like miss count: {miss.count}",
        ]
        for sample in miss.samples[:3]:
            lines.append(f"- {sample.input_text}")
        lines.append("")
        lines.append("## Gợi ý xử lý")
        lines.append("- Xem evidence web bên dưới để xác nhận bước xử lý đúng với bối cảnh nội bộ.")
        lines.append("- Bổ sung các điều kiện cụ thể trước khi publish KB.")
        lines.append("")
        lines.append("## Evidence")
        for idx, item in enumerate(evidence[:4], start=1):
            lines.append(f"{idx}. {item['title']} - {item['url']}")
            if item.get("snippet"):
                lines.append(f"   {item['snippet']}")
        return "\n".join(lines)

    def _guess_category(self, pattern: str) -> str:
        text = normalize_text(pattern)
        if any(word in text for word in ["policy", "quy định", "chính sách", "approval", "request"]):
            return "policy"
        if any(word in text for word in ["how", "cách", "làm sao", "guide", "hướng dẫn"]):
            return "guide"
        if any(word in text for word in ["document", "file", "sharepoint", "onedrive", "csv", "excel"]):
            return "document"
        return "faq"

    def _normalize_tags(self, tags: Any) -> list[str]:
        if isinstance(tags, str):
            tags = [tags]
        normalized: list[str] = []
        for tag in tags or []:
            cleaned = normalize_text(str(tag)).replace(" ", "_")
            if cleaned and cleaned not in normalized:
                normalized.append(cleaned)
        return normalized[:8]

    async def _upsert_candidate(self, draft: DraftResult) -> KnowledgeCandidate:
        existing_result = await self.session.execute(
            select(KnowledgeCandidate).where(KnowledgeCandidate.source_request_id == draft.source_request_id)
        )
        existing = existing_result.scalar_one_or_none()
        review_note = json.dumps(
            {
                "pattern": draft.pattern,
                "miss_count": draft.miss_count,
                "summary": draft.summary,
                "questions_for_user": draft.questions_for_user,
                "sources": draft.sources,
                "rationale": draft.rationale,
            },
            ensure_ascii=False,
            indent=2,
        )

        if existing:
            existing.extracted_title = draft.title
            existing.extracted_content = draft.kb_content
            existing.category = draft.category
            existing.tags = draft.tags
            existing.confidence_score = draft.confidence
            existing.status = draft.status
            existing.review_note = review_note
            candidate = existing
            logger.info("kb_candidate_updated", candidate_id=draft.candidate_id, title=draft.title)
        else:
            candidate = KnowledgeCandidate(
                source_request_id=draft.source_request_id,
                source_thread_id="daily_kb_draft",
                ticket_id=None,
                ticket_system="supervisor-api",
                extracted_title=draft.title,
                extracted_content=draft.kb_content,
                category=draft.category,
                tags=draft.tags,
                confidence_score=draft.confidence,
                status=draft.status,
                review_note=review_note,
            )
            self.session.add(candidate)
            logger.info("kb_candidate_created", candidate_id=draft.candidate_id, title=draft.title)

        await self.session.commit()
        await self.session.refresh(candidate)
        return candidate

    async def _write_artifacts(self, draft: DraftResult) -> Path:
        output_dir = Path("reports/kb_drafts")
        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        path = output_dir / f"{stamp}-{draft.candidate_id}.json"
        payload = asdict(draft)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    async def _notify_telegram(self, draft: DraftResult) -> None:
        if not self.telegram_bot_token or not self.telegram_chat_ids:
            return

        message_lines = [
            "KB draft ready",
            f"Candidate: {draft.candidate_id}",
            f"Title: {draft.title}",
            f"Category: {draft.category}",
            f"Confidence: {draft.confidence:.2f}",
            f"Miss count: {draft.miss_count}",
            "",
            "Summary:",
            draft.summary,
            "",
            "Questions for you:",
        ]
        if draft.questions_for_user:
            message_lines.extend([f"- {item}" for item in draft.questions_for_user[:5]])
        else:
            message_lines.append("- No additional questions from the draft.")
        message_lines.extend(
            [
                "",
                "Next action:",
                f"Tap Approve / Revise below, or reply with: APPROVE {draft.candidate_id} / REVISE {draft.candidate_id}: ...",
                "",
                "Top sources:",
            ]
        )
        for idx, item in enumerate(draft.sources[:3], start=1):
            message_lines.append(f"{idx}. {item['title']} - {item['url']}")
        text = "\n".join(message_lines)

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": f"kb_candidate:approve:{draft.candidate_id}"},
                    {"text": "📝 Revise", "callback_data": f"kb_candidate:revise:{draft.candidate_id}"},
                ]
            ]
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            for chat_id in self.telegram_chat_ids:
                try:
                    await client.post(
                        f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage",
                        json={
                            "chat_id": chat_id,
                            "text": text[:3800],
                            "disable_web_page_preview": True,
                            "reply_markup": reply_markup,
                        },
                    )
                except Exception as exc:
                    logger.warning("kb_draft_telegram_notify_failed", chat_id=chat_id, error=str(exc))

    async def run(self, days: int = 30, top_n: int = 5, min_count: int = 2, max_rows: int = 4000) -> dict[str, Any]:
        patterns = await self.collect_top_miss_patterns(days=days, top_n=top_n, min_count=min_count, max_rows=max_rows)
        drafts: list[dict[str, Any]] = []
        for miss in patterns:
            draft = await self.build_draft(miss)
            if not draft:
                continue
            drafts.append(
                {
                    "candidate_id": draft.candidate_id,
                    "source_request_id": draft.source_request_id,
                    "title": draft.title,
                    "category": draft.category,
                    "tags": draft.tags,
                    "summary": draft.summary,
                    "confidence": draft.confidence,
                    "status": draft.status,
                    "pattern": draft.pattern,
                    "miss_count": draft.miss_count,
                }
            )

        return {
            "window_days": days,
            "top_n": top_n,
            "min_count": min_count,
            "miss_patterns_found": len(patterns),
            "drafts_created": drafts,
        }


async def run_daily_kb_draft_job(
    days: int = 30,
    top_n: int = 5,
    min_count: int = 2,
    max_rows: int = 4000,
    telegram_bot_token: str | None = None,
    telegram_chat_ids: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    bot_token = settings.telegram_bot_token if telegram_bot_token is None else telegram_bot_token
    chat_ids = settings.telegram_approval_chat_ids if telegram_chat_ids is None else telegram_chat_ids
    async with async_session() as session:
        service = KBDraftService(
            session=session,
            telegram_bot_token=bot_token,
            telegram_chat_ids=chat_ids,
        )
        return await service.run(days=days, top_n=top_n, min_count=min_count, max_rows=max_rows)


async def review_kb_candidate(
    candidate_id_or_source_id: str,
    action: str,
    reviewer_id: str,
    note: str | None = None,
) -> dict[str, Any] | None:
    async with async_session() as session:
        service = KBDraftService(session=session)
        candidate = await service.review_candidate(
            candidate_id_or_source_id=candidate_id_or_source_id,
            action=action,
            reviewer_id=reviewer_id,
            note=note,
        )
        if not candidate:
            return None
        return {
            "id": candidate.id,
            "candidate_id": candidate_id_from_source_request_id(candidate.source_request_id),
            "source_request_id": candidate.source_request_id,
            "title": candidate.extracted_title,
            "content": candidate.extracted_content,
            "category": candidate.category,
            "tags": candidate.tags or [],
            "confidence_score": candidate.confidence_score,
            "status": candidate.status,
            "reviewer_id": candidate.reviewer_id,
            "review_note": candidate.review_note,
            "reviewed_at": candidate.reviewed_at.isoformat() if candidate.reviewed_at else None,
            "promoted_at": candidate.promoted_at.isoformat() if candidate.promoted_at else None,
        }
