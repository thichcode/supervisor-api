from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import structlog
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from src.db import async_session, init_db
from src.db.models import KnowledgeDocument, KnowledgeFAQ, KnowledgeGuide, KnowledgePolicy

logger = structlog.get_logger(__name__)

_KNOWN_KINDS = {"policy", "faq", "guide", "document"}
_LIST_SPLIT_RE = re.compile(r"[;,|\n\r]+")
_BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "header",
    "footer",
    "main",
    "aside",
    "blockquote",
    "pre",
    "li",
    "tr",
    "table",
    "ul",
    "ol",
    "br",
    "hr",
}


class _PlainTextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0
        self._last_was_space = False

    def handle_starttag(self, tag: str, attrs):  # noqa: D401
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self._append_newline()

    def handle_endtag(self, tag: str):
        if tag in {"script", "style"}:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in _BLOCK_TAGS:
            self._append_newline()

    def handle_data(self, data: str):
        if self._skip_depth > 0:
            return
        text = re.sub(r"\s+", " ", data)
        if not text.strip():
            return
        if self._parts and not self._last_was_space and not self._parts[-1].endswith(("\n", " ")):
            self._parts.append(" ")
        self._parts.append(text.strip())
        self._last_was_space = False

    def get_text(self) -> str:
        text = "".join(self._parts)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\s+([.,;:!?%])", r"\1", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _append_newline(self) -> None:
        if not self._parts:
            return
        if not self._parts[-1].endswith("\n"):
            self._parts.append("\n")
        self._last_was_space = True
def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def html_to_plain_text(value: Any) -> str:
    """Convert HTML content to normalized plain text."""
    text = _clean_text(value)
    if not text:
        return ""
    if "<" not in text or ">" not in text:
        return text

    parser = _PlainTextHTMLParser()
    parser.feed(text)
    parser.close()
    return parser.get_text()


def normalize_csv_list(value: Any) -> list[str]:
    """Normalize a CSV cell into a list of strings.

    Accepts comma/semicolon/pipe/newline-separated text, JSON arrays, or iterables.
    """
    if value is None:
        return []

    if isinstance(value, (list, tuple, set)):
        raw_items: Iterable[Any] = value
        return [item for item in (_clean_text(item) for item in raw_items) if item]

    text = _clean_text(value)
    if not text:
        return []

    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        else:
            if isinstance(parsed, list):
                return [item for item in (_clean_text(item) for item in parsed) if item]

    return [item for item in (_clean_text(item) for item in _LIST_SPLIT_RE.split(text)) if item]


def parse_csv_rows(csv_path: str | Path, delimiter: str | None = None, encoding: str = "utf-8-sig"):
    """Yield normalized rows from a CSV/TSV file."""
    path = Path(csv_path)
    with path.open("r", encoding=encoding, newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)

        resolved_delimiter = delimiter
        if resolved_delimiter is None:
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                resolved_delimiter = dialect.delimiter
            except csv.Error:
                resolved_delimiter = "\t" if path.suffix.lower() == ".tsv" else ","

        reader = csv.DictReader(handle, delimiter=resolved_delimiter)
        for row in reader:
            normalized: dict[str, str] = {}
            for key, value in row.items():
                if key is None:
                    continue
                normalized[key.strip().lower()] = _clean_text(value)
            if any(normalized.values()):
                yield normalized


