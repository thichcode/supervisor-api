from dataclasses import dataclass
from typing import Protocol, Optional


@dataclass
class ExternalMemoryProviderConfig:
    provider_name: str = "mempalace"
    enabled: bool = False
    path: str = ""
    top_k: int = 3


class NullExternalMemoryProvider:
    enabled = False

    async def search(
        self,
        *,
        message_text: str,
        user_id: str,
        thread_id: str,
        case_id: Optional[str] = None,
        team: Optional[str] = None,
    ):
        from src.memory.mempalace_adapter import MemPalaceContext

        return MemPalaceContext(query=message_text, results=[], enabled=False)

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
        return False

    async def health_check(self) -> bool:
        return False


class ExternalMemoryProvider(Protocol):
    @property
    def enabled(self) -> bool:
        ...

    async def search(
        self,
        *,
        message_text: str,
        user_id: str,
        thread_id: str,
        case_id: Optional[str] = None,
        team: Optional[str] = None,
    ):
        ...

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
        ...

    async def health_check(self) -> bool:
        ...


def get_external_memory_provider(config: Optional[ExternalMemoryProviderConfig] = None) -> ExternalMemoryProvider:
    from src.memory.file_provider import FileExternalMemoryProvider
    from src.memory.mempalace_adapter import MemPalaceAdapter

    provider_config = config or ExternalMemoryProviderConfig()
    if not provider_config.enabled:
        return NullExternalMemoryProvider()

    if provider_config.provider_name == "none":
        return NullExternalMemoryProvider()

    if provider_config.provider_name == "file":
        return FileExternalMemoryProvider(provider_config.path)

    if provider_config.provider_name != "mempalace":
        raise ValueError(f"Unsupported external memory provider: {provider_config.provider_name}")

    adapter = MemPalaceAdapter(palace_path=provider_config.path, top_k=provider_config.top_k)
    adapter._enabled = provider_config.enabled
    return adapter