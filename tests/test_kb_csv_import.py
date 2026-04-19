from pathlib import Path

import asyncio
import os
import subprocess
import sys

import pytest


CSV_CONTENT = """knowledge_type,title,content,category,tags,keywords,version,guide_type,document_type,file_url,is_active
faq,VPN access,Use the VPN client from Software Center,access,"vpn;remote","vpn;access",, , , ,true
policy,Work from home policy,WFH is allowed twice per week,policy,"wfh;remote","wfh;remote",2.1,,, ,false
guide,Reset password guide,"1. Open portal\n2. Reset password",howto,"password;reset","password;reset",1.0,howto,,,true
document,CSV handbook,This is an uploaded handbook,docs,"handbook;csv","handbook;csv",, ,pdf,https://example.com/handbook.pdf,true
"""


def test_parse_csv_rows_and_build_models(tmp_path: Path):
    from src.knowledge.csv_import import parse_csv_rows, build_knowledge_record
    from src.db.models import KnowledgeFAQ, KnowledgePolicy, KnowledgeGuide, KnowledgeDocument

    csv_path = tmp_path / "kb.csv"
    csv_path.write_text(CSV_CONTENT, encoding="utf-8")

    rows = list(parse_csv_rows(csv_path))
    assert len(rows) == 4

    faq = build_knowledge_record(rows[0], row_number=1, default_category="general")
    assert isinstance(faq, KnowledgeFAQ)
    assert faq.question == "VPN access"
    assert faq.answer == "Use the VPN client from Software Center"
    assert faq.tags == ["vpn", "remote"]
    assert faq.keywords == ["vpn", "access"]
    assert faq.is_active is True

    policy = build_knowledge_record(rows[1], row_number=2, default_category="general")
    assert isinstance(policy, KnowledgePolicy)
    assert policy.policy_id.startswith("policy_csv_")
    assert policy.version == "2.1"
    assert policy.is_active is False

    guide = build_knowledge_record(rows[2], row_number=3, default_category="general")
    assert isinstance(guide, KnowledgeGuide)
    assert guide.steps == ["1. Open portal", "2. Reset password"]
    assert guide.guide_type == "howto"

    document = build_knowledge_record(rows[3], row_number=4, default_category="general")
    assert isinstance(document, KnowledgeDocument)
    assert document.file_url == "https://example.com/handbook.pdf"
    assert document.document_type == "pdf"
    assert document.is_active is True


def test_normalize_csv_list_handles_separators_and_blank_values():
    from src.knowledge.csv_import import normalize_csv_list

    assert normalize_csv_list("a, b; c | d") == ["a", "b", "c", "d"]
    assert normalize_csv_list("  ") == []
    assert normalize_csv_list(None) == []


def test_script_runs_from_scripts_directory(tmp_path: Path):
    csv_path = tmp_path / "kb.csv"
    csv_path.write_text(CSV_CONTENT, encoding="utf-8")

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)

    result = subprocess.run(
        [sys.executable, "import_kb_csv.py", str(csv_path), "--dry-run"],
        cwd=Path(__file__).resolve().parents[1] / "scripts",
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert '"rows_total"' in result.stdout


def test_import_csv_to_db_initializes_schema_on_dry_run(tmp_path: Path, monkeypatch):
    from src.knowledge.csv_import import import_csv_to_db

    csv_path = tmp_path / "kb.csv"
    csv_path.write_text(CSV_CONTENT, encoding="utf-8")

    called = {"init_db": False}

    async def fake_init_db():
        called["init_db"] = True

    monkeypatch.setattr("src.knowledge.csv_import.init_db", fake_init_db)

    summary = asyncio.run(import_csv_to_db(csv_path, dry_run=True))

    assert called["init_db"] is True
    assert summary["rows_total"] == 4