def _pick(row: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = row.get(key)
        text = _clean_text(value)
        if text:
            return text
    return default


def _parse_bool(value: Any, default: bool = True) -> bool:
    text = _clean_text(value).lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on", "active", "enabled", "published"}:
        return True
    if text in {"0", "false", "no", "n", "off", "inactive", "disabled", "draft", "archived"}:
        return False
    return default


def _infer_kind(row: dict[str, Any], default_kind: str = "document") -> str:
    kind = _pick(row, "knowledge_type", "type", "kind", "record_type", default=default_kind).lower()
    if kind not in _KNOWN_KINDS:
        return default_kind
    return kind


def _row_base_metadata(row: dict[str, Any], row_number: int, source_name: str | None) -> dict[str, Any]:
    return {
        "source_row": row_number,
        "source_file": source_name,
        "raw_record": row,
    }


def build_knowledge_record(
    row: dict[str, Any],
    row_number: int,
    default_category: str = "general",
    default_is_active: bool = True,
    source_name: str | None = None,
):
    """Build a SQLAlchemy knowledge object from a normalized CSV row."""
    kind = _infer_kind(row)
    category = _pick(row, "category", default=default_category)
    is_active = _parse_bool(_pick(row, "is_active", "active", "enabled"), default=default_is_active)

    if kind == "policy":
        policy_id = _pick(row, "policy_id", "id", "record_id", default=f"policy_csv_{row_number}")
        title = _pick(row, "title", "name", "question", default=policy_id)
        content = html_to_plain_text(_pick(row, "content", "body", "answer", "text"))
        if not content:
            content = title
        return KnowledgePolicy(
            policy_id=policy_id,
            title=title,
            content=content,
            category=category,
            tags=normalize_csv_list(_pick(row, "tags")),
            version=_pick(row, "version", default="1.0"),
            is_active=is_active,
        )

    if kind == "faq":
        question_id = _pick(row, "question_id", "faq_id", "id", "record_id", default=f"faq_csv_{row_number}")
        question = _pick(row, "question", "title", "name", default=question_id)
        answer = html_to_plain_text(_pick(row, "answer", "content", "body", "text"))
        if not answer:
            answer = question
        return KnowledgeFAQ(
            question_id=question_id,
            question=question,
            answer=answer,
            category=category,
            tags=normalize_csv_list(_pick(row, "tags")),
            keywords=normalize_csv_list(_pick(row, "keywords", "keyword")),
            is_active=is_active,
        )

    if kind == "guide":
        guide_id = _pick(row, "guide_id", "id", "record_id", default=f"guide_csv_{row_number}")
        title = _pick(row, "title", "name", "question", default=guide_id)
        content = html_to_plain_text(_pick(row, "content", "body", "answer", "text"))
        if not content:
            content = title
        guide_type = _pick(row, "guide_type", "type", default="document")
        steps = normalize_csv_list(_pick(row, "steps", "step_list", "procedure_steps"))
        if not steps and content:
            steps = [line.strip() for line in content.splitlines() if line.strip()]
        return KnowledgeGuide(
            guide_id=guide_id,
            title=title,
            content=content,
            guide_type=guide_type,
            category=category,
            tags=normalize_csv_list(_pick(row, "tags")),
            steps=steps,
            is_active=is_active,
        )

    document_id = _pick(
        row,
        "document_id",
        "doc_id",
        "solution_id",
        "id",
        "record_id",
        default=f"doc_csv_{row_number}",
    )
    title = _pick(row, "title", "subject", "name", "question", default=document_id)
    content = html_to_plain_text(_pick(row, "content", "description", "body", "answer", "text"))
    if not content:
        content = title
    document_type = _pick(row, "document_type", "file_type", "extension", default="document")
    file_url = _pick(row, "file_url", "url", "source_url") or None
    extra_metadata = {
        **_row_base_metadata(row, row_number, source_name),
        "total_view": _pick(row, "total_view"),
        "created_time": _pick(row, "created_time", "created_at"),
        "created_by": _pick(row, "created_by"),
        "last_updated_by": _pick(row, "last_updated_by", "updated_by"),
    }
    return KnowledgeDocument(
        document_id=document_id,
        title=title,
        content=content,
        document_type=document_type,
        category=category,
        tags=normalize_csv_list(_pick(row, "tags", "keyword", "keywords")),
        file_url=file_url,
        extra_metadata=extra_metadata,
        is_active=is_active,
    )


async def import_csv_to_db(
    csv_path: str | Path,
    default_category: str = "general",
    default_is_active: bool = True,
    delimiter: str | None = None,
    encoding: str = "utf-8-sig",
    dry_run: bool = False,
) -> dict[str, Any]:
    await init_db()
    path = Path(csv_path)
    rows = list(parse_csv_rows(path, delimiter=delimiter, encoding=encoding))

    summary: dict[str, Any] = {
        "source_file": str(path),
        "rows_total": len(rows),
        "dry_run": dry_run,
        "created": 0,
        "skipped": 0,
        "errors": [],
        "kinds": {"policy": 0, "faq": 0, "guide": 0, "document": 0},
    }

    if dry_run:
        for index, row in enumerate(rows, start=1):
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

    async with async_session() as session:
        for index, row in enumerate(rows, start=1):
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
            except Exception as exc:  # pragma: no cover - error path is reported in summary
                await session.rollback()
                summary["skipped"] += 1
                summary["errors"].append({
                    "row_number": index,
                    "kind": kind,
                    "error": str(exc),
                })

    return summary


async def _amain(args: argparse.Namespace) -> int:
    summary = await import_csv_to_db(
        args.csv_path,
        default_category=args.default_category,
        default_is_active=not args.inactive_by_default,
        delimiter=args.delimiter,
        encoding=args.encoding,
        dry_run=args.dry_run,
    )
    logger.info("Import summary", summary=summary)
    return 0 if not summary["errors"] else 1


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import KB CSV rows into Postgres.")
    parser.add_argument("csv_path", help="Path to the CSV/TSV file")
    parser.add_argument("--default-category", default="general", help="Fallback category for rows without one")
    parser.add_argument("--delimiter", default=None, help="Override CSV delimiter")
    parser.add_argument("--encoding", default="utf-8-sig", help="File encoding (default: utf-8-sig)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and validate without writing to DB")
    parser.add_argument(
        "--inactive-by-default",
        action="store_true",
        help="Mark rows inactive unless they explicitly opt in",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
