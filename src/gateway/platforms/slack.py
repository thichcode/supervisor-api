"""
Slack Platform Adapter
"""

import os
import asyncio
from typing import Optional, Dict, Any, List
import hashlib
import time
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
            
            if not user or not text:
                return None
            
            # Skip bot messages
            if event.get("subtype") == "bot_message":
                return None
            
            # Process message
            session_id = f"slack_{channel}_{user}"
            reply = await self._call_supervisor(user, text, session_id)
            
            # Send reply
            await self._send_message(channel, reply)
            
        elif event_type == "app_mention":
            user = event.get("user")
            text = event.get("text", "")
            channel = event.get("channel")
            
            # Remove mention from text
            import re
            text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()
            
            session_id = f"slack_{channel}_{user}"
            reply = await self._call_supervisor(user, text, session_id)
            
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
        session_id = f"slack_{channel.get('id', '')}_{user_id}"
        
        reply = await self._call_supervisor(user_id, action_value, session_id)
        
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
        
        session_id = f"slack_{channel_id}_{user_id}"
        
        if cmd == "/supervisor":
            reply = await self._call_supervisor(user_id, args, session_id)
        else:
            reply = f"Unknown command: {cmd}"
        
        return {
            "response_type": "ephemeral",
            "text": reply
        }
    
    async def _call_supervisor(self, user_id: str, message: str, session_id: str) -> str:
        """Call Supervisor API"""
        import httpx
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.supervisor_url}/api/v1/chat",
                    json={
                        "message": message,
                        "user_id": user_id,
                        "session_id": session_id,
                    },
                    headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    return response.json().get("response", "No response")
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