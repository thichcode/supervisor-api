     1|"""
     2|Telegram Platform Adapter
     3|"""
     4|
     5|import asyncio
     6|import hmac
     7|from datetime import datetime, timezone
     8|import os
     9|from typing import Optional, Dict, Any, Tuple
    10|import secrets
    11|import structlog
    12|import httpx
    13|
    14|from src.config import get_settings
    15|from src.core.conversation_continuity import ConversationContinuityEvaluator
    16|from src.core.kb_presentation import build_kb_card, format_kb_response
    17|logger = structlog.get_logger()
    18|
    19|
    20|def _get_approval_secret() -> str:
    21|    """Get secret for HMAC verification of approval callbacks."""
    22|    settings = get_settings()
    23|    # Use dedicated secret or fallback to hmac_secret
    24|    return getattr(settings, "telegram_approval_secret", "") or settings.hmac_secret or "default-approval-secret"
    25|
    26|
    27|def generate_approval_hmac(approval_id: str, length: int = 10) -> str:
    28|    """Generate a short HMAC for approval callback verification.
    29|
    30|    Telegram callback_data is limited to 64 bytes, so we keep the signature short.
    31|    """
    32|    secret = _get_approval_secret()
    33|    return hmac.new(secret.encode(), approval_id.encode(), "sha256").hexdigest()[:length]
    34|
    35|
    36|def verify_approval_hmac(approval_id: str, hmac_sig: str) -> bool:
    37|    """Verify HMAC signature for approval callback.
    38|
    39|    Accept both the new shorter signature and legacy longer signatures so old cards
    40|    continue to work after deployment.
    41|    """
    42|    if not hmac_sig:
    43|        return False
    44|    for length in (10, 16):
    45|        expected = generate_approval_hmac(approval_id, length=length)
    46|        if hmac.compare_digest(expected, hmac_sig):
    47|            return True
    48|    return False
    49|
    50|
    51|def get_allowed_approval_chat_ids() -> set[str]:
    52|    """Get allowed chat IDs for approval actions."""
    53|    settings = get_settings()
    54|    chat_ids = getattr(settings, "telegram_approval_chat_ids", "") or ""
    55|    if not chat_ids:
    56|        return set()
    57|    return set(cid.strip() for cid in chat_ids.split(",") if cid.strip())
    58|
    59|
    60|def _truncate_text(text: str, limit: int = 120) -> str:
    61|    if not text:
    62|        return ""
    63|    if len(text) <= limit:
    64|        return text
    65|    return text[: max(0, limit - 1)].rstrip() + "…"
    66|
    67|
    68|
    69|def build_approval_message_text(approval, compact: Optional[bool] = None) -> str:
    70|    """Build the Telegram approval card text."""
    71|    confidence_pct = round((approval.confidence * 100) if approval.confidence <= 1 else approval.confidence, 1)
    72|    threshold_pct = round((approval.threshold * 100) if approval.threshold <= 1 else approval.threshold, 1)
    73|    metadata = getattr(approval, "metadata", None) or {}
    74|    thread_id = metadata.get("thread_id", "")
    75|    platform = metadata.get("platform", "")
    76|    chat_type = metadata.get("chat_type", "")
    77|    chat_scope = metadata.get("chat_scope", "")
    78|    group_chat = metadata.get("group_chat")
    79|    risk_level = metadata.get("risk_level", "")
    80|    kb_sources = metadata.get("kb_sources", [])
    81|    kb_evidence = metadata.get("kb_evidence", [])
    82|    user_id = getattr(approval, "user_id", "") or metadata.get("user_id", "")
    83|    display_name = getattr(approval, "display_name", "") or metadata.get("display_name", "")
    84|
    85|    is_group_chat = group_chat is True
    86|    is_compact = compact if compact is not None else is_group_chat
    87|    chat_mode_label = "Group chat" if is_group_chat else "Direct message" if group_chat is False else "Chat"
    88|    header = "⚠️ Group Chat Approval Required" if is_group_chat else ("⚠️ Direct Message Approval Required" if group_chat is False else "⚠️ Approval Required")
    89|    scope_note = (
    90|        "⚠️ This request came from a *group chat*. Verify the requester, thread context, and impact before approving."
    91|        if is_group_chat
    92|        else (
    93|            "This request came from a direct message."
    94|            if group_chat is False
    95|            else ""
    96|        )
    97|    )
    98|
    99|    kb_lines = []
   100|    if kb_sources:
   101|        kb_lines.append("KB Sources:")
   102|        for idx, source in enumerate(kb_sources[:3], start=1):
   103|            title = source.get("title") or source.get("name") or source.get("id") or "N/A"
   104|            similarity = source.get("similarity")
   105|            similarity_text = f" ({similarity:.2f})" if isinstance(similarity, (int, float)) else ""
   106|            kb_lines.append(f"{idx}. {title}{similarity_text}")
   107|    if kb_evidence:
   108|        kb_lines.append("KB Evidence:")
   109|        for idx, item in enumerate(kb_evidence[:3], start=1):
   110|            title = item.get("title") or item.get("id") or "N/A"
   111|            similarity = item.get("similarity")
   112|            similarity_text = f" ({similarity:.2f})" if isinstance(similarity, (int, float)) else ""
   113|            kb_lines.append(f"{idx}. {title}{similarity_text}")
   114|
   115|    context_lines = [
   116|        f"Approval ID: {approval.id}",
   117|        f"Request ID: {approval.request_id}",
   118|        f"Display Name: {display_name or 'N/A'}",
   119|        f"User ID: {user_id or 'N/A'}",
   120|        f"Thread ID: {thread_id or 'N/A'}",
   121|        f"Chat Mode: {chat_mode_label}",
   122|    ]
   123|    if platform:
   124|        context_lines.append(f"Platform: {platform}")
   125|    if chat_type:
   126|        context_lines.append(f"Chat Type: {chat_type}")
   127|    if chat_scope:
   128|        context_lines.append(f"Chat Scope: {chat_scope}")
   129|    if group_chat is not None:
   130|        context_lines.append(f"Group Chat: {group_chat}")
   131|
   132|    if is_compact:
   133|        summary_original = _truncate_text(approval.original_message, 100)
   134|        summary_ai = _truncate_text(approval.ai_response, 120)
   135|        return (
   136|            f"{header}\n\n"
   137|            + "\n".join(context_lines)
   138|            + f"\nRisk: {risk_level or 'N/A'}\n"
   139|            + f"Confidence: {confidence_pct}% (threshold: {threshold_pct}%)"
   140|            + (f"\n\n{scope_note}" if scope_note else "")
   141|            + f"\n\nOriginal (preview):\n{summary_original or 'N/A'}"
   142|            + f"\n\nAI (preview):\n{summary_ai or 'N/A'}\n\n"
   143|            + "Tap *View full context* to expand this card."
   144|        )
   145|
   146|    kb_section = "\n".join(kb_lines)
   147|    if kb_section:
   148|        kb_section = f"\n\n{kb_section}"
   149|
   150|    note_section = f"\n\n{scope_note}" if scope_note else ""
   151|
   152|    return (
   153|        f"{header}\n\n"
   154|        + "\n".join(context_lines)
   155|        + f"\nRisk: {risk_level or 'N/A'}\n"
   156|        + f"Confidence: {confidence_pct}% (threshold: {threshold_pct}%)"
   157|        + note_section
   158|        + f"\n\nOriginal:\n{approval.original_message}\n\n"
   159|        + f"AI Response:\n{approval.ai_response}{kb_section}\n\n"
   160|        + "Use the buttons below to approve or reject."
   161|    )
   162|
   163|
   164|
   165|def build_approval_inline_keyboard(approval_id: str, compact: bool = False, group_chat: Optional[bool] = None) -> Dict[str, Any]:
   166|    """Build the inline keyboard for approval actions with HMAC verification."""
   167|    hmac_sig = generate_approval_hmac(approval_id)
   168|    buttons = [
   169|        [
   170|            {"text": "✅ Approve", "callback_data": f"approval:approve:{approval_id}:{hmac_sig}"},
   171|            {"text": "🚫 Reject", "callback_data": f"approval:reject:{approval_id}:{hmac_sig}"},
   172|        ]
   173|    ]
   174|    if group_chat is True and compact:
   175|        buttons.append([
   176|            {"text": "🔍 Search KB", "callback_data": f"approval:kb:{approval_id}:{hmac_sig}"},
   177|        ])
   178|        buttons.append([
   179|            {"text": "🔎 View full context", "callback_data": f"approval:ctx:{approval_id}:{hmac_sig}"},
   180|        ])
   181|    else:
   182|        buttons.append([
   183|            {"text": "🔍 Search KB", "callback_data": f"approval:kb:{approval_id}:{hmac_sig}"},
   184|        ])
   185|    return {"inline_keyboard": buttons}
   186|
   187|
   188|def build_rating_inline_keyboard(request_id: str, thread_id: str) -> Dict[str, Any]:
   189|    """Build inline keyboard for star rating feedback.
   190|    
   191|    Creates 3 buttons: ⭐ (1 star), ⭐⭐ (2 stars), ⭐⭐⭐ (3 stars).
   192|    Stores request_id and thread_id in callback_data for feedback linking.
   193|    """
   194|    hmac_sig = hmac.new(
   195|        _get_approval_secret().encode(),
   196|        f"{request_id}:{thread_id}".encode(),
   197|        "sha256"
   198|    ).hexdigest()[:8]
   199|    
   200|    buttons = [
   201|        [
   202|            {"text": "⭐", "callback_data": f"rating:1:{request_id}:{thread_id}:{hmac_sig}"},
   203|            {"text": "⭐⭐", "callback_data": f"rating:2:{request_id}:{thread_id}:{hmac_sig}"},
   204|            {"text": "⭐⭐⭐", "callback_data": f"rating:3:{request_id}:{thread_id}:{hmac_sig}"},
   205|        ]
   206|    ]
   207|    return {"inline_keyboard": buttons}
   208|
   209|
   210|def parse_rating_callback_data(data: str) -> Optional[Tuple[int, str, str, str]]:
   211|    """Parse rating callback data.
   212|    
   213|    Expected format: rating:{score}:{request_id}:{thread_id}:{hmac_sig}
   214|    Returns: (score, request_id, thread_id, hmac_sig) or None if invalid.
   215|    """
   216|    if not data.startswith("rating:"):
   217|        return None
   218|    
   219|    parts = data.split(":")
   220|    if len(parts) != 5:
   221|        return None
   222|    
   223|    try:
   224|        score = int(parts[1])
   225|        if score not in (1, 2, 3):
   226|            return None
   227|    except ValueError:
   228|        return None
   229|    
   230|    request_id = parts[2]
   231|    thread_id = parts[3]
   232|    hmac_sig = parts[4]
   233|    
   234|    # Verify HMAC
   235|    expected = hmac.new(
   236|        _get_approval_secret().encode(),
   237|        f"{request_id}:{thread_id}".encode(),
   238|        "sha256"
   239|    ).hexdigest()[:8]
   240|    
   241|    if not hmac.compare_digest(expected, hmac_sig):
   242|        return None
   243|    
   244|    return (score, request_id, thread_id, hmac_sig)
   245|
   246|
   247|def build_kb_search_prompt(approval_id: str) -> str:
   248|    """Build prompt for KB search."""
   249|    return (
   250|        "🔍 Search Knowledge Base\n\n"
   251|        f"Approval ID: {approval_id}\n\n"
   252|        "Nhập từ khóa để tìm kiếm trong Knowledge Base.\n"
   253|        "Hệ thống sẽ tìm kết quả và tạo câu trả lời mới."
   254|    )
   255|
   256|
   257|
   258|def build_kb_force_reply_markup(approval_id: str) -> Dict[str, Any]:
   259|    """Build a ForceReply payload so Telegram shows an inline text box for the KB query."""
   260|    return {
   261|        "force_reply": True,
   262|        "input_field_placeholder": "Nhập từ khóa KB để gợi ý cho user...",
   263|        "selective": True,
   264|        "kb_approval_id": approval_id,
   265|    }
   266|
   267|
   268|
   269|def build_kb_candidate_force_reply_markup(candidate_id: str) -> Dict[str, Any]:
   270|    """Build a ForceReply payload for KB candidate revision notes."""
   271|    return {
   272|        "force_reply": True,
   273|        "input_field_placeholder": "Nhập lý do revise / bổ sung cho KB candidate...",
   274|        "selective": True,
   275|        "kb_candidate_id": candidate_id,
   276|    }
   277|
   278|
   279|
   280|def build_kb_candidate_revision_prompt(candidate_id: str) -> str:
   281|    return (
   282|        "📝 Revise KB Candidate\n\n"
   283|        f"Candidate ID: {candidate_id}\n\n"
   284|        "Nhập nhận xét ngắn để revise KB (thiếu gì, cần sửa gì, hoặc keyword bổ sung)."
   285|    )
   286|
   287|
   288|
   289|def build_kb_candidate_callback_data(
   290|    action: str,
   291|    candidate_id: str,
   292|    session_id: Optional[str] = None,
   293|    page: Optional[int] = None,
   294|) -> str:
   295|    """Build callback data for KB candidate actions.
   296|
   297|    The optional session/page suffix lets list actions refresh the same page in place
   298|    after approve/revise, while keeping the legacy 3-part form for notification cards.
   299|    """
   300|    action = (action or "").strip().lower()
   301|    candidate_id = (candidate_id or "").strip()
   302|    parts = ["kb_candidate", action, candidate_id]
   303|    if session_id:
   304|        parts.append(session_id.strip())
   305|        if page is not None:
   306|            parts.append(str(max(1, int(page))))
   307|    return ":".join(parts)
   308|
   309|
   310|def parse_kb_candidate_callback_data(data: str) -> Optional[Tuple[str, str, Optional[str], Optional[int]]]:
   311|    """Parse callback data for knowledge candidate actions."""
   312|    if not data:
   313|        return None
   314|    parts = data.split(":")
   315|    if len(parts) not in {3, 4, 5} or parts[0] != "kb_candidate":
   316|        return None
   317|    action, candidate_id = parts[1], parts[2]
   318|    if action not in {"approve", "revise"} or not candidate_id:
   319|        return None
   320|    session_id: Optional[str] = None
   321|    page: Optional[int] = None
   322|    if len(parts) >= 4:
   323|        session_id = parts[3] or None
   324|    if len(parts) == 5:
   325|        try:
   326|            page = max(1, int(parts[4]))
   327|        except ValueError:
   328|            return None
   329|    return action, candidate_id, session_id, page
   330|
   331|
   332|
   333|def parse_kb_candidate_text_action(text: str) -> Optional[Tuple[str, str, str]]:
   334|    """Parse plain text review commands like APPROVE <id> or REVISE <id>: note."""
   335|    stripped = (text or "").strip()
   336|    if not stripped:
   337|        return None
   338|    upper = stripped.upper()
   339|    if upper.startswith("APPROVE "):
   340|        candidate_id = stripped.split(None, 1)[1].strip()
   341|        if candidate_id:
   342|            return "approve", candidate_id, ""
   343|        return None
   344|    if upper.startswith("REVISE "):
   345|        remainder = stripped.split(None, 1)[1].strip()
   346|        if not remainder:
   347|            return None
   348|        if ":" in remainder:
   349|            candidate_id, note = remainder.split(":", 1)
   350|        else:
   351|            candidate_id, note = remainder, ""
   352|        candidate_id = candidate_id.strip()
   353|        note = note.strip()
   354|        if candidate_id:
   355|            return "revise", candidate_id, note
   356|    return None
   357|
   358|
   359|def parse_approval_callback_data(data: str) -> Optional[Tuple[str, str, str]]:
   360|    """Parse Telegram callback data for approval actions (with HMAC).
   361|    
   362|    Returns: (action, approval_id, hmac_signature)
   363|    """
   364|    if not data:
   365|        return None
   366|
   367|    action_aliases = {
   368|        "approve": "approve",
   369|        "reject": "reject",
   370|        "search_kb": "search_kb",
   371|        "kb": "search_kb",
   372|        "view_full_context": "view_full_context",
   373|        "ctx": "view_full_context",
   374|    }
   375|
   376|    # New format: approval:action:ID:HMAC (4 parts)
   377|    parts = data.split(":")
   378|    if len(parts) == 4 and parts[0] == "approval":
   379|        action = action_aliases.get(parts[1])
   380|        approval_id, hmac_sig = parts[2], parts[3]
   381|        if action and approval_id:
   382|            return action, approval_id, hmac_sig
   383|    # Legacy format (without HMAC) for backward compat: approval:action:ID
   384|    if len(parts) == 3 and parts[0] == "approval":
   385|        action = action_aliases.get(parts[1])
   386|        approval_id = parts[2]
   387|        if action and approval_id:
   388|            return action, approval_id, ""  # Empty HMAC = legacy
   389|    return None
   390|
   391|
   392|class TelegramAdapter:
   393|    """
   394|    Telegram bot adapter for Supervisor
   395|    """
   396|    
   397|    def __init__(
   398|        self,
   399|        token: str,
   400|        session_store,
   401|        supervisor_url: str,
   402|        api_key: Optional[str] = None
   403|    ):
   404|        self.token = token
   405|        self.session_store = session_store
   406|        self.supervisor_url = supervisor_url
   407|        self.api_key = api_key
   408|        
   409|        self.api_base = f"https://api.telegram.org/bot{token}"
   410|        self.is_running = False
   411|        self._offset = 0
   412|        self._task: Optional[asyncio.Task] = None
   413|        self._pending_kb_search: Dict[str, str] = {}
   414|        self._pending_kb_revision: Dict[str, Dict[str, Any]] = {}
   415|        self._conversation_buffers: Dict[str, Dict[str, Any]] = {}
   416|        self._conversation_flush_tasks: Dict[str, asyncio.Task] = {}
   417|        self._buffer_delay_seconds = 60
   418|        self._message_mode_detector = ConversationContinuityEvaluator()
   419|        self._kb_sessions: Dict[str, Dict[str, Any]] = {}
   420|        
   421|        # Verbose mode for streaming app logs to Telegram
   422|        self._verbose_mode: bool = False
   423|        self._verbose_chat_ids: set[str] = set()
        
        # Proxy support - read from environment
        self._proxy_url = self._get_proxy_from_env()
        self._http_client: Optional[httpx.AsyncClient] = None
        
        if self._proxy_url:
            logger.info("Telegram adapter using proxy", proxy=self._proxy_url)
    
   424|        
   425|        # Proxy support - read from environment
   426|        self._proxy_url = self._get_proxy_from_env()
   427|        self._http_client: Optional[httpx.AsyncClient] = None
   428|        
   429|        if self._proxy_url:
   430|            logger.info("Telegram adapter using proxy", proxy=self._proxy_url)
   431|        # Test connection
   432|        try:
   433|            async with asyncio.timeout(10):
   434|                client = await self._get_http_client()
   435|                resp = await client.get(f"{self.api_base}/getMe")
   436|                    if resp.status_code != 200:
   437|                        logger.error(
   438|                            "Telegram auth failed",
   439|                            status=resp.status_code,
   440|                            body=resp.text[:500],
   441|                        )
   442|                        return
   443|                    
   444|                    me = resp.json()
   445|                    logger.info("Telegram bot started", username=me.get("result", {}).get("username"))
   446|                    await self._register_bot_commands()
   447|                    
   448|        except Exception as e:
   449|            logger.error(
   450|                "Failed to start Telegram",
   451|                error=str(e),
   452|                error_type=type(e).__name__,
   453|                error_repr=repr(e),
   454|            )
   455|            return
   456|        
   457|        self.is_running = True
   458|        
   459|        if self._proxy_url:
   460|            logger.info("Telegram adapter using proxy", proxy=self._proxy_url)
   461|    
   462|    def _get_proxy_from_env(self) -> Optional[str]:
   463|        """Read proxy URL from environment variables."""
   464|        proxy = (
   465|            os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or
   466|            os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
   467|        )
   468|        return proxy.strip() if proxy else None
   469|    
   470|    async def _get_http_client(self) -> httpx.AsyncClient:
   471|        """Get or create shared HTTP client with proxy support."""
   472|        if self._http_client is None or self._http_client.is_closed:
   473|            # httpx will use proxy from env vars by default (trust_env=True)
   474|            # But we explicitly set it if configured
   475|            proxies = self._proxy_url if self._proxy_url else None
   476|            self._http_client = httpx.AsyncClient(
   477|                proxies=proxies,
   478|                timeout=30.0,
   479|                trust_env=True  # Allow reading proxy from env vars
   480|            )
   481|        return self._http_client
   482|    
   483|    async def _close_http_client(self):
   484|        """Close the shared HTTP client."""
   485|        if self._http_client and not self._http_client.is_closed:
   486|            await self._http_client.aclose()
   487|            self._http_client = None
   488|    
   489|    async def stop(self):
   490|        """Stop the Telegram bot"""
   491|        self.is_running = False
   492|        
   493|        # Close HTTP client
   494|        await self._close_http_client()
   495|        
   496|        if self._task:
   497|            self._task.cancel()
   498|            try:
   499|                await self._task
   500|            except asyncio.CancelledError:
   501|