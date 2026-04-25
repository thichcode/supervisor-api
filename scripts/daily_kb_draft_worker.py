#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from src.config import get_settings
from src.services.kb_draft_service import run_daily_kb_draft_job


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate daily KB drafts from service-like misses.")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days (default: 30)")
    parser.add_argument("--top-n", type=int, default=5, help="How many top miss patterns to process (default: 5)")
    parser.add_argument("--min-count", type=int, default=2, help="Minimum miss count per pattern (default: 2)")
    parser.add_argument("--max-rows", type=int, default=4000, help="Maximum interaction rows to scan (default: 4000)")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="reports/kb_drafts",
        help="Directory for JSON report artifacts (default: reports/kb_drafts)",
    )
    parser.add_argument("--env-file", type=str, default="", help="Optional .env file to load before running")
    parser.add_argument("--send-telegram", action="store_true", help="Notify Telegram approval chat(s) if configured")
    parser.add_argument("--no-send-telegram", action="store_true", help="Disable Telegram notification even if configured")
    parser.add_argument("--json", action="store_true", help="Print JSON output instead of text")
    return parser.parse_args()


def maybe_load_env(path: str) -> None:
    if not path:
        return
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
        if key:
            import os

            os.environ[key] = value


async def main_async(args: argparse.Namespace) -> dict:
    settings = get_settings()
    use_telegram = args.send_telegram and not args.no_send_telegram
    report = await run_daily_kb_draft_job(
        days=args.days,
        top_n=args.top_n,
        min_count=args.min_count,
        max_rows=args.max_rows,
        telegram_bot_token=settings.telegram_bot_token if use_telegram else "",
        telegram_chat_ids=settings.telegram_approval_chat_ids if use_telegram else "",
    )
    return report


def main() -> None:
    args = parse_args()
    if args.env_file:
        maybe_load_env(args.env_file)

    report = asyncio.run(main_async(args))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "latest-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print(f"Window: {report['window_days']} day(s)")
    print(f"Top patterns scanned: {report['miss_patterns_found']}")
    print(f"Drafts created: {len(report['drafts_created'])}")
    for item in report["drafts_created"]:
        print(f"- {item['candidate_id']} | {item['title']} | {item['confidence']:.2f} | {item['category']}")


if __name__ == "__main__":
    main()
