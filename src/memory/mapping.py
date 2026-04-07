from dataclasses import dataclass
from typing import Optional


def _slugify(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("/", "_")


@dataclass
class MemPalaceMapping:
    wing: str
    read_room: Optional[str] = None
    write_room: Optional[str] = None


class MemPalaceMappingPolicy:
    """Map supervisor-api domain concepts into MemPalace wing/room taxonomy."""

    def resolve(
        self,
        *,
        user_id: str,
        message_text: str,
        thread_id: str,
        case_id: Optional[str] = None,
        team: Optional[str] = None,
        intent: Optional[str] = None,
    ) -> MemPalaceMapping:
        if case_id:
            wing = f"wing_case_{_slugify(case_id)}"
            return MemPalaceMapping(wing=wing, read_room="case-history", write_room="case-insights")

        if team:
            wing = f"wing_team_{_slugify(team)}"
            if intent == "policy":
                return MemPalaceMapping(wing=wing, read_room="policy-guidance", write_room="policy-decisions")
            return MemPalaceMapping(wing=wing, read_room="team-context", write_room="team-insights")

        wing = f"wing_user_{_slugify(user_id)}"
        return MemPalaceMapping(wing=wing, read_room="user-context", write_room="user-preferences")