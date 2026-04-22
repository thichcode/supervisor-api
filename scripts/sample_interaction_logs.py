#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.config import get_settings
from src.db.models import ApprovalRequestRecord, InteractionLog


@dataclass
class LoadedEnv:
    path: str
    loaded: int


def _parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_env_file(path: str) -> LoadedEnv:
    """Load a simple KEY=VALUE .env file into os.environ.

    This is intentionally tiny and dependency-free so the script can run on
    the deployed host even if python-dotenv is unavailable.
    """
    count = 0
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"env file not found: {path}")

    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        os.environ[key] = value
        count += 1
    return LoadedEnv(path=path, loaded=count)


def truncate(text: str | None, limit: int = 180) -> str:
    if not text:
        return ""
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


def to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def fetch_counts(session, cutoff, limit: int):
    total_q = select(func.count()).select_from(InteractionLog).where(InteractionLog.created_at >= cutoff)
    kb_q = select(func.count()).select_from(InteractionLog).where(
        InteractionLog.created_at >= cutoff,
        InteractionLog.kb_hit_count > 0,
    )
    skip_q = select(func.count()).select_from(InteractionLog).where(
        InteractionLog.created_at >= cutoff,
        InteractionLog.outcome_status == "skipped",
    )
    completed_q = select(func.count()).select_from(InteractionLog).where(
        InteractionLog.created_at >= cutoff,
        InteractionLog.outcome_status == "completed",
    )
    review_q = select(func.count()).select_from(InteractionLog).where(
        InteractionLog.created_at >= cutoff,
        InteractionLog.outcome_status == "needs_review",
    )
    intent_q = (
        select(InteractionLog.intent, func.count().label("count"))
        .where(InteractionLog.created_at >= cutoff)
        .group_by(InteractionLog.intent)
        .order_by(func.count().desc())
        .limit(10)
    )
    approvals_q = select(
        func.count().filter(ApprovalRequestRecord.status == "pending"),
        func.count().filter(ApprovalRequestRecord.status == "approved"),
        func.count().filter(ApprovalRequestRecord.status == "rejected"),
    ).where(ApprovalRequestRecord.created_at >= cutoff)

    sample_q = (
        select(InteractionLog)
        .where(InteractionLog.created_at >= cutoff)
        .order_by(InteractionLog.created_at.desc())
        .limit(limit)
    )

    total = (await session.execute(total_q)).scalar_one()
    kb_hits = (await session.execute(kb_q)).scalar_one()
    skipped = (await session.execute(skip_q)).scalar_one()
    completed = (await session.execute(completed_q)).scalar_one()
    needs_review = (await session.execute(review_q)).scalar_one()
    top_intents = (await session.execute(intent_q)).all()
    approvals_row = (await session.execute(approvals_q)).one()
    samples = (await session.execute(sample_q)).scalars().all()

    confidences = [row.confidence_score for row in samples if row.confidence_score is not None]
    latencies = [row.processing_latency_ms for row in samples if row.processing_latency_ms is not None]
    intent_counter = Counter((row.intent or "unknown") for row in samples)

    return {
        "counts": {
            "total": total,
            "kb_hits": kb_hits,
            "skipped": skipped,
            "completed": completed,
            "needs_review": needs_review,
            "kb_hit_rate": round((kb_hits / total * 100), 1) if total else 0.0,
            "skip_rate": round((skipped / total * 100), 1) if total else 0.0,
            "auto_send_rate": round((completed / total * 100), 1) if total else 0.0,
            "avg_confidence_sample": round((sum(confidences) / len(confidences) * 100), 1) if confidences else 0.0,
            "avg_latency_ms_sample": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        },
        "approvals": {
            "pending": int(approvals_row[0] or 0),
            "approved": int(approvals_row[1] or 0),
            "rejected": int(approvals_row[2] or 0),
        },
        "top_intents": [{"intent": intent or "unknown", "count": int(count)} for intent, count in top_intents],
        "samples": [
            {
                "request_id": row.request_id,
                "created_at": to_iso(row.created_at),
                "thread_id": row.thread_id,
                "user_id": row.user_id,
                "intent": row.intent or "unknown",
                "risk_level": row.risk_level,
                "confidence_score": row.confidence_score,
                "outcome_status": row.outcome_status,
                "approval_required": bool(row.approval_required),
                "kb_hit_count": row.kb_hit_count or 0,
                "input_text": truncate(row.input_text, 220),
                "output_text": truncate(row.output_text, 220),
                "extra_metadata": {
                    "platform": (row.extra_metadata or {}).get("platform"),
                    "chat_type": (row.extra_metadata or {}).get("chat_type"),
                    "chat_scope": (row.extra_metadata or {}).get("chat_scope"),
                    "group_chat": (row.extra_metadata or {}).get("group_chat"),
                    "agents_used": (row.extra_metadata or {}).get("agents_used", []),
                    "pattern_hit": (row.extra_metadata or {}).get("pattern_hit"),
                    "cache_hit": (row.extra_metadata or {}).get("cache_hit"),
                },
            }
            for row in samples
        ],
    }


