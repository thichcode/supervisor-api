from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import json


@dataclass
class FileMemoryHit:
    text: str
    source_file: str
    similarity: float = 0.5


@dataclass
class FileMemoryContext:
    query: str
    results: list[FileMemoryHit]
    enabled: bool = False

    def to_memory_items(self) -> list[dict]:
        return [
            {
                "content": hit.text,
                "confidence": hit.similarity,
                "source_file": hit.source_file,
                "provider": "file",
            }
            for hit in self.results
        ]


class FileExternalMemoryProvider:
    def __init__(self, path: str):
        self.path = path

    @property
    def enabled(self) -> bool:
        return bool(self.path)

    async def search(
        self,
        *,
        message_text: str,
        user_id: str,
        thread_id: str,
        case_id: Optional[str] = None,
        team: Optional[str] = None,
    ) -> FileMemoryContext:
        if not self.enabled:
            return FileMemoryContext(query=message_text, results=[], enabled=False)

        file_path = Path(self.path)
        if not file_path.exists():
            return FileMemoryContext(query=message_text, results=[], enabled=False)

        data = json.loads(file_path.read_text(encoding="utf-8"))
        hits = [
            FileMemoryHit(text=item["content"], source_file=file_path.name, similarity=float(item.get("confidence", 0.5)))
            for item in data
            if message_text.lower().split()[0] in item["content"].lower()
        ]
        return FileMemoryContext(query=message_text, results=hits, enabled=True)

    async def write_memory(
        self,
        *,
        content: str,
        user_id: str,
        thread_id: str,
        case_id: Optional[str] = None,
        team: Optional[str] = None,
        room: str = "supervisor-insights",
        source_file: str = "supervisor-api",
        agent: str = "supervisor-api",
    ) -> bool:
        if not self.enabled:
            return False

        file_path = Path(self.path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        if file_path.exists():
            data = json.loads(file_path.read_text(encoding="utf-8"))
        else:
            data = []

        data.append({"content": content, "confidence": 0.6, "room": room, "agent": agent})
        file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

    async def health_check(self) -> bool:
        return self.enabled