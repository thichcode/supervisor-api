#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from src.config import get_settings
from src.db.models import InteractionLog
from src.db.session import async_session


@dataclass
class LoadedEnv:
    path: str
    loaded: int


def load_env_file(path: str) -> LoadedEnv:
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


def normalize(text: str | None) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^\wÀ-ỹ]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def truncate(text: str | None, limit: int = 220) -> str:
    if not text:
        return ""
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


async def gather_top_misses(days: int, limit: int, top_n: int) -> dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(days=days)
    async with async_session() as session:
        q = select(InteractionLog).where(
            InteractionLog.created_at >= cutoff,
            InteractionLog.traffic_class == "service_like",
            (InteractionLog.kb_hit_count == None) | (InteractionLog.kb_hit_count == 0),
        ).order_by(InteractionLog.created_at.desc())
        rows = (await session.execute(q)).scalars().all()

    intent_counts = Counter((row.intent or "unknown") for row in rows)
    pattern_counts = Counter()
    pattern_samples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        pattern = normalize(row.input_text)
        pattern_counts[pattern] += 1
        if len(pattern_samples[pattern]) < 3:
            pattern_samples[pattern].append(
                {
                    "request_id": row.request_id,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "thread_id": row.thread_id,
                    "user_id": row.user_id,
                    "intent": row.intent or "unknown",
                    "confidence_score": row.confidence_score,
                    "kb_hit_count": row.kb_hit_count or 0,
                    "input_text": truncate(row.input_text, limit=260),
                    "output_text": truncate(row.output_text, limit=220),
                }
            )

    top_patterns = []
    for pattern, count in pattern_counts.most_common(top_n):
        top_patterns.append(
            {
                "pattern": pattern,
                "count": count,
                "samples": pattern_samples[pattern],
            }
        )

    return {
        "window_days": days,
        "cutoff": cutoff.isoformat(),
        "service_like_miss_count": len(rows),
        "top_intents": [{"intent": intent, "count": count} for intent, count in intent_counts.most_common(top_n)],
        "top_patterns": top_patterns,
    }


def format_text(report: dict[str, Any]) -> str:
    lines = []
    lines.append(f"Window: last {report['window_days']} day(s)")
    lines.append(f"Cutoff: {report['cutoff']}")
    lines.append(f"Service-like misses: {report['service_like_miss_count']}")
    lines.append("")
    lines.append("Top intents:")
    for item in report["top_intents"]:
        lines.append(f"- {item['intent']}: {item['count']}")
    lines.append("")
    lines.append("Top miss patterns:")
    for idx, item in enumerate(report["top_patterns"], start=1):
        lines.append(f"{idx}. {item['pattern']} ({item['count']})")
        for sample in item["samples"]:
            lines.append(f"   - {sample['created_at']} | intent={sample['intent']} | {sample['input_text']}")
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    if args.env_file:
        load_env_file(args.env_file)

    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("database_url is empty; check DB env vars or --env-file")

    return await gather_top_misses(args.days, args.limit, args.top_n)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect top service-like KB misses from the live DB.")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days (default: 30)")
    parser.add_argument("--limit", type=int, default=3, help="Samples per pattern (default: 3)")
    parser.add_argument("--top-n", type=int, default=15, help="Top patterns/intents to print (default: 15)")
    parser.add_argument("--env-file", type=str, default="", help="Optional .env file to load first")
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format (default: text)")
    args = parser.parse_args()

    report = asyncio.run(main_async(args))
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_text(report))


if __name__ == "__main__":
    main()
