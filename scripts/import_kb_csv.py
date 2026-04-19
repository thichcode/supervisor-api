#!/usr/bin/env python3
"""
KB CSV Import with optional Ollama summarization.

Usage:
    python scripts/import_kb_csv.py file.csv --dry-run
    python scripts/import_kb_csv.py file.csv --summarize  # Summarize large content
    python scripts/import_kb_csv.py file.csv --summarize --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.knowledge.csv_import import (
    import_csv_to_db,
    parse_csv_rows,
    build_knowledge_record,
    _infer_kind,
    html_to_plain_text,
)


# LLM client - lazy loaded
_llm_client = None


def _get_llm_client():
    """Lazy load LLM client only when needed."""
    global _llm_client
    if _llm_client is None:
        try:
            from src.llm.provider import MultiProviderLLMClient
            _llm_client = MultiProviderLLMClient()
        except Exception as e:
            print(f"Warning: Cannot load LLM client: {e}")
            return None
    return _llm_client


async def summarize_content(text: str, max_input_chars: int = 4000, max_output_tokens: int = 500) -> str:
    """Summarize content using Ollama. Returns original if summarization fails."""
    if not text or len(text) < 500:
        return text  # Skip short content
    
    client = _get_llm_client()
    if client is None:
        return text[:max_input_chars]  # Fallback: truncate
    
    try:
        # Initialize client if needed
        if hasattr(client, 'initialize'):
            await client.initialize()
        
        # Truncate input to avoid token limits
        input_text = text[:max_input_chars]
        
        response = await client.complete(
            system_prompt="Bạn là assistant tóm tắt văn bản. Tóm tắt ngắn gọn, giữ key points và thông tin quan trọng. Output plain text, không markdown.",
            user_message=f"Tóm tắt nội dung sau đây:\n\n{input_text}",
            max_tokens=max_output_tokens,
        )
        
        if response and response.content:
            return response.content.strip()
    except Exception as e:
        print(f"Warning: Summarization failed: {e}")
    
    # Fallback: return truncated original
    return text[:max_input_chars]


async def _summarize_row_content(row: dict, use_summarize: bool = False) -> dict:
    """Summarize content fields in a row if enabled."""
    if not use_summarize:
        return row
    
    # Fields that may contain large content to summarize
    content_fields = ["content", "description", "body", "answer", "text"]
    
    for field in content_fields:
        if field in row and row[field]:
            original = row[field]
            if len(original) > 500:  # Only summarize if > 500 chars
                print(f"  Summarizing {field} ({len(original)} chars -> ...)", end=" ", flush=True)
                row[field] = await summarize_content(original)
                print(f"({len(row[field])} chars)")
    
    return row


async def import_csv_with_summarize(
    csv_path: str | Path,
    default_category: str = "general",
    default_is_active: bool = True,
    delimiter: str | None = None,
    encoding: str = "utf-8-sig",
    dry_run: bool = False,
    use_summarize: bool = False,
) -> dict:
    """Import CSV with optional summarization."""
    path = Path(csv_path)
    rows = list(parse_csv_rows(path, delimiter=delimiter, encoding=encoding))

    summary: dict = {
        "source_file": str(path),
        "rows_total": len(rows),
        "dry_run": dry_run,
        "use_summarize": use_summarize,
        "created": 0,
        "skipped": 0,
        "summarized": 0,
        "errors": [],
        "kinds": {"policy": 0, "faq": 0, "guide": 0, "document": 0},
    }

    # Process rows with summarization if enabled
    processed_rows = []
    for index, row in enumerate(rows, start=1):
        if use_summarize:
            row = await _summarize_row_content(row, use_summarize=True)
            if any(len(row.get(f, "")) > 500 for f in ["content", "description", "body", "answer", "text"]):
                summary["summarized"] += 1
        processed_rows.append(row)

    if dry_run:
        for index, row in enumerate(processed_rows, start=1):
            model = build_knowledge_record(
                row,
                row_number=index,
                default_category=default_category,
                default_is_active=default_is_active,
                source_name=path.name,
            )
            kind = _infer_kind(row)
            summary["kinds"][kind] += 1
            summary["created"] += 1
            summary.setdefault("preview", []).append({
                "row_number": index,
                "kind": kind,
                "identifier": getattr(
                    model,
                    "policy_id",
                    getattr(model, "question_id", getattr(model, "guide_id", getattr(model, "document_id", ""))),
                ),
            })
        return summary

    from src.db import async_session
    
    async with async_session() as session:
        for index, row in enumerate(processed_rows, start=1):
            kind = _infer_kind(row)
            try:
                model = build_knowledge_record(
                    row,
                    row_number=index,
                    default_category=default_category,
                    default_is_active=default_is_active,
                    source_name=path.name,
                )
                session.add(model)
                await session.commit()
                summary["created"] += 1
                summary["kinds"][kind] += 1
            except Exception as exc:
                await session.rollback()
                summary["skipped"] += 1
                summary["errors"].append({
                    "row_number": index,
                    "kind": kind,
                    "error": str(exc),
                })

    return summary


async def _amain(args: argparse.Namespace) -> int:
    # Increase CSV field limit for large content
    csv.field_size_limit(10_000_000)
    
    summary = await import_csv_with_summarize(
        args.csv_path,
        default_category=args.default_category,
        default_is_active=not args.inactive_by_default,
        delimiter=args.delimiter,
        encoding=args.encoding,
        dry_run=args.dry_run,
        use_summarize=args.summarize,
    )
    
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["errors"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import KB CSV rows into Postgres with optional summarization.")
    parser.add_argument("csv_path", help="Path to the CSV/TSV file")
    parser.add_argument("--default-category", default="general", help="Fallback category for rows without one")
    parser.add_argument("--delimiter", default=None, help="Override CSV delimiter")
    parser.add_argument("--encoding", default="utf-8-sig", help="File encoding (default: utf-8-sig)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and validate without writing to DB")
    parser.add_argument("--inactive-by-default", action="store_true", help="Mark rows inactive unless they explicitly opt in")
    parser.add_argument("--summarize", action="store_true", help="Summarize large content using Ollama before storing")
    
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())