"""
Telegram Platform Adapter
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple
import secrets
import structlog
import httpx

from src.core.conversation_continuity import ConversationContinuityEvaluator
from src.core.kb_presentation import build_kb_card, format_kb_response
logger = structlog.get_logger()


def _truncate_text(text: str, limit: int = 120) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"



def build_approval_message_text(approval, compact: Optional[bool] = None) -> str:
    """Build the Telegram approval card text."""
    confidence_pct = round((approval.confidence * 100) if approval.confidence <= 1 else approval.confidence, 1)
    threshold_pct = round((approval.threshold * 100) if approval.threshold <= 1 else approval.threshold, 1)
    metadata = getattr(approval, "metadata", None) or {}
    thread_id = metadata.get("thread_id", "")
    platform = metadata.get("platform", "")
    chat_type = metadata.get("chat_type", "")
    chat_scope = metadata.get("chat_scope", "")
    group_chat = metadata.get("group_chat")
    risk_level = metadata.get("risk_level", "")
    kb_sources = metadata.get("kb_sources", [])
    kb_evidence = metadata.get("kb_evidence", [])
    user_id = getattr(approval, "user_id", "") or metadata.get("user_id", "")
    display_name = getattr(approval, "display_name", "") or metadata.get("display_name", "")

    is_group_chat = group_chat is True
    is_compact = compact if compact is not None else is_group_chat
    chat_mode_label = "Group chat" if is_group_chat else "Direct message" if group_chat is False else "Chat"
    header = "⚠️ Group Chat Approval Required" if is_group_chat else ("⚠️ Direct Message Approval Required" if group_chat is False else "⚠️ Approval Required")
    scope_note = (
        "⚠️ This request came from a *group chat*. Verify the requester, thread context, and impact before approving."
        if is_group_chat
        else (
            "This request came from a direct message."
            if group_chat is False
            else ""
        )
    )

    kb_lines = []
    if kb_sources:
        kb_lines.append("KB Sources:")
        for idx, source in enumerate(kb_sources[:3], start=1):
            title = source.get("title") or source.get("name") or source.get("id") or "N/A"
            similarity = source.get("similarity")
            similarity_text = f" ({similarity:.2f})" if isinstance(similarity, (int, float)) else ""
            kb_lines.append(f"{idx}. {title}{similarity_text}")
    if kb_evidence:
        kb_lines.append("KB Evidence:")
        for idx, item in enumerate(kb_evidence[:3], start=1):
            title = item.get("title") or item.get("id") or "N/A"
            similarity = item.get("similarity")
            similarity_text = f" ({similarity:.2f})" if isinstance(similarity, (int, float)) else ""
            kb_lines.append(f"{idx}. {title}{similarity_text}")

    context_lines = [
        f"Approval ID: {approval.id}",
        f"Request ID: {approval.request_id}",
        f"Display Name: {display_name or 'N/A'}",
        f"User ID: {user_id or 'N/A'}",
        f"Thread ID: {thread_id or 'N/A'}",
        f"Chat Mode: {chat_mode_label}",
    ]
    if platform:
        context_lines.append(f"Platform: {platform}")
    if chat_type:
        context_lines.append(f"Chat Type: {chat_type}")
    if chat_scope:
        context_lines.append(f"Chat Scope: {chat_scope}")
    if group_chat is not None:
        context_lines.append(f"Group Chat: {group_chat}")

    if is_compact:
        summary_original = _truncate_text(approval.original_message, 100)
        summary_ai = _truncate_text(approval.ai_response, 120)
        return (
            f"{header}\n\n"
            + "\n".join(context_lines)
            + f"\nRisk: {risk_level or 'N/A'}\n"
            + f"Confidence: {confidence_pct}% (threshold: {threshold_pct}%)"
            + (f"\n\n{scope_note}" if scope_note else "")
            + f"\n\nOriginal (preview):\n{summary_original or 'N/A'}"
            + f"\n\nAI (preview):\n{summary_ai or 'N/A'}\n\n"
            + "Tap *View full context* to expand this card."
        )

    kb_section = "\n".join(kb_lines)
    if kb_section:
        kb_section = f"\n\n{kb_section}"

    note_section = f"\n\n{scope_note}" if scope_note else ""

    return (
        f"{header}\n\n"
        + "\n".join(context_lines)
        + f"\nRisk: {risk_level or 'N/A'}\n"
        + f"Confidence: {confidence_pct}% (threshold: {threshold_pct}%)"
        + note_section
        + f"\n\nOriginal:\n{approval.original_message}\n\n"
        + f"AI Response:\n{approval.ai_response}{kb_section}\n\n"
        + "Use the buttons below to approve or reject."
    )



def build_approval_inline_keyboard(approval_id: str, compact: bool = False, group_chat: Optional[bool] = None) -> Dict[str, Any]:
    """Build the inline keyboard for approval actions."""
    buttons = [
        [
            {"text": "✅ Approve", "callback_data": f"approval:approve:{approval_id}"},
            {"text": "🚫 Reject", "callback_data": f"approval:reject:{approval_id}"},
        ]
    ]
    if group_chat is True and compact:
        buttons.append([
            {"text": "🔍 Search KB", "callback_data": f"approval:search_kb:{approval_id}"},
        ])
        buttons.append([
            {"text": "🔎 View full context", "callback_data": f"approval:view_full_context:{approval_id}"},
        ])
    else:
        buttons.append([
            {"text": "🔍 Search KB", "callback_data": f"approval:search_kb:{approval_id}"},
        ])
    return {"inline_keyboard": buttons}

def build_kb_search_prompt(approval_id: str) -> str:
    """Build prompt for KB search."""
    return (
        "🔍 Search Knowledge Base\n\n"
        f"Approval ID: {approval_id}\n\n"
        "Nhập từ khóa để tìm kiếm trong Knowledge Base.\n"
        "Hệ thống sẽ tìm kết quả và tạo câu trả lời mới."
    )



def build_kb_force_reply_markup(approval_id: str) -> Dict[str, Any]:
    """Build a ForceReply payload so Telegram shows an inline text box for the KB query."""
    return {
        "force_reply": True,
        "input_field_placeholder": "Nhập từ khóa KB để gợi ý cho user...",
        "selective": True,
        "kb_approval_id": approval_id,
    }



def build_kb_candidate_force_reply_markup(candidate_id: str) -> Dict[str, Any]:
    """Build a ForceReply payload for KB candidate revision notes."""
    return {
        "force_reply": True,
        "input_field_placeholder": "Nhập lý do revise / bổ sung cho KB candidate...",
        "selective": True,
        "kb_candidate_id": candidate_id,
    }



def build_kb_candidate_revision_prompt(candidate_id: str) -> str:
    return (
        "📝 Revise KB Candidate\n\n"
        f"Candidate ID: {candidate_id}\n\n"
        "Nhập nhận xét ngắn để revise KB (thiếu gì, cần sửa gì, hoặc keyword bổ sung)."
    )



def build_kb_candidate_callback_data(
    action: str,
    candidate_id: str,
    session_id: Optional[str] = None,
    page: Optional[int] = None,
) -> str:
    """Build callback data for KB candidate actions.

    The optional session/page suffix lets list actions refresh the same page in place
    after approve/revise, while keeping the legacy 3-part form for notification cards.
    """
    action = (action or "").strip().lower()
    candidate_id = (candidate_id or "").strip()
    parts = ["kb_candidate", action, candidate_id]
    if session_id:
        parts.append(session_id.strip())
        if page is not None:
            parts.append(str(max(1, int(page))))
    return ":".join(parts)


def parse_kb_candidate_callback_data(data: str) -> Optional[Tuple[str, str, Optional[str], Optional[int]]]:
    """Parse callback data for knowledge candidate actions."""
    if not data:
        return None
    parts = data.split(":")
    if len(parts) not in {3, 4, 5} or parts[0] != "kb_candidate":
        return None
    action, candidate_id = parts[1], parts[2]
    if action not in {"approve", "revise"} or not candidate_id:
        return None
    session_id: Optional[str] = None
    page: Optional[int] = None
    if len(parts) >= 4:
        session_id = parts[3] or None
    if len(parts) == 5:
        try:
            page = max(1, int(parts[4]))
        except ValueError:
            return None
    return action, candidate_id, session_id, page



def parse_kb_candidate_text_action(text: str) -> Optional[Tuple[str, str, str]]:
    """Parse plain text review commands like APPROVE <id> or REVISE <id>: note."""
    stripped = (text or "").strip()
    if not stripped:
        return None
    upper = stripped.upper()
    if upper.startswith("APPROVE "):
        candidate_id = stripped.split(None, 1)[1].strip()
        if candidate_id:
            return "approve", candidate_id, ""
        return None
    if upper.startswith("REVISE "):
        remainder = stripped.split(None, 1)[1].strip()
        if not remainder:
            return None
        if ":" in remainder:
            candidate_id, note = remainder.split(":", 1)
        else:
            candidate_id, note = remainder, ""
        candidate_id = candidate_id.strip()
        note = note.strip()
        if candidate_id:
            return "revise", candidate_id, note
    return None


def parse_approval_callback_data(data: str) -> Optional[Tuple[str, str]]:
    """Parse Telegram callback data for approval actions."""
    if not data:
        return None
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != "approval":
        return None
    action, approval_id = parts[1], parts[2]
    if action not in {"approve", "reject", "search_kb", "view_full_context"} or not approval_id:
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
        self._pending_kb_search: Dict[str, str] = {}
        self._pending_kb_revision: Dict[str, Dict[str, Any]] = {}
        self._conversation_buffers: Dict[str, Dict[str, Any]] = {}
        self._conversation_flush_tasks: Dict[str, asyncio.Task] = {}
        self._buffer_delay_seconds = 60
        self._message_mode_detector = ConversationContinuityEvaluator()
        self._kb_sessions: Dict[str, Dict[str, Any]] = {}
    
    async def start(self):
        """Start the Telegram bot"""
        # Test connection
        try:
            async with asyncio.timeout(10):
                async with httpx.AsyncClient() as client:
                    resp = await client.get(f"{self.api_base}/getMe")
                    if resp.status_code != 200:
                        logger.error(
                            "Telegram auth failed",
                            status=resp.status_code,
                            body=resp.text[:500],
                        )
                        return
                    
                    me = resp.json()
                    logger.info("Telegram bot started", username=me.get("result", {}).get("username"))
                    await self._register_bot_commands()
                    
        except Exception as e:
            logger.error(
                "Failed to start Telegram",
                error=str(e),
                error_type=type(e).__name__,
                error_repr=repr(e),
            )
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
            self._task = None
        for task in list(self._conversation_flush_tasks.values()):
            task.cancel()
        self._conversation_flush_tasks.clear()
        self._conversation_buffers.clear()
    
    async def _register_bot_commands(self):
        """Register the Telegram command menu so /health appears in the client UI."""
        commands = [
            {"command": "start", "description": "Start the bot"},
            {"command": "help", "description": "Show available commands"},
            {"command": "health", "description": "Check bot and supervisor health"},
            {"command": "history", "description": "View history guidance"},
            {"command": "clear", "description": "Clear chat history"},
            {"command": "kb", "description": "Search or browse the knowledge base"},
            {"command": "super_analytics", "description": "View quick supervisor analytics"},
        ]

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self.api_base}/setMyCommands",
                    json={"commands": commands},
                    timeout=10.0,
                )
                if resp.status_code != 200:
                    logger.warning("Failed to register Telegram commands", status=resp.status_code)
                    return False
                data = resp.json()
                if not data.get("ok", False):
                    logger.warning("Telegram commands registration returned not ok", response=data)
                    return False
                logger.info("Telegram commands registered", commands=[c["command"] for c in commands])
                return True
        except Exception as e:
            logger.error("Failed to register Telegram commands", error=str(e))
            return False

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
        chat_scope = "group" if group_chat else "dm"
        thread_id = f"telegram_{chat_id}"
        metadata = {
            "platform": "telegram",
            "chat_id": chat_id,
            "chat_type": chat_type,
            "chat_scope": chat_scope,
            "group_chat": group_chat,
        }
        
        if not text:
            return
        
        # Handle commands
        if text.startswith("/"):
            await self._handle_command(chat_id, user_id, text)
            return

        kb_candidate_command = parse_kb_candidate_text_action(text)
        if kb_candidate_command:
            action, candidate_id, note = kb_candidate_command
            if action == "approve":
                await self._handle_kb_candidate_review(chat_id, user_id, candidate_id, action, note or None)
            else:
                await self._handle_kb_candidate_review(chat_id, user_id, candidate_id, action, note or None)
            return

        # Check for pending KB search
        if chat_id in self._pending_kb_search:
            approval_id = self._pending_kb_search.pop(chat_id)
            await self._handle_kb_search(chat_id, user_id, text, approval_id)
            return

        # Check for pending KB candidate revision notes
        if chat_id in self._pending_kb_revision:
            pending_revision = self._pending_kb_revision.pop(chat_id)
            candidate_id = str(pending_revision.get("candidate_id", ""))
            await self._handle_kb_candidate_review(chat_id, user_id, candidate_id, "revise", text, refresh_context=pending_revision)
            return

        # Buffer regular messages for 60s so multi-line / burst updates are merged before asking Supervisor.
        await self._buffer_conversation_message(
            thread_id=thread_id,
            chat_id=chat_id,
            user_id=user_id,
            display_name=display_name,
            text=text,
            metadata=metadata,
        )
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
            parsed_candidate = parse_kb_candidate_callback_data(data)
            if parsed_candidate:
                return await self._handle_kb_candidate_callback(callback_query, *parsed_candidate)
            parsed_kb = self._parse_kb_callback_data(data)
            if parsed_kb:
                return await self._handle_kb_callback(callback_query, *parsed_kb)
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
            if action == "search_kb":
                await self._answer_callback_query(
                    callback_query_id,
                    "Nhập từ khóa để tìm KB...",
                    show_alert=True,
                )
                self._pending_kb_search[chat_id] = approval_id
                await self._send_message(
                    chat_id,
                    build_kb_search_prompt(approval_id),
                    reply_markup=build_kb_force_reply_markup(approval_id),
                    parse_mode=None,
                )
                return True

            if action == "view_full_context":
                from src.core.approval import approval_service as approval_service_local

                approval = await approval_service_local.get_approval(approval_id)
                if not approval:
                    await self._answer_callback_query(callback_query_id, "Không tìm thấy approval.", show_alert=True)
                    return False
                await self._answer_callback_query(callback_query_id, "Đã mở full context")
                full_text = build_approval_message_text(approval, compact=False)
                await self._edit_message_text(
                    chat_id,
                    message_id,
                    full_text,
                    reply_markup=build_approval_inline_keyboard(
                        approval_id,
                        compact=False,
                        group_chat=bool((getattr(approval, "metadata", None) or {}).get("group_chat")),
                    ),
                )
                return True

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

    async def _review_kb_candidate(self, candidate_id: str, action: str, reviewer_id: str, note: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Review a KB candidate via the shared draft service."""
        try:
            from src.services.kb_draft_service import review_kb_candidate as review_kb_candidate_local

            return await review_kb_candidate_local(
                candidate_id_or_source_id=candidate_id,
                action=action,
                reviewer_id=reviewer_id,
                note=note,
            )
        except Exception as exc:
            logger.error("KB candidate review failed", error=str(exc), candidate_id=candidate_id, action=action)
            return None

    async def _handle_kb_candidate_callback(
        self,
        callback_query: Dict[str, Any],
        action: str,
        candidate_id: str,
        session_id: Optional[str] = None,
        page: Optional[int] = None,
    ) -> bool:
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

        if action == "revise":
            self._pending_kb_revision[chat_id] = {
                "candidate_id": candidate_id,
                "session_id": session_id,
                "page": page or 1,
                "message_id": message_id,
            }
            await self._answer_callback_query(callback_query_id, "Nhập ghi chú revise cho KB candidate...", show_alert=True)
            await self._send_message(
                chat_id,
                build_kb_candidate_revision_prompt(candidate_id),
                reply_markup=build_kb_candidate_force_reply_markup(candidate_id),
                parse_mode=None,
            )
            return True

        try:
            result = await self._review_kb_candidate(candidate_id, "approve", actor)
            if not result:
                await self._answer_callback_query(callback_query_id, "Không tìm thấy KB candidate.", show_alert=True)
                return False
            await self._answer_callback_query(callback_query_id, "Đã approve KB candidate")
            if session_id:
                session = self._kb_sessions.get(session_id)
                if session:
                    await self._render_kb_candidates(chat_id, session, page=page or 1, edit_message_id=message_id)
                    return True
            await self._edit_message_text(
                chat_id,
                message_id,
                f"✅ KB candidate approved by {actor}\nCandidate ID: {result.get('candidate_id', candidate_id)}\nTitle: {result.get('title', 'N/A')}",
                parse_mode=None,
            )
            return True
        except Exception as e:
            logger.error("KB candidate callback failed", error=str(e), candidate_id=candidate_id, action=action)
            await self._answer_callback_query(callback_query_id, "Có lỗi xảy ra khi xử lý KB candidate.", show_alert=True)
            return False

    async def _handle_kb_candidate_review(
        self,
        chat_id: str,
        user_id: str,
        candidate_id: str,
        action: str,
        note: Optional[str] = None,
        refresh_context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        actor = user_id
        try:
            result = await self._review_kb_candidate(candidate_id, action, actor, note)
            if not result:
                await self._send_message(chat_id, f"Không tìm thấy KB candidate: {candidate_id}", parse_mode=None)
                return False
            if action == "approve":
                message = f"✅ KB candidate approved\nCandidate ID: {result.get('candidate_id', candidate_id)}\nTitle: {result.get('title', 'N/A')}"
            else:
                message = (
                    f"📝 KB candidate marked for revision\nCandidate ID: {result.get('candidate_id', candidate_id)}\nTitle: {result.get('title', 'N/A')}"
                    + (f"\nNote: {note}" if note else "")
                )
            await self._send_message(chat_id, message, parse_mode=None)

            if refresh_context and refresh_context.get("session_id") and refresh_context.get("message_id") is not None:
                session = self._kb_sessions.get(str(refresh_context.get("session_id")))
                if session:
                    try:
                        await self._render_kb_candidates(
                            chat_id,
                            session,
                            page=int(refresh_context.get("page") or 1),
                            edit_message_id=refresh_context.get("message_id"),
                        )
                    except Exception as refresh_exc:
                        logger.warning(
                            "KB candidate list refresh failed",
                            error=str(refresh_exc),
                            candidate_id=candidate_id,
                            action=action,
                        )
            return True
        except Exception as e:
            logger.error("KB candidate review message failed", error=str(e), candidate_id=candidate_id, action=action)
            await self._send_message(chat_id, f"Có lỗi xảy ra khi xử lý KB candidate: {str(e)}", parse_mode=None)
            return False

    async def _handle_command(self, chat_id: str, user_id: str, command: str):
        """Handle a command"""
        tokens = command.split()
        cmd = tokens[0].lower()

        if cmd == "/start":
            await self._send_message(chat_id, "Xin chào! Tôi là Supervisor Agent. Gửi tin nhắn để được hỗ trợ.")
        elif cmd == "/help":
            await self._send_message(
                chat_id,
                "Commands:\n/start - Start\n/help - Help\n/health - Check bot and supervisor health\n/history - View history\n/clear - Clear history\n/kb - Search or browse KB\n/super_analytics - Quick analytics report\n\nKB candidate review:\n- APPROVE <candidate_id>\n- REVISE <candidate_id>: <note>",
            )
        elif cmd == "/history":
            await self._send_message(chat_id, "Use /clear to clear history")
        elif cmd == "/health":
            await self._handle_health_command(chat_id)
        elif cmd == "/clear":
            session_id = f"telegram_{user_id}"
            self.session_store.clear_history(session_id)
            await self._send_message(chat_id, "History cleared!")
        elif cmd == "/kb":
            await self._handle_kb_command(chat_id, user_id, command)
        elif cmd == "/super_analytics":
            await self._handle_super_analytics_command(chat_id, command)
        else:
            await self._send_message(chat_id, f"Unknown command: {command}")

    async def _handle_health_command(self, chat_id: str):
        """Check gateway and supervisor health."""
        status_lines = ["Bot health: online"]
        supervisor_ok = False
        supervisor_ready = False
        
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.supervisor_url}/health", timeout=10.0)
                supervisor_ok = resp.status_code == 200
                if supervisor_ok:
                    data = resp.json()
                    status_lines.append(f"Supervisor: {data.get('status', 'unknown')}")
                    status_lines.append(f"Model: {data.get('llm_model', 'N/A')}")
                else:
                    status_lines.append(f"Supervisor: error {resp.status_code}")

                ready_resp = await client.get(f"{self.supervisor_url}/health/ready", timeout=10.0)
                supervisor_ready = ready_resp.status_code == 200
                if supervisor_ready:
                    ready_data = ready_resp.json()
                    status_lines.append(f"Readiness: {ready_data.get('status', 'unknown')}")
                else:
                    status_lines.append(f"Readiness: error {ready_resp.status_code}")
        except Exception as e:
            status_lines.append(f"Supervisor: unreachable ({str(e)})")

        if supervisor_ok and supervisor_ready:
            status_lines.append("Overall: healthy")
        elif supervisor_ok:
            status_lines.append("Overall: degraded")
        else:
            status_lines.append("Overall: down")

        await self._send_message(chat_id, "\n".join(status_lines))
    
    async def _handle_kb_search(self, chat_id: str, user_id: str, keywords: str, approval_id: str):
        """Handle KB search with keywords and generate new response."""
        try:
            await self._send_message(chat_id, f"🔍 Đang tìm: {keywords}...")

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.supervisor_url}/approvals/{approval_id}/retry-with-kb",
                    json={"keywords": keywords, "requested_by": user_id},
                    headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                    timeout=60.0
                )

                if response.status_code == 200:
                    data = response.json()
                    new_response = data.get("new_response", "Không tìm thấy kết quả.")
                    kb_summary = data.get("kb_summary", "")
                    kb_action_items = data.get("kb_action_items", []) or []
                    kb_sources = data.get("kb_sources", []) or []
                    kb_template_label = data.get("kb_template_label", "")
                    kb_template_hint = data.get("kb_template_hint", "")
                    kb_cards = [build_kb_card(item, query=keywords) for item in kb_sources]
                    source_lines = []
                    for idx, card in enumerate(kb_cards[:3], start=1):
                        kb_id_part = f" | ID: {card['id']}" if card.get("id") else ""
                        source_lines.append(f"{idx}. {card['title']}{kb_id_part} ({card.get('source_hint', '')})")

                    message_lines = ["🔄 KB Response", ""]
                    if kb_template_label:
                        message_lines.append(f"Mẫu KB: {kb_template_label}")
                        if kb_template_hint:
                            message_lines.append(f"Gợi ý: {kb_template_hint}")
                        message_lines.append("")
                    if kb_summary:
                        message_lines.append(f"Tóm tắt:\n{kb_summary}")
                        message_lines.append("")
                    if kb_action_items:
                        message_lines.append("Làm theo:")
                        for idx, step in enumerate(kb_action_items[:5], start=1):
                            message_lines.append(f"{idx}. {step}")
                        message_lines.append("")
                    else:
                        message_lines.append(f"Response:\n{new_response}")
                        message_lines.append("")
                    if source_lines:
                        message_lines.append("Nguồn:")
                        message_lines.extend(source_lines)
                        message_lines.append("")
                    message_lines.append(f"Confidence: {data.get('confidence', 'N/A')}%")
                    message_lines.append(f"Approval ID: {approval_id}")
                    message = "\n".join(message_lines).strip()
                    await self._send_message(chat_id, message)
                else:
                    await self._send_message(
                        chat_id,
                        f"Lỗi khi tìm KB: {response.status_code}"
                    )

        except Exception as e:
            logger.error("KB search failed", error=str(e), approval_id=approval_id)
            await self._send_message(chat_id, f"Có lỗi xảy ra: {str(e)}")
    
    def _normalize_kb_kind(self, kind: str) -> str:
        kind = (kind or "all").strip().lower()
        aliases = {
            "policies": "policy",
            "policy": "policy",
            "faqs": "faq",
            "faq": "faq",
            "guides": "guide",
            "guide": "guide",
            "documents": "document",
            "document": "document",
            "all": "all",
        }
        return aliases.get(kind, "all")

    def _parse_kb_callback_data(self, data: str) -> Optional[Tuple[str, str, int]]:
        if not data.startswith("kb:page:"):
            return None
        parts = data.split(":", 3)
        if len(parts) != 4:
            return None
        _, _, session_id, page_text = parts
        if not session_id:
            return None
        try:
            page = int(page_text)
        except ValueError:
            return None
        return "page", session_id, page

    def _kb_session_label(self, session: Dict[str, Any]) -> str:
        if session.get("mode") == "search":
            query = session.get("query", "").strip()
            return f"🔍 KB Search: {query or 'all'}"
        kind = session.get("search_type", "all")
        kind_label = {
            "policy": "Policies",
            "faq": "FAQs",
            "guide": "Guides",
            "document": "Documents",
            "all": "All KB",
        }.get(kind, kind.title())
        return f"📚 KB List: {kind_label}"

    def _build_kb_inline_keyboard(self, session_id: str, page: int, total_pages: int) -> Dict[str, Any]:
        buttons = []
        nav_row = []
        if page > 1:
            nav_row.append({"text": "⬅️ Prev", "callback_data": f"kb:page:{session_id}:{page - 1}"})
        if page < total_pages:
            nav_row.append({"text": "➡️ Next", "callback_data": f"kb:page:{session_id}:{page + 1}"})
        if nav_row:
            buttons.append(nav_row)
        return {"inline_keyboard": buttons} if buttons else {}

    def _build_kb_candidates_inline_keyboard(self, session_id: str, page: int, total_pages: int, candidates: list) -> Dict[str, Any]:
        buttons = []
        for item in candidates:
            candidate_id = item.get("candidate_id") or item.get("source_request_id") or item.get("id") or ""
            if not candidate_id:
                continue
            buttons.append([
                {
                    "text": "✅ Approve",
                    "callback_data": build_kb_candidate_callback_data("approve", candidate_id, session_id, page),
                },
                {
                    "text": "📝 Revise",
                    "callback_data": build_kb_candidate_callback_data("revise", candidate_id, session_id, page),
                },
            ])
        nav_row = []
        if page > 1:
            nav_row.append({"text": "⬅️ Prev", "callback_data": f"kb:page:{session_id}:{page - 1}"})
        if page < total_pages:
            nav_row.append({"text": "➡️ Next", "callback_data": f"kb:page:{session_id}:{page + 1}"})
        if nav_row:
            buttons.append(nav_row)
        return {"inline_keyboard": buttons} if buttons else {}

    def _format_kb_results_text(self, session: Dict[str, Any], results: list, page: int, total_pages: int, total: int) -> str:
        header_lines = [self._kb_session_label(session), f"Page: {page}/{total_pages}", f"Total results: {total}"]
        if session.get("template_label"):
            header_lines.append(f"Mẫu: {session['template_label']}")
        if session.get("template_hint"):
            header_lines.append(f"Gợi ý: {session['template_hint']}")
        if session.get("category"):
            header_lines.append(f"Category: {session['category']}")
        clarification = session.get("clarification") or {}
        if clarification.get("needs_clarification") and clarification.get("clarification_question"):
            header_lines.append(f"Cần thêm: {clarification['clarification_question']}")
        if session.get("query"):
            header_lines.append(f"Query: {session['query']}")
        header_lines.append("")

        body_lines = []
        start_index = (page - 1) * session.get("page_size", 5) + 1
        for idx, item in enumerate(results, start=start_index):
            card = build_kb_card(item, query=session.get("query"))
            kb_id = card.get("id") or ""
            kind = (card.get("kind") or "").upper()
            category = card.get("category") or "N/A"
            similarity = card.get("similarity")
            similarity_text = f"{similarity:.2f}" if isinstance(similarity, (int, float)) else "N/A"
            body_lines.append(f"{idx}. [{kind}] {card['title']}")
            if kb_id:
                body_lines.append(f"   ID: {kb_id}")
            body_lines.append(f"   Category: {category} | Score: {similarity_text}")
            if card.get("template_label"):
                body_lines.append(f"   Mẫu: {card['template_label']}")
            if card.get("template_hint"):
                body_lines.append(f"   Gợi ý: {card['template_hint']}")
            if card.get("summary"):
                body_lines.append(f"   Tóm tắt: {card['summary']}")
            steps = card.get("steps") or []
            if steps:
                quick_steps = "; ".join(steps[:2])
                body_lines.append(f"   Làm nhanh: {quick_steps}")
            body_lines.append("")

        if not body_lines:
            body_lines.append("Không có kết quả KB phù hợp.")

        return "\n".join(header_lines + body_lines).strip()

    def _kb_candidates_session_label(self, session: Dict[str, Any]) -> str:
        status = (session.get("status") or "pending").strip().lower()
        label = {
            "pending": "Pending Review",
            "approved": "Approved",
            "needs_revision": "Needs Revision",
            "all": "All Candidates",
        }.get(status, status.title())
        return f"🧪 KB Candidates: {label}"

    def _format_kb_candidates_text(self, session: Dict[str, Any], candidates: list, page: int, total_pages: int, total: int) -> str:
        header_lines = [self._kb_candidates_session_label(session), f"Page: {page}/{total_pages}", f"Total candidates: {total}"]
        status = session.get("status") or "pending"
        header_lines.append(f"Status: {status}")
        header_lines.append("")

        body_lines = []
        start_index = (page - 1) * session.get("page_size", 5) + 1
        for idx, item in enumerate(candidates, start=start_index):
            candidate_id = item.get("candidate_id") or item.get("source_request_id") or item.get("id") or "N/A"
            title = item.get("title") or item.get("extracted_title") or "N/A"
            category = item.get("category") or "N/A"
            confidence = item.get("confidence_score")
            confidence_text = f"{confidence:.2f}" if isinstance(confidence, (int, float)) else "N/A"
            status_text = item.get("status") or "pending"
            summary = _truncate_text(item.get("content") or item.get("review_note") or "", 180)
            body_lines.append(f"{idx}. {title}")
            body_lines.append(f"   ID: {candidate_id}")
            body_lines.append(f"   Category: {category} | Confidence: {confidence_text} | Status: {status_text}")
            if item.get("reviewer_id"):
                body_lines.append(f"   Reviewer: {item['reviewer_id']}")
            if summary:
                body_lines.append(f"   Preview: {summary}")
            body_lines.append(f"   Commands: APPROVE {candidate_id} | REVISE {candidate_id}: note")
            body_lines.append("")

        if not body_lines:
            body_lines.append("Không có KB candidate phù hợp.")
            body_lines.append("Hãy đợi job 20:00 chạy hoặc xem lại status khác bằng /kb candidates approved")

        body_lines.append("Reply hoặc bấm nút để duyệt/revise từng candidate.")
        return "\n".join(header_lines + body_lines).strip()

    async def _render_kb_candidates(self, chat_id: str, session: Dict[str, Any], page: int = 1, edit_message_id: Any = None) -> None:
        session_id = session.get("session_id")
        if not session_id:
            session_id = secrets.token_hex(3)
            session = {**session, "session_id": session_id}
            self._kb_sessions[session_id] = session
        else:
            self._kb_sessions[session_id] = session

        page_size = session.get("page_size", 5)
        offset = max(0, (page - 1) * page_size)

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.supervisor_url}/knowledge/candidates",
                    params={
                        "status": session.get("status", "pending"),
                        "limit": page_size,
                        "offset": offset,
                    },
                    timeout=30.0,
                )
            if response.status_code != 200:
                await self._send_message(chat_id, f"Lỗi khi lấy KB candidates: {response.status_code}", parse_mode=None)
                return

            data = response.json()
            candidates = data.get("candidates", []) or []
            total = int(data.get("total", len(candidates)) or 0)
            total_pages = max(1, (total + page_size - 1) // page_size)
            text = self._format_kb_candidates_text(session, candidates, page, total_pages, total)
            keyboard = self._build_kb_candidates_inline_keyboard(session_id, page, total_pages, candidates)
            if edit_message_id is not None:
                await self._edit_message_text(chat_id, edit_message_id, text, reply_markup=keyboard or None, parse_mode=None)
            else:
                await self._send_message(chat_id, text, reply_markup=keyboard or None, parse_mode=None)
        except Exception as e:
            logger.error("KB candidates render failed", error=str(e), session=session)
            await self._send_message(chat_id, f"Có lỗi xảy ra khi lấy KB candidates: {str(e)}", parse_mode=None)

    async def _handle_kb_command(self, chat_id: str, user_id: str, command: str):
        tokens = command.split()
        if len(tokens) < 2:
            await self._send_message(
                chat_id,
                "Dùng:\n/kb search <từ khoá>\n/kb list <policy|faq|guide|document|all> [page]\n/kb candidates [pending|approved|needs_revision|all] [page]\nVí dụ:\n/kb search vpn\n/kb list faq 2\n/kb candidates pending 1",
                parse_mode=None,
            )
            return

        action = tokens[1].lower()
        if action in {"candidates", "candidate", "pending"}:
            status = tokens[2].lower() if len(tokens) > 2 else "pending"
            page = 1
            if len(tokens) > 3:
                try:
                    page = max(1, int(tokens[3]))
                except ValueError:
                    page = 1
            session = {
                "mode": "candidates",
                "status": status,
                "page_size": 5,
                "user_id": user_id,
            }
            await self._render_kb_candidates(chat_id, session, page=page)
            return

        if action in {"search", "find"}:
            query = command.split(maxsplit=2)[2].strip() if len(tokens) > 2 else ""
            if not query:
                await self._send_message(chat_id, "Nhập từ khoá sau /kb search để tìm KB.", parse_mode=None)
                return
            session = {
                "mode": "search",
                "query": query,
                "search_type": None,
                "category": None,
                "tags": [],
                "page_size": 5,
                "user_id": user_id,
            }
            await self._render_kb_results(chat_id, session, page=1)
            return

        if action in {"list", "ls"}:
            kind = self._normalize_kb_kind(tokens[2] if len(tokens) > 2 else "all")
            page = 1
            if len(tokens) > 3:
                try:
                    page = max(1, int(tokens[3]))
                except ValueError:
                    page = 1
            session = {
                "mode": "list",
                "query": "",
                "search_type": kind,
                "category": None,
                "tags": [],
                "page_size": 5,
                "user_id": user_id,
            }
            await self._render_kb_results(chat_id, session, page=page)
            return

        kind = self._normalize_kb_kind(action)
        if kind != "all":
            page = 1
            if len(tokens) > 2:
                try:
                    page = max(1, int(tokens[2]))
                except ValueError:
                    page = 1
            session = {
                "mode": "list",
                "query": "",
                "search_type": kind,
                "category": None,
                "tags": [],
                "page_size": 5,
                "user_id": user_id,
            }
            await self._render_kb_results(chat_id, session, page=page)
            return

        query = command.split(maxsplit=1)[1].strip()
        session = {
            "mode": "search",
            "query": query,
            "search_type": None,
            "category": None,
            "tags": [],
            "page_size": 5,
            "user_id": user_id,
        }
        await self._render_kb_results(chat_id, session, page=1)

    async def _handle_kb_callback(self, callback_query: Dict[str, Any], action: str, session_id: str, page: int) -> bool:
        callback_query_id = callback_query.get("id")
        message = callback_query.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        message_id = message.get("message_id")

        if action != "page":
            await self._answer_callback_query(callback_query_id, "Không hỗ trợ action KB này.", show_alert=True)
            return False

        if session_id not in self._kb_sessions:
            await self._answer_callback_query(callback_query_id, "Phiên KB đã hết hạn. Hãy tìm lại.", show_alert=True)
            return False

        await self._answer_callback_query(callback_query_id, f"Đang mở trang {page}...", show_alert=False)
        await self._render_kb_results(chat_id, self._kb_sessions[session_id], page=page, edit_message_id=message_id)
        return True

    async def _render_kb_results(self, chat_id: str, session: Dict[str, Any], page: int = 1, edit_message_id: Any = None) -> None:
        session_id = session.get("session_id")
        if not session_id:
            session_id = secrets.token_hex(3)
            session = {**session, "session_id": session_id}
            self._kb_sessions[session_id] = session
        else:
            self._kb_sessions[session_id] = session

        page_size = session.get("page_size", 5)
        offset = max(0, (page - 1) * page_size)
        payload = {
            "query": session.get("query", ""),
            "search_type": session.get("search_type", "all"),
            "category": session.get("category"),
            "tags": session.get("tags") or [],
            "limit": page_size,
            "offset": offset,
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.supervisor_url}/knowledge/search",
                    json=payload,
                    timeout=30.0,
                )

            if response.status_code != 200:
                await self._send_message(chat_id, f"Lỗi khi tìm KB: {response.status_code}", parse_mode=None)
                return

            data = response.json()
            results = data.get("results", []) or []
            total = int(data.get("total", len(results)) or 0)
            total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
            current_page = max(1, min(page, total_pages))
            session["template_label"] = data.get("template_label", "")
            session["template_hint"] = data.get("template_hint", "")
            session["template_id"] = data.get("template_id", "")
            session["template_score"] = data.get("template_score", 0.0)
            session["template_terms"] = data.get("template_terms", [])
            session["clarification"] = data.get("clarification", {}) or {}
            if current_page != page and total > 0:
                offset = max(0, (current_page - 1) * page_size)
                payload["offset"] = offset
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.supervisor_url}/knowledge/search",
                        json=payload,
                        timeout=30.0,
                    )
                if response.status_code != 200:
                    await self._send_message(chat_id, f"Lỗi khi tìm KB: {response.status_code}", parse_mode=None)
                    return
                data = response.json()
                results = data.get("results", []) or []

            text = self._format_kb_results_text(session, results, current_page, total_pages, total)
            keyboard = self._build_kb_inline_keyboard(session_id, current_page, total_pages)

            if edit_message_id is not None:
                await self._edit_message_text(chat_id, edit_message_id, text, reply_markup=keyboard or None, parse_mode=None)
            else:
                await self._send_message(chat_id, text, reply_markup=keyboard or None, parse_mode=None)
        except Exception as e:
            logger.error("KB browse failed", error=str(e), session=session)
            await self._send_message(chat_id, f"Có lỗi xảy ra khi tìm KB: {str(e)}", parse_mode=None)

    async def _handle_super_analytics_command(self, chat_id: str, command: str) -> None:
        """Fetch a quick supervisor analytics report for Telegram."""
        tokens = command.split()
        days = 1
        if len(tokens) > 1:
            try:
                days = max(1, min(90, int(tokens[1])))
            except ValueError:
                days = 1

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.supervisor_url}/metrics/dashboard/boss-report",
                    params={"days": days},
                    timeout=20.0,
                )

            if response.status_code != 200:
                await self._send_message(chat_id, f"Lỗi khi lấy super analytics: {response.status_code}", parse_mode=None)
                return

            report = (response.text or "").strip()
            if not report:
                report = "Không có dữ liệu analytics hiện tại."
            if len(report) > 3900:
                report = report[:3900] + "..."

            header = f"📈 Super Analytics ({days} ngày)"
            await self._send_message(chat_id, f"{header}\n\n{report}", parse_mode=None)
        except Exception as e:
            logger.error("Super analytics failed", error=str(e), command=command)
            await self._send_message(chat_id, f"Có lỗi xảy ra khi lấy super analytics: {str(e)}", parse_mode=None)

    async def _buffer_conversation_message(
        self,
        thread_id: str,
        chat_id: str,
        user_id: str,
        display_name: str,
        text: str,
        metadata: Dict[str, Any],
    ) -> None:
        """Buffer Telegram user messages for a short window so multi-line bursts are merged before calling Supervisor."""
        now = datetime.now(timezone.utc)
        buffer = self._conversation_buffers.setdefault(
            thread_id,
            {
                "thread_id": thread_id,
                "chat_id": chat_id,
                "user_id": user_id,
                "display_name": display_name,
                "metadata": {},
                "messages": [],
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "message_mode": "statement",
            },
        )

        buffer["chat_id"] = chat_id
        buffer["user_id"] = user_id
        buffer["display_name"] = display_name
        buffer["updated_at"] = now.isoformat()
        buffer["metadata"] = {**buffer.get("metadata", {}), **(metadata or {})}
        buffer["message_mode"] = self._message_mode_detector.detect_message_mode(text)
        buffer["messages"].append(
            {
                "text": text,
                "message_mode": buffer["message_mode"],
                "timestamp": now.isoformat(),
            }
        )

        session_id = f"telegram_{user_id}"
        self.session_store.add_message(
            session_id=session_id,
            role="user",
            content=text,
            metadata={
                **(metadata or {}),
                "message_mode": buffer["message_mode"],
                "buffered": True,
                "buffer_thread_id": thread_id,
            },
        )

        await self._schedule_conversation_flush(thread_id)

    async def _schedule_conversation_flush(self, thread_id: str) -> None:
        existing = self._conversation_flush_tasks.get(thread_id)
        if existing and not existing.done():
            existing.cancel()
        task = asyncio.create_task(self._flush_conversation_after_delay(thread_id))
        self._conversation_flush_tasks[thread_id] = task

    async def _flush_conversation_after_delay(self, thread_id: str) -> None:
        try:
            await asyncio.sleep(self._buffer_delay_seconds)
            await self._flush_conversation_buffer(thread_id)
        except asyncio.CancelledError:
            return

    async def _flush_conversation_buffer(self, thread_id: str) -> None:
        buffer = self._conversation_buffers.get(thread_id)
        if not buffer or not buffer.get("messages"):
            return

        last_updated = buffer.get("updated_at")
        if last_updated:
            updated_at = datetime.fromisoformat(last_updated)
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - updated_at).total_seconds() < self._buffer_delay_seconds - 1:
                await self._schedule_conversation_flush(thread_id)
                return

        messages = buffer.get("messages", [])
        merged_text = "\n".join(msg.get("text", "").strip() for msg in messages if msg.get("text", "").strip())
        if not merged_text:
            self._conversation_buffers.pop(thread_id, None)
            self._conversation_flush_tasks.pop(thread_id, None)
            return

        chat_id = buffer.get("chat_id", "")
        user_id = buffer.get("user_id", "")
        display_name = buffer.get("display_name", user_id)
        metadata = dict(buffer.get("metadata", {}))
        metadata.update(
            {
                "platform": "telegram",
                "chat_id": chat_id,
                "thread_buffered": True,
                "buffer_delay_seconds": self._buffer_delay_seconds,
                "buffer_message_count": len(messages),
                "buffer_message_modes": [msg.get("message_mode") for msg in messages if msg.get("message_mode")],
                "message_mode": buffer.get("message_mode", "statement"),
            }
        )

        try:
            reply = await self._call_supervisor(user_id, display_name, merged_text, thread_id, metadata)
            if reply:
                await self._send_message(chat_id, reply)
        finally:
            self._conversation_buffers.pop(thread_id, None)
            task = self._conversation_flush_tasks.pop(thread_id, None)
            if task and not task.done():
                task.cancel()

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
                    timeout=30.0,
                )

                if response.status_code == 200:
                    payload = response.json()
                    if payload.get("status") == "skipped":
                        return ""
                    return payload.get("message", payload.get("response", "No response"))
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

    async def _edit_message_text(self, chat_id: str, message_id: Any, text: str, reply_markup: Optional[Dict[str, Any]] = None, parse_mode: Optional[str] = "Markdown"):

        async with httpx.AsyncClient() as client:
            payload = {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if reply_markup is not None:
                payload["reply_markup"] = reply_markup
            await client.post(
                f"{self.api_base}/editMessageText",
                json=payload,
            )
    
    async def _send_message(self, chat_id: str, text: str, reply_markup: Optional[Dict[str, Any]] = None, parse_mode: Optional[str] = "Markdown"):
        """Send a message via Telegram"""
        async with httpx.AsyncClient() as client:
            payload = {
                "chat_id": chat_id,
                "text": text,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if reply_markup is not None:
                payload["reply_markup"] = reply_markup
            await client.post(
                f"{self.api_base}/sendMessage",
                json=payload,
            )


__all__ = [
    "TelegramAdapter",
    "build_approval_message_text",
    "build_approval_inline_keyboard",
    "parse_approval_callback_data",
    "build_kb_candidate_callback_data",
    "parse_kb_candidate_callback_data",
    "parse_kb_candidate_text_action",
    "build_kb_candidate_force_reply_markup",
    "build_kb_candidate_revision_prompt",
]
