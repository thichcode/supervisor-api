from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Optional


_SPACE_RE = re.compile(r"\s+")


def _normalize(text: str | None) -> str:
    return _SPACE_RE.sub(" ", (text or "").strip().lower())


def _contains_any(text: str, keywords: Iterable[str]) -> tuple[list[str], float]:
    matched: list[str] = []
    score = 0.0
    for keyword in keywords:
        normalized_keyword = _normalize(keyword)
        if not normalized_keyword:
            continue
        if normalized_keyword in text:
            matched.append(keyword)
            score += 1.2 if " " in normalized_keyword else 1.0
    return matched, score


@dataclass(frozen=True)
class KBCategoryTemplate:
    template_id: str
    label: str
    keywords: tuple[str, ...]
    preferred_search_types: tuple[str, ...]
    category_hints: tuple[str, ...]
    query_variants: tuple[str, ...]
    summary_hint: str
    action_hint: str


@dataclass(frozen=True)
class KBTemplateMatch:
    template: KBCategoryTemplate
    matched_terms: tuple[str, ...]
    score: float

    @property
    def template_id(self) -> str:
        return self.template.template_id

    @property
    def label(self) -> str:
        return self.template.label

    @property
    def preferred_search_types(self) -> tuple[str, ...]:
        return self.template.preferred_search_types

    @property
    def category_hints(self) -> tuple[str, ...]:
        return self.template.category_hints

    @property
    def query_variants(self) -> tuple[str, ...]:
        return self.template.query_variants

    @property
    def summary_hint(self) -> str:
        return self.template.summary_hint

    @property
    def action_hint(self) -> str:
        return self.template.action_hint


