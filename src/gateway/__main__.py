"""Module entrypoint for the Telegram gateway process."""

import asyncio
import os
import structlog

from .run import start_gateway

logger = structlog.get_logger()


def _parse_platforms() -> list[str] | None:
    raw = os.getenv("GATEWAY_PLATFORMS", "").strip()
    if not raw:
        return None
    platforms = [part.strip() for part in raw.split(",") if part.strip()]
    return platforms or None


async def main() -> None:
    platforms = _parse_platforms()
    proxy_present = any(
        os.getenv(name, "").strip()
        for name in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        )
    )
    logger.info(
        "Telegram gateway process booting",
        platforms=platforms or ["telegram", "discord", "slack"],
        token_present=bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip()),
        enable_gateway=os.getenv("ENABLE_TELEGRAM_GATEWAY", "true"),
        proxy_present=proxy_present,
        no_proxy=os.getenv("NO_PROXY", os.getenv("no_proxy", "")),
    )
    logger.info("Starting gateway process", platforms=platforms or "configured")
    await start_gateway(platforms)


if __name__ == "__main__":
    asyncio.run(main())
