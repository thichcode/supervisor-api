"""
Telegram Platform Adapter
"""

import asyncio
from typing import Optional, Dict, Any
import structlog

logger = structlog.get_logger()


class TelegramAdapter:
    """
    Telegram bot adapter for Supervisor
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
        
        self.api_base = f"https://api.telegram.org/bot{token}"
        self.is_running = False
        self._offset = 0
        self._task: Optional[asyncio.Task] = None
    
    async def start(self):
        """Start the Telegram bot"""
        # Test connection
        try:
            async with asyncio.timeout(10):
                import httpx
                async with httpx.AsyncClient() as client:
                    resp = await client.get(f"{self.api_base}/getMe")
                    if resp.status_code != 200:
                        logger.error("Telegram auth failed", status=resp.status_code)
                        return
                    
                    me = resp.json()
                    logger.info("Telegram bot started", username=me.get("result", {}).get("username"))
                    
        except Exception as e:
            logger.error("Failed to start Telegram", error=str(e))
            return
        
        self.is_running = True
        
        # Start polling
        self._task = asyncio.create_task(self._poll_loop())
    
    async def stop(self):
        """Stop the Telegram bot"""
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
    
    async def _poll_loop(self):
        """Poll for updates"""
        import httpx
        
        while self.is_running:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"{self.api_base}/getUpdates",
                        params={
                            "offset": self._offset,
                            "timeout": 30,
                        }
                    )
                    
                    if resp.status_code == 200:
                        updates = resp.json().get("result", [])
                        
                        for update in updates:
                            self._offset = update.get("update_id", 0) + 1
                            await self._handle_update(update)
                    
                    await asyncio.sleep(1)
                    
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error("Polling error", error=str(e))
                await asyncio.sleep(5)
    
    async def _handle_update(self, update: Dict[str, Any]):
        """Handle an incoming update"""
        message = update.get("message")
        if not message:
            return
        
        user_id = str(message.get("from", {}).get("id", ""))
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "")
        display_name = (
            message.get("from", {}).get("first_name")
            or message.get("from", {}).get("username")
            or user_id
        )
        chat_type = message.get("chat", {}).get("type", "private")
        group_chat = chat_type in {"group", "supergroup"}
        thread_id = f"telegram_{chat_id}"
        metadata = {
            "platform": "telegram",
            "chat_id": chat_id,
            "chat_type": chat_type,
            "group_chat": group_chat,
        }
        
        if not text:
            return
        
        # Handle commands
        if text.startswith("/"):
            await self._handle_command(chat_id, user_id, text)
            return
        
        # Process as regular message
        reply = await self._call_supervisor(user_id, display_name, text, thread_id, metadata)
        if not reply:
            return
        
        # Send response
        await self._send_message(chat_id, reply)
    
    async def _handle_command(self, chat_id: str, user_id: str, command: str):
        """Handle a command"""
        cmd = command.split()[0].lower()
        
        if cmd == "/start":
            await self._send_message(chat_id, "Xin chào! Tôi là Supervisor Agent. Gửi tin nhắn để được hỗ trợ.")
        elif cmd == "/help":
            await self._send_message(chat_id, "Commands:\n/start - Start\n/help - Help\n/history - View history")
        elif cmd == "/history":
            await self._send_message(chat_id, "Use /clear to clear history")
        elif cmd == "/clear":
            session_id = f"telegram_{user_id}"
            self.session_store.clear_history(session_id)
            await self._send_message(chat_id, "History cleared!")
        else:
            await self._send_message(chat_id, f"Unknown command: {command}")
    
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
                    return f"Lỗi: {response.status_code}"
                    
        except Exception as e:
            logger.error("Supervisor call failed", error=str(e))
            return "Xin lỗi, có lỗi xảy ra."
    
    async def _send_message(self, chat_id: str, text: str):
        """Send a message via Telegram"""
        import httpx
        
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.api_base}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown"
                }
            )


__all__ = ["TelegramAdapter"]