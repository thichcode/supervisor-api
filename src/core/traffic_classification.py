from __future__ import annotations

from typing import Any

SERVICE_INTENTS = {
    "faq",
    "policy",
    "guide_request",
    "support_case",
    "system_query",
    "analysis",
    "executive_request",
    "incident",
    "request",
    "service_request",
}

SERVICE_TEXT_PATTERNS = (
    "password",
    "reset password",
    "mfa",
    "vpn",
    "ticket",
    "incident",
    "request",
    "approval",
    "policy",
    "knowledge base",
    "knowledgebase",
    "kb",
    "guide",
    "support",
    "help",
    "issue",
    "error",
    "lỗi",
    "bug",
    "deploy",
    "server",
    "sharepoint",
    "onedrive",
    "outlook",
    "email",
    "teams",
    "service",
    "asset",
    "monitor",
    "backup",
    "compliance",
    "access",
    "permission",
    "privileged",
    "account",
    "sso",
    "trino",
    "svn",
    "gitlab",
    "devsecops",
    "excel",
    "csv",
    "attachment",
    "report",
    "dashboard",
    "health",
)


def _text_blob(*parts: Any) -> str:
    return " ".join(str(part or "") for part in parts).lower()


def classify_traffic_class(
    *,
    intent: str | None = None,
    input_text: str | None = None,
    output_text: str | None = None,
    extra_metadata: dict | None = None,
) -> str:
    """Classify interaction traffic as service_like or casual_unknown.

    This is a conservative heuristic: favor service_like only when there is an
    explicit signal from intent, KB usage, or service-related vocabulary.
    """
    metadata = extra_metadata or {}
    explicit = (metadata.get("traffic_class") or "").strip().lower()
    if explicit in {"service_like", "casual_unknown"}:
        return explicit

    if metadata.get("kb_hit_count") or (metadata.get("kb_sources") and len(metadata.get("kb_sources", [])) > 0):
        return "service_like"

    lowered_intent = (intent or "").strip().lower()
    if lowered_intent in SERVICE_INTENTS:
        return "service_like"

    blob = _text_blob(input_text, output_text, metadata.get("message_mode"), metadata.get("intent"))
    for pattern in SERVICE_TEXT_PATTERNS:
        if pattern and pattern in blob:
            return "service_like"

    if metadata.get("approval_required") and metadata.get("kb_sources"):
        return "service_like"

    return "casual_unknown"