class KBCategoryTemplateMapper:
    """Detect common KB query categories and provide lightweight search/render hints."""

    DEFAULT_SEARCH_TYPES = ("policy", "faq", "guide", "document")

    # System-specific keywords that override generic template detection.
    # Keys sorted longest-first so "gitlab" beats "git".
    # Values: (template_id, score_boost).
    SYSTEM_KEYWORDS: tuple[tuple[str, tuple[str, float]], ...] = (
        ("gitlab", ("git_access", 2.0)),
        ("github", ("git_access", 2.0)),
        ("bitbucket", ("git_access", 2.0)),
        ("svn", ("git_access", 1.8)),
        ("vpn", ("vpn_access", 1.8)),
        ("remote access", ("vpn_access", 2.0)),
        ("outlook", ("outlook_mail", 1.8)),
        ("sharepoint", ("sharepoint_onedrive", 1.8)),
        ("onedrive", ("sharepoint_onedrive", 1.8)),
        ("jira", ("jira_confluence", 1.8)),
        ("confluence", ("jira_confluence", 1.8)),
        ("excel", ("excel_csv", 1.5)),
        ("backup", ("backup_restore", 1.5)),
        ("restore", ("backup_restore", 1.5)),
        ("policy", ("policy_request", 1.5)),
    )

    _TEMPLATES: tuple[KBCategoryTemplate, ...] = (
        KBCategoryTemplate(
            template_id="git_access",
            label="Git / GitLab / GitHub",
            keywords=(
                "git",
                "gitlab",
                "github",
                "bitbucket",
                "svn",
                "source control",
                "version control",
                "repo",
                "repository",
                "branch",
                "commit",
                "merge",
                "pipeline",
                "ci/cd",
                "access git",
                "quyền git",
            ),
            preferred_search_types=("guide", "faq", "document", "policy"),
            category_hints=("git", "gitlab", "github", "svn", "repo", "access"),
            query_variants=(
                "git access",
                "gitlab login",
                "github access",
                "svn access",
                "git permission",
                "git không login được",
                "git access request",
                "gitlab không vào được",
            ),
            summary_hint="Ưu tiên hướng dẫn truy cập, quyền repo, SSH key và login GitLab/GitHub.",
            action_hint="Kiểm tra VPN, SSO, quyền repo, SSH key và Git credentials trước.",
        ),
        KBCategoryTemplate(
            template_id="password_reset",
            label="Mật khẩu / Reset",
            keywords=(
                "password",
                "reset password",
                "mật khẩu",
                "quên mật khẩu",
                "đổi mật khẩu",
                "đăng nhập",
                "sign in",
                "account locked",
            ),
            preferred_search_types=("faq", "guide", "document", "policy"),
            category_hints=("access", "auth", "login", "account"),
            query_variants=(
                "reset password",
                "quên mật khẩu",
                "đổi mật khẩu",
                "password recovery",
                "account locked",
            ),
            summary_hint="Ưu tiên FAQ/guide về reset, unlock hoặc đăng nhập tài khoản.",
            action_hint="Bám theo hướng reset mật khẩu, mở khóa tài khoản hoặc kiểm tra login trước.",
        ),
        KBCategoryTemplate(
            template_id="vpn_access",
            label="VPN / Access",
            keywords=(
                "vpn",
                "remote access",
                "vpn access",
                "kết nối vpn",
                "access",
                "truy cập",
                "network access",
                "remote connection",
                "connect vpn",
            ),
            preferred_search_types=("faq", "guide", "document", "policy"),
            category_hints=("access", "network", "auth", "vpn"),
            query_variants=(
                "remote access",
                "vpn access",
                "kết nối vpn",
                "vpn lỗi",
                "network access",
            ),
            summary_hint="Ưu tiên hướng dẫn VPN, remote access, credential hoặc lỗi kết nối.",
            action_hint="Kiểm tra credential, profile, mạng nội bộ và bước reconnect trước.",
        ),
        KBCategoryTemplate(
            template_id="sharepoint_onedrive",
            label="SharePoint / OneDrive",
            keywords=(
                "sharepoint",
                "onedrive",
                "document",
                "file",
                "tài liệu",
                "folder",
                "permission",
                "share",
                "drive",
                "sync",
            ),
            preferred_search_types=("document", "faq", "guide", "policy"),
            category_hints=("document", "file", "collaboration", "sharing", "sync"),
            query_variants=(
                "sharepoint access",
                "onedrive access",
                "document access",
                "file permission",
                "tài liệu chia sẻ",
                "sync lỗi",
            ),
            summary_hint="Ưu tiên tài liệu, quyền truy cập, chia sẻ file, sync và hướng dẫn sử dụng.",
            action_hint="Kiểm tra quyền file/folder, link chia sẻ, đồng bộ và scope tài liệu trước.",
        ),
        KBCategoryTemplate(
            template_id="outlook_mail",
            label="Outlook / Mail",
            keywords=(
                "outlook",
                "mail",
                "email",
                "hộp thư",
                "inbox",
                "sender",
                "receive",
                "send email",
                "calendar",
            ),
            preferred_search_types=("faq", "guide", "document", "policy"),
            category_hints=("mail", "email", "messaging", "calendar"),
            query_variants=(
                "outlook lỗi",
                "email không gửi được",
                "mail không nhận được",
                "outlook access",
                "hộp thư",
            ),
            summary_hint="Ưu tiên lỗi gửi/nhận mail, truy cập Outlook và thiết lập hộp thư.",
            action_hint="Kiểm tra inbox, send/receive, cấu hình account và quy tắc mail trước.",
        ),
        KBCategoryTemplate(
            template_id="backup_restore",
            label="Backup / Restore",
            keywords=(
                "backup",
                "restore",
                "sao lưu",
                "khôi phục",
                "phục hồi",
                "snapshot",
                "recover",
                "recovery",
            ),
            preferred_search_types=("guide", "document", "faq", "policy"),
            category_hints=("backup", "restore", "recovery", "storage"),
            query_variants=(
                "backup lỗi",
                "restore lỗi",
                "sao lưu",
                "khôi phục dữ liệu",
                "recovery",
            ),
            summary_hint="Ưu tiên hướng dẫn backup, restore, phục hồi và kiểm tra trạng thái sao lưu.",
            action_hint="Xác định nguồn backup, phạm vi restore và thời điểm snapshot trước.",
        ),
        KBCategoryTemplate(
            template_id="excel_csv",
            label="Excel / CSV",
            keywords=(
                "excel",
                "csv",
                "spreadsheet",
                "worksheet",
                "file",
                "data",
                "convert",
                "import",
                "export",
            ),
            preferred_search_types=("document", "guide", "faq", "policy"),
            category_hints=("data", "file", "reporting", "spreadsheet"),
            query_variants=(
                "excel file",
                "csv file",
                "import csv",
                "export excel",
                "convert csv",
            ),
            summary_hint="Ưu tiên file bảng tính, import/export và cách xử lý dữ liệu bảng.",
            action_hint="Kiểm tra format file, cột dữ liệu, delimiter và cách import/export trước.",
        ),
        KBCategoryTemplate(
            template_id="jira_confluence",
            label="Jira / Confluence",
            keywords=(
                "jira",
                "confluence",
                "atlassian",
                "ticket",
                "issue",
                "project",
                "page",
                "space",
                "workflow",
            ),
            preferred_search_types=("faq", "guide", "document", "policy"),
            category_hints=("ticket", "issue", "project", "wiki"),
            query_variants=(
                "jira issue",
                "confluence page",
                "jira ticket",
                "confluence access",
                "atlassian",
            ),
            summary_hint="Ưu tiên ticket/issue trong Jira và tài liệu, page, space trong Confluence.",
            action_hint="Kiểm tra project/space, quyền truy cập và trạng thái ticket trước.",
        ),
        KBCategoryTemplate(
            template_id="policy_request",
            label="Policy / Request",
            keywords=(
                "policy",
                "quy định",
                "chính sách",
                "request",
                "approval",
                "approve",
                "request process",
                "procedure",
                "process",
            ),
            preferred_search_types=("policy", "faq", "guide", "document"),
            category_hints=("policy", "request", "approval", "process"),
            query_variants=(
                "request process",
                "approval flow",
                "quy trình request",
                "policy approval",
                "request policy",
            ),
            summary_hint="Ưu tiên policy, quy trình request/approve và tài liệu hướng dẫn nội bộ.",
            action_hint="Xem phạm vi áp dụng, ai duyệt và biểu mẫu/luồng request trước.",
        ),
    )

    @classmethod
    def detect(cls, query: str | None) -> Optional[KBTemplateMatch]:
        normalized = _normalize(query)
        if not normalized:
            return None

        best: Optional[KBTemplateMatch] = None
        for template in cls._TEMPLATES:
            matched_terms, score = _contains_any(normalized, template.keywords)
            if not score:
                continue

            if len(matched_terms) > 1:
                score += 0.4
            if best is None or score > best.score:
                best = KBTemplateMatch(
                    template=template,
                    matched_terms=tuple(dict.fromkeys(matched_terms)),
                    score=round(score, 3),
                )

        # System-level filter: override if query contains system-specific keywords
        # This prevents generic templates (e.g. password_reset) from matching
        # when a specific system (e.g. git, vpn) is mentioned.
        system_override = cls._system_filter(normalized)
        if system_override and (best is None or system_override.score > best.score):
            best = system_override
        elif best is not None and system_override is not None:
            # If system keyword found, apply score penalty to the generic template
            # so the system-specific one wins (it already has higher score)
            pass  # best remains the system-specific match from above

        return best

    @classmethod
    def _system_filter(cls, normalized_query: str) -> Optional[KBTemplateMatch]:
        """Override template if query contains system-specific keywords.

        Iterates SYSTEM_KEYWORDS in declaration order (longest-first after sorting).
        Returns the first matching system-specific template with elevated score.
        """
        for keyword, (template_id, score_boost) in cls.SYSTEM_KEYWORDS:
            if keyword in normalized_query:
                for template in cls._TEMPLATES:
                    if template.template_id == template_id:
                        # Find which keyword(s) matched for this system
                        matched_terms = [keyword]
                        return KBTemplateMatch(
                            template=template,
                            matched_terms=tuple(matched_terms),
                            score=round(score_boost, 3),
                        )
        return None

    @classmethod
    def search_types_for(cls, query: str | None, requested_search_type: str | None = None) -> list[str]:
        normalized = _normalize(requested_search_type)
        if normalized and normalized != "all":
            return [normalized]

        template = cls.detect(query)
        if not template:
            return list(cls.DEFAULT_SEARCH_TYPES)

        ordered = list(template.preferred_search_types)
        for search_type in cls.DEFAULT_SEARCH_TYPES:
            if search_type not in ordered:
                ordered.append(search_type)
        return ordered

    @classmethod
    def build_query_variants(cls, query: str | None, template: Optional[KBTemplateMatch] = None) -> list[str]:
        normalized = _normalize(query)
        if not normalized:
            return []

        variants: list[str] = []
        if template:
            for variant in template.query_variants:
                cleaned = _normalize(variant)
                if cleaned and cleaned not in variants and cleaned != normalized:
                    variants.append(cleaned)

        generic_variants = cls._generic_variants(normalized)
        for variant in generic_variants:
            if variant not in variants:
                variants.append(variant)

        return variants[:8]

    @classmethod
    def _generic_variants(cls, query: str) -> list[str]:
        tokens = re.findall(r"[\wÀ-ỹ]+", query)
        if not tokens:
            return []

        synonym_map = {
            "vpn": ["remote access", "kết nối vpn", "vpn access"],
            "password": ["reset password", "quên mật khẩu", "đổi mật khẩu"],
            "mật khẩu": ["reset password", "quên mật khẩu", "đổi mật khẩu"],
            "mfa": ["2fa", "otp", "two factor authentication"],
            "otp": ["mfa", "2fa", "two factor authentication"],
            "email": ["outlook", "mail", "hộp thư"],
            "outlook": ["email", "mail", "hộp thư"],
            "sharepoint": ["onedrive", "document access", "file permission"],
            "onedrive": ["sharepoint", "document access", "file permission"],
            "backup": ["sao lưu", "restore", "khôi phục"],
            "restore": ["backup", "sao lưu", "khôi phục"],
            "policy": ["quy định", "chính sách", "approval"],
            "guide": ["hướng dẫn", "cách làm", "how to"],
            "request": ["approval", "request process", "biểu mẫu"],
            "ticket": ["incident", "service desk", "request"],
            "incident": ["ticket", "issue", "problem"],
            "trino": ["sql", "query", "dashboard"],
            "gitlab": ["repo", "pipeline", "ci/cd"],
            "svn": ["repo", "source control", "version control"],
            "excel": ["spreadsheet", "csv", "file"],
        }

        variants: list[str] = []
        for token in tokens:
            for synonym in synonym_map.get(token, []):
                cleaned = _normalize(synonym)
                if cleaned and cleaned not in variants and cleaned != query:
                    variants.append(cleaned)

        if len(tokens) >= 2:
            joined = " ".join(tokens[:2])
            if joined != query and joined not in variants:
                variants.append(joined)

        return variants

    @classmethod
    def boost_similarity(
        cls,
        template: Optional[KBTemplateMatch],
        result_title: str,
        result_category: str,
        result_content: str,
        similarity: float,
        knowledge_type: str,
    ) -> float:
        if not template:
            return similarity

        boosted = similarity
        haystack = f"{result_title} {result_category} {result_content}".lower()
        if any(hint in (result_category or "").lower() for hint in template.category_hints):
            boosted = min(1.0, boosted + 0.08)
        if any(term.lower() in haystack for term in template.matched_terms):
            boosted = min(1.0, boosted + 0.1)
        if knowledge_type.lower() in template.preferred_search_types[:2]:
            boosted = min(1.0, boosted + 0.04)
        return boosted
