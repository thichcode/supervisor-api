"""
Slack Platform Adapter
"""

import asyncio
from typing import Optional, Dict, Any
import structlog

logger = structlog.get_logger()


class SlackAdapter:
    """
    Slack bot adapter for Supervisor
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
        
        self.api_base = "https://slack.com/api"
        self.is_running = False
        self._task: Optional[asyncio.Task] = None
    
    async def start(self):
        """Start the Slack bot"""
        try:
            import httpx
            
            async with httpx.AsyncClient() as client:
                # Test auth
                resp = await client.get(
                    f"{self.api_base}/auth.test",
                    headers={"Authorization": f"Bearer {self.token}"}
                )
                
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ok"):
                        self.is_running = True
                        logger.info("Slack bot started", user=data.get("user"))
                    else:
                        logger.error("Slack auth failed", error=data.get("error"))
                        return
                    
        except Exception as e:
            logger.error("Failed to start Slack", error=str(e))
            return
        
        self.is_running = True
        logger.info("Slack platform ready")
    
    async def stop(self):
        """Stop the Slack bot"""
        self.is_running = False
        if self._task:
            self._task.cancel()
    
    async def handle_event(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle a Slack event"""
        event_type = payload.get("type")
        
        if event_type == "url_verification":
            # Verification challenge for Events API
            return {"challenge": payload.get("challenge")}
        
        if event_type == "event_callback":
            event = payload.get("event", {})
            return await self._handle_slack_event(event)
        
        return None
    
    async def _handle_slack_event(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Handle a Slack event callback"""
        subtype = event.get("subtype")
        
        # Ignore message changes/updates
        if subtype in ["message_changed", "message_deleted"]:
            return None
        
        # Handle direct messages and mentions
        event_type = event.get("type")
        
        if event_type == "message":
            user = event.get("user")
            text = event.get("text", "")
            channel = event.get("channel")
            channel_type = event.get("channel_type", "")
            display_name = event.get("username") or user
            thread_ts = event.get("thread_ts") or event.get("ts") or "root"
            thread_id = f"slack_{channel}_{thread_ts}"
            metadata = {
                "platform": "slack",
                "channel": channel,
                "channel_type": channel_type,
                "group_chat": channel_type in {"channel", "group", "mpim"} or (isinstance(channel, str) and channel[:1] in {"C", "G"}),
                "thread_ts": thread_ts,
            }
            
            if not user or not text:
                return None
            
            # Skip bot messages
            if event.get("subtype") == "bot_message":
                return None
            
            # Process message
            reply = await self._call_supervisor(user, display_name, text, thread_id, metadata)
            if not reply:
                return None
            
            # Send reply
            await self._send_message(channel, reply)
            
        elif event_type == "app_mention":
            user = event.get("user")
            text = event.get("text", "")
            channel = event.get("channel")
            channel_type = event.get("channel_type", "")
            display_name = event.get("username") or user
            thread_ts = event.get("thread_ts") or event.get("ts") or "root"
            thread_id = f"slack_{channel}_{thread_ts}"
            metadata = {
                "platform": "slack",
                "channel": channel,
                "channel_type": channel_type,
                "group_chat": channel_type in {"channel", "group", "mpim"} or (isinstance(channel, str) and channel[:1] in {"C", "G"}),
                "thread_ts": thread_ts,
            }
            
            # Remove mention from text
            import re
            text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
            
            reply = await self._call_supervisor(user, display_name, text, thread_id, metadata)
            
            await self._send_message(channel, reply)
        
        return None
    
    async def handle_action(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle a Slack action (button click, etc.)"""
        actions = payload.get("actions", [])
        user = payload.get("user", {})
        channel = payload.get("channel", {})
        
        if not actions:
            return {"response_action": "clear"}
        
        action = actions[0]
        action_value = action.get("value", "")
        
        # Process action through supervisor
        user_id = user.get("id", "") if isinstance(user, dict) else ""
        display_name = user.get("name", user_id) if isinstance(user, dict) else user_id
        thread_id = f"slack_{channel.get('id', '')}_{action.get('action_ts', 'action')}"
        metadata = {
            "platform": "slack",
            "channel": channel.get("id", ""),
            "group_chat": True,
            "action": action.get("action_id", ""),
        }
        
        reply = await self._call_supervisor(user_id, display_name, action_value, thread_id, metadata)
        
        return {
            "response_action": "update",
            "message": {
                "text": reply
            }
        }
    
    async def handle_command(self, command: str, user_id: str, channel_id: str) -> Dict[str, Any]:
        """Handle a slash command"""
        # Parse command
        parts = command.split()
        cmd = parts[0] if parts else ""
        args = " ".join(parts[1:]) if len(parts) > 1 else ""
        
        thread_id = f"slack_{channel_id}_{user_id}"
        metadata = {
            "platform": "slack",
            "channel": channel_id,
            "group_chat": channel_id[:1] in {"C", "G"},
        }
        
        if cmd == "/supervisor":
            reply = await self._call_supervisor(user_id, user_id, args, thread_id, metadata)
        else:
            reply = f"Unknown command: {cmd}"
        
        return {
            "response_type": "ephemeral",
            "text": reply
        }
    
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
    
    async def _send_message(self, channel: str, text: str):
        """Send message to Slack channel"""
        import httpx
        
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.api_base}/chat.postMessage",
                headers={"Authorization": f"Bearer {self.token}"},
                json={
                    "channel": channel,
                    "text": text
                }
            )


__all__ = ["SlackAdapter"]