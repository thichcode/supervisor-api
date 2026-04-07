from dataclasses import dataclass
from typing import Optional


@dataclass
class ExternalMemoryRoute:
    provider_name: str
    reason: str


class ExternalMemoryRoutingPolicy:
    """Choose an external memory backend based on request shape.

    Current heuristic:
    - support cases prefer MemPalace for deeper recall
    - team/policy-oriented requests prefer MemPalace
    - everything else can fall back to a lightweight file provider when enabled
    - disabled configurations resolve to `none`
    """

    def __init__(
        self,
        *,
        mempalace_enabled: bool,
        file_enabled: bool,
    ):
        self.mempalace_enabled = mempalace_enabled
        self.file_enabled = file_enabled

    def select(
        self,
        *,
        message_text: str,
        case_id: Optional[str] = None,
        team: Optional[str] = None,
        intent: Optional[str] = None,
    ) -> ExternalMemoryRoute:
        normalized = (message_text or "").lower()

        if case_id and self.mempalace_enabled:
            return ExternalMemoryRoute("mempalace", "case-aware routing")

        if intent == "policy" and self.mempalace_enabled:
            return ExternalMemoryRoute("mempalace", "policy intent routing")

        if team and self.mempalace_enabled:
            return ExternalMemoryRoute("mempalace", "team-context routing")

        if any(keyword in normalized for keyword in ["decision", "why", "history", "context"]) and self.mempalace_enabled:
            return ExternalMemoryRoute("mempalace", "semantic recall routing")

        if self.file_enabled:
            return ExternalMemoryRoute("file", "lightweight fallback routing")

        if self.mempalace_enabled:
            return ExternalMemoryRoute("mempalace", "default mempalace routing")

        return ExternalMemoryRoute("none", "no external provider enabled")