async def main_async(args):
    if args.env_file:
        load_env_file(args.env_file)

    settings = get_settings()
    db_url = settings.database_url
    if not db_url:
        raise RuntimeError("database_url is empty; check DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD env vars")

    engine = create_async_engine(db_url, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    try:
        async with Session() as session:
            payload = await fetch_counts(session, cutoff, args.limit)
            payload["window_days"] = args.days
            payload["cutoff"] = cutoff.isoformat()
            payload["database_host"] = settings.db_host
            payload["database_name"] = settings.db_name
            payload["sample_limit"] = args.limit

        return payload
    finally:
        await engine.dispose()


def format_text(report: dict[str, Any]) -> str:
    lines = []
    lines.append(f"Window: last {report['window_days']} day(s)")
    lines.append(f"Cutoff: {report['cutoff']}")
    lines.append("")
    counts = report["counts"]
    approvals = report["approvals"]
    lines.append(f"Total interactions: {counts['total']}")
    lines.append(f"KB hit rate: {counts['kb_hit_rate']}%")
    lines.append(f"Skip rate: {counts['skip_rate']}%")
    lines.append(f"Auto-send rate: {counts['auto_send_rate']}%")
    lines.append(f"Avg confidence (sample): {counts['avg_confidence_sample']}%")
    lines.append(f"Avg latency (sample): {counts['avg_latency_ms_sample']} ms")
    lines.append(
        f"Approvals: pending={approvals['pending']}, approved={approvals['approved']}, rejected={approvals['rejected']}"
    )
    lines.append("")
    lines.append("Top intents:")
    for item in report["top_intents"]:
        lines.append(f"- {item['intent']}: {item['count']}")
    lines.append("")
    lines.append("Samples:")
    for idx, row in enumerate(report["samples"], start=1):
        lines.append(
            f"{idx}. {row['created_at']} | {row['request_id']} | user={row['user_id']} | thread={row['thread_id']} | intent={row['intent']} | conf={row['confidence_score']} | outcome={row['outcome_status']} | kb={row['kb_hit_count']}"
        )
        if row.get("input_text"):
            lines.append(f"   IN: {row['input_text']}")
        if row.get("output_text"):
            lines.append(f"   OUT: {row['output_text']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Sample InteractionLog data from deployed supervisor-api DB")
    parser.add_argument("--days", type=int, default=1, help="Lookback window in days")
    parser.add_argument("--limit", type=int, default=20, help="Number of sample rows to print")
    parser.add_argument("--env-file", type=str, default="", help="Optional .env file path to load first")
    parser.add_argument("--output", type=str, default="", help="Optional output file path (JSON or text based on --format)")
    parser.add_argument("--format", choices=["json", "text"], default="json", help="Output format")
    args = parser.parse_args()

    report = asyncio.run(main_async(args))
    if args.format == "text":
        output = format_text(report)
    else:
        output = json.dumps(report, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
