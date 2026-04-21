"""
Discord Platform Adapter
"""

import asyncio
from typing import Optional, Dict, Any
import structlog

logger = structlog.get_logger()


class DiscordAdapter:
    """
    Discord bot adapter for Supervisor
    """
    
    def __init__(
        self,
        token: str,
        session_store,
        supervisor_url: str,
        api_key: Optional[str] = None
    ):
        self.token = token
        self.session_store = session_store
        self.supervisor_url = supervisor_url
        self.api_key = api_key
        
        self.api_base = "https://discord.com/api/v10"
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
        self._sequence = 0
        self._session_id: Optional[str] = None
    
    async def start(self):
        """Start the Discord bot"""
        # Get gateway info
        try:
            import httpx
            
            async with httpx.AsyncClient() as client:
                # Get gateway
                resp = await client.get(f"{self.api_base}/gateway")
                if resp.status_code != 200:
                    logger.error("Failed to get Discord gateway")
                    return
                
                gateway_url = resp.json().get("url", "")
                
                # Connect (simplified - real implementation needs websocket)
                logger.info("Discord bot initialized", gateway=gateway_url)
                
        except Exception as e:
            logger.error("Failed to start Discord", error=str(e))
            return
        
        self.is_running = True
        logger.info("Discord platform ready (websocket not implemented - use REST API for testing)")
    
    async def stop(self):
        """Stop the Discord bot"""
        self.is_running = False
        if self._task:
            self._task.cancel()
    
    async def handle_interaction(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle a Discord interaction (slash command, button, etc.)"""
        interaction_type = payload.get("type")
        
        if interaction_type == 1:  # Ping
            return {"type": 1}
        
        if interaction_type == 2:  # Application Command (slash)
            data = payload.get("data", {})
            command_name = data.get("name", "")
            options = data.get("options", [])
            
            user_id = str(payload.get("member", {}).get("user", {}).get("id", ""))
            guild_id = str(payload.get("guild_id", ""))
            channel_id = str(payload.get("channel_id", ""))
            user = payload.get("member", {}).get("user", {})
            display_name = user.get("global_name") or user.get("username") or user_id
            thread_id = f"discord_{guild_id or channel_id or user_id}"
            metadata = {
                "platform": "discord",
                "guild_id": guild_id,
                "channel_id": channel_id,
                "chat_type": "group" if guild_id else "private",
                "chat_scope": "group" if guild_id else "dm",
                "group_chat": bool(guild_id),
            }
            
            # Build message from command
            message = f"/{command_name}"
            for opt in options:
                message += f" {opt.get('value', '')}"
            
            # Process through supervisor
            reply = await self._call_supervisor(user_id, display_name, message, thread_id, metadata)
            if not reply:
                return {"type": 5}
            
            return {
                "type": 4,  # Channel message with source
                "data": {
                    "content": reply
                }
            }
        
        return {"type": 5}  # Unknown
    
    async def _call_supervisor(
        self,
        user_id: str,
        display_name: str,
        message: str,
        thread_id: str,
        metadata: Dict[str, Any],
    ) -> str:
        """Call Supervisor API"""
        import httpx
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.supervisor_url}/chat",
                    json={
                        "user_id": user_id,
                        "display_name": display_name,
                        "message": message,
                        "thread_id": thread_id,
                        "metadata": metadata,
                    },
                    headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    payload = response.json()
                    if payload.get("status") == "skipped":
                        return ""
                    return payload.get("message", payload.get("response", "No response"))
                else:
                    return f"Error: {response.status_code}"
                    
        except Exception as e:
            logger.error("Supervisor call failed", error=str(e))
            return "Sorry, an error occurred."
    
    # REST API endpoints for manual testing
    async def send_message(self, channel_id: str, content: str):
        """Send message to Discord channel"""
        import httpx
        
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.api_base}/channels/{channel_id}/messages",
                headers={"Authorization": f"Bot {self.token}"},
                json={"content": content}
            )


__all__ = ["DiscordAdapter"]