"""
Gateway Runner - Main entry point for multi-platform gateway
"""

import os
import asyncio
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
import structlog

from .session import SessionStore, get_session_store

logger = structlog.get_logger()


@dataclass
class GatewayConfig:
    """Gateway configuration"""
    telegram_token: Optional[str] = None
    discord_token: Optional[str] = None
    slack_token: Optional[str] = None
    
    # Supervisor integration
    supervisor_url: str = "http://localhost:8000"
    supervisor_api_key: Optional[str] = None
    
    # Session settings
    max_history: int = 50
    
    # Features
    enable_slash_commands: bool = True
    enable_buttons: bool = True


class GatewayRunner:
    """
    Main gateway runner - handles message routing across platforms
    """
    
    def __init__(self, config: Optional[GatewayConfig] = None):
        self.config = config or self._load_config()
        self.session_store: SessionStore = get_session_store()
        
        # Platform adapters (lazy loaded)
        self._platforms: Dict[str, Any] = {}
        
        # Message handlers
        self._handlers: Dict[str, callable] = {}
    
    def _load_config(self) -> GatewayConfig:
        """Load config from environment"""
        return GatewayConfig(
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            discord_token=os.getenv("DISCORD_BOT_TOKEN"),
            slack_token=os.getenv("SLACK_BOT_TOKEN"),
            supervisor_url=os.getenv("SUPERVISOR_URL", "http://localhost:8000"),
            supervisor_api_key=os.getenv("SUPERVISOR_API_KEY"),
        )
    
    async def start_platform(self, platform: str):
        """Start a specific platform adapter"""
        if platform == "telegram":
            if not self.config.telegram_token:
                logger.warning("Telegram token not configured")
                return
            
            from .platforms.telegram import TelegramAdapter
            adapter = TelegramAdapter(
                token=self.config.telegram_token,
                session_store=self.session_store,
                supervisor_url=self.config.supervisor_url,
                api_key=self.config.supervisor_api_key
            )
            await adapter.start()
            self._platforms["telegram"] = adapter
            logger.info("Telegram platform started")
            
        elif platform == "discord":
            if not self.config.discord_token:
                logger.warning("Discord token not configured")
                return
            
            from .platforms.discord import DiscordAdapter
            adapter = DiscordAdapter(
                token=self.config.discord_token,
                session_store=self.session_store,
                supervisor_url=self.config.supervisor_url,
                api_key=self.config.supervisor_api_key
            )
            await adapter.start()
            self._platforms["discord"] = adapter
            logger.info("Discord platform started")
            
        elif platform == "slack":
            if not self.config.slack_token:
                logger.warning("Slack token not configured")
                return
            
            from .platforms.slack import SlackAdapter
            adapter = SlackAdapter(
                token=self.config.slack_token,
                session_store=self.session_store,
                supervisor_url=self.config.supervisor_url,
                api_key=self.config.supervisor_api_key
            )
            await adapter.start()
            self._platforms["slack"] = adapter
            logger.info("Slack platform started")
    
    async def start_all(self):
        """Start all configured platforms"""
        if self.config.telegram_token:
            await self.start_platform("telegram")
        
        if self.config.discord_token:
            await self.start_platform("discord")
        
        if self.config.slack_token:
            await self.start_platform("slack")
        
        logger.info("Gateway started", platforms=list(self._platforms.keys()))
    
    async def stop_all(self):
        """Stop all platforms"""
        for platform_name, adapter in self._platforms.items():
            try:
                await adapter.stop()
                logger.info("Platform stopped", platform=platform_name)
            except Exception as e:
                logger.error("Failed to stop platform", platform=platform_name, error=str(e))
        
        self._platforms.clear()
    
    async def process_message(
        self,
        platform: str,
        user_id: str,
        message: str,
        session_id: Optional[str] = None
    ) -> str:
        """
        Process a message through Supervisor
        Returns the response text
        """
        # Get or create session
        if not session_id:
            session_id = f"{platform}_{user_id}"
        
        session = self.session_store.get_session(session_id)
        if not session:
            session = self.session_store.create_session(
                session_id=session_id,
                platform=platform,
                user_id=user_id
            )
        
        # Add user message
        self.session_store.add_message(
            session_id=session_id,
            role="user",
            content=message
        )
        
        # Call Supervisor API
        try:
            import httpx
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.config.supervisor_url}/api/v1/chat",
                    json={
                        "message": message,
                        "user_id": user_id,
                        "session_id": session_id,
                    },
                    headers={"Authorization": f"Bearer {self.config.supervisor_api_key}"} if self.config.supervisor_api_key else {},
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    result = response.json()
                    reply = result.get("response", "")
                else:
                    reply = f"Error: {response.status_code}"
                    
        except Exception as e:
            logger.error("Supervisor call failed", error=str(e))
            reply = "Xin lỗi, có lỗi xảy ra. Vui lòng thử lại sau."
        
        # Add assistant message
        self.session_store.add_message(
            session_id=session_id,
            role="assistant",
            content=reply
        )
        
        return reply
    
    def get_platform_status(self) -> Dict[str, Any]:
        """Get status of all platforms"""
        status = {}
        
        for platform_name, adapter in self._platforms.items():
            status[platform_name] = {
                "connected": hasattr(adapter, "is_running") and adapter.is_running,
                "type": type(adapter).__name__
            }
        
        return status


# ============ CLI Commands ============

async def start_gateway(platforms: Optional[List[str]] = None):
    """Start the gateway"""
    config = GatewayConfig()
    runner = GatewayRunner(config)
    
    if platforms:
        for p in platforms:
            await runner.start_platform(p)
    else:
        await runner.start_all()
    
    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        await runner.stop_all()


__all__ = ["GatewayRunner", "GatewayConfig", "get_session_store", "start_gateway"]