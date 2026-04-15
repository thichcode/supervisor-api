"""
Telegram Platform Adapter
"""

import asyncio
from typing import Optional, Dict, Any, Tuple
import structlog
import httpx

logger = structlog.get_logger()


def build_approval_message_text(approval) -> str:
    """Build the Telegram approval card text."""
    confidence_pct = round((approval.confidence * 100) if approval.confidence <= 1 else approval.confidence, 1)
    threshold_pct = round((approval.threshold * 100) if approval.threshold <= 1 else approval.threshold, 1)
    thread_id = approval.metadata.get("thread_id", "") if getattr(approval, "metadata", None) else ""
    risk_level = approval.metadata.get("risk_level", "") if getattr(approval, "metadata", None) else ""

    return (
        "⚠️ Approval Required\n\n"
        f"Approval ID: {approval.id}\n"
        f"Request ID: {approval.request_id}\n"
        f"User: {approval.display_name} ({approval.user_id})\n"
        f"Thread: {thread_id or 'N/A'}\n"
        f"Risk: {risk_level or 'N/A'}\n"
        f"Confidence: {confidence_pct}% (threshold: {threshold_pct}%)\n\n"
        f"Original:\n{approval.original_message}\n\n"
        f"AI Response:\n{approval.ai_response}\n\n"
        "Use the buttons below to approve or reject."
    )


def build_approval_inline_keyboard(approval_id: str) -> Dict[str, Any]:
    """Build the inline keyboard for approval actions."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"approval:approve:{approval_id}"},
                {"text": "🚫 Reject", "callback_data": f"approval:reject:{approval_id}"},
            ]
        ]
    }


def parse_approval_callback_data(data: str) -> Optional[Tuple[str, str]]:
    """Parse Telegram callback data for approval actions."""
    if not data:
        return None
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "approval":
        return None
    action, approval_id = parts[1], parts[2]
    if action not in {"approve", "reject"} or not approval_id:
        return None
    return action, approval_id


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
        callback_query = update.get("callback_query")
        if callback_query:
            await self.handle_callback_query(callback_query)
            return

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
    
    async def handle_callback_query(self, callback_query: Dict[str, Any]) -> bool:
        """Handle Telegram inline keyboard callbacks for approval actions."""
        data = callback_query.get("data", "")
        parsed = parse_approval_callback_data(data)
        if not parsed:
            return False

        action, approval_id = parsed
        callback_query_id = callback_query.get("id")
        message = callback_query.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        message_id = message.get("message_id")
        actor = (
            callback_query.get("from", {}).get("first_name")
            or callback_query.get("from", {}).get("username")
            or str(callback_query.get("from", {}).get("id", ""))
        )

        try:
            result = await self._call_approval_action(approval_id, action, actor)
            if not result:
                await self._answer_callback_query(callback_query_id, "Không thể xử lý approval này.", show_alert=True)
                return False

            status_text = "✅ Approved" if action == "approve" else "🚫 Rejected"
            await self._answer_callback_query(callback_query_id, f"{status_text} successfully")
            await self._edit_message_text(
                chat_id,
                message_id,
                f"{status_text} by {actor}\nApproval ID: {approval_id}",
            )
            return True
        except Exception as e:
            logger.error("Approval callback failed", error=str(e), approval_id=approval_id, action=action)
            await self._answer_callback_query(callback_query_id, "Có lỗi xảy ra khi xử lý approval.", show_alert=True)
            return False
    
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

    async def _call_approval_action(self, approval_id: str, action: str, actor: str) -> Optional[Dict[str, Any]]:
        """Call the approval action endpoint on Supervisor."""

        payload = {
            "action": action,
            "reviewed_by": actor,
            "comment": f"Telegram inline {action} by {actor}",
        }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.supervisor_url}/approvals/{approval_id}/action",
                json=payload,
                headers=headers,
                timeout=30.0,
            )
            if response.status_code != 200:
                logger.warning("Approval action failed", approval_id=approval_id, status=response.status_code, body=response.text)
                return None
            return response.json()

    async def _answer_callback_query(self, callback_query_id: str, text: str, show_alert: bool = False):

        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.api_base}/answerCallbackQuery",
                json={
                    "callback_query_id": callback_query_id,
                    "text": text,
                    "show_alert": show_alert,
                },
            )

    async def _edit_message_text(self, chat_id: str, message_id: Any, text: str):

        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.api_base}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text,
                },
            )
    
    async def _send_message(self, chat_id: str, text: str):
        """Send a message via Telegram"""
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{self.api_base}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown"
                }
            )


__all__ = ["TelegramAdapter", "build_approval_message_text", "build_approval_inline_keyboard", "parse_approval_callback_data"]