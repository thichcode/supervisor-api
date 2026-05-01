"""Minimal reasoning loop orchestrator (v1).

Implements a lightweight plan -> act -> observe flow that can be gated by
ENABLE_REASONING_LOOP and delegated from Supervisor.process.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
import json
import time

import structlog

logger = structlog.get_logger(__name__)

from src.core import InputPayload, OutputPayload
from src.config import get_settings
from src.memory import MemoryContext as MemoryContextModel
from src.tools.tool_registry import ToolRegistry, build_default_registry
from src.core.subagent_delegation import (
    SubagentPool,
    SubagentAggregator,
    SubagentTask,
    SubagentResult,
    build_multi_source_tasks,
)


if TYPE_CHECKING:
    from src.core.supervisor import Supervisor


@dataclass
class ReasoningState:
    """Internal state for one reasoning-loop execution."""

    decision: str = "direct"
    answer: str = ""
    confidence: float = 0.5  # ← FIX v2: start at 0.5, not 0.8
    kb_hit: bool = False
    kb_sources: list[dict[str, Any]] = field(default_factory=list)
    qa_needs_review: bool = False
    pattern_hit: bool = False
    agents_used: list[str] = field(default_factory=list)


@dataclass
class ToolPlan:
    """Planned tool execution with basic validation result."""

    tool_name: str | None
    arguments: dict[str, Any] = field(default_factory=dict)
    valid: bool = True
    missing_fields: list[str] = field(default_factory=list)
    reason: str = ""


class ReasoningLoopOrchestrator:
    """Minimal plan/act/observe orchestrator for Supervisor."""

    def __init__(self, supervisor: "Supervisor", tool_registry: ToolRegistry | None = None):
        self.supervisor = supervisor
        self.tool_registry = tool_registry or build_default_registry()

    async def _generate_response(
        self,
        payload: InputPayload,
        memory: Any,
        context: dict,
        policy: dict,
        knowledge: dict,
        llm: Any,
    ) -> tuple[str, float]:
        """
        Generate a response using DraftAgent + QAAgent validation.
        
        This is the core response generation pipeline:
        1. DraftAgent.generate() creates initial draft
        2. QAAgent.refine() validates and refines
        
        Args:
            payload: Input payload with user message
            memory: Memory context
            context: Context from ContextAgent
            policy: Policy from PolicyAgent
            knowledge: Knowledge from KnowledgeAgent
            llm: LLM instance for generation
            
        Returns:
            Tuple of (answer, confidence)
        """
        # Step 1: Generate draft
        draft = await self.supervisor.draft_agent.generate(
            payload,
            context,
            policy,
            knowledge,
            llm,
        )
        
        if not draft:
            return "(no draft generated)", 0.3
        
        # Step 2: Validate and refine
        validation = await self.supervisor._enhanced_validate(
            draft,
            payload,
            context,
            policy,
            knowledge,
        )
        
        answer = self.supervisor.qa_agent.refine(validation, payload, context)
        confidence = validation.get("confidence", 0.5)
        
        return answer, confidence

    def _should_delegate_to_subagent_pool(self, payload: InputPayload) -> bool:
        """Detect multi-source / complex tasks that benefit from parallel subagents.

        Heuristics: keywords like 'tổng hợp', 'so sánh', 'viết báo cáo',
        'from multiple sources', or explicit mention of >1 source/URL.
        """
        if not getattr(get_settings(), "enable_subagent_delegation", True):
            return False
        text = (payload.message.text or "").lower()
        triggers = [
            "tổng hợp", "so sánh", "báo cáo", "từ nhiều nguồn",
            "viết báo cáo", "multiple sources", "combine", "aggregate",
            "từ 3 nguồn", "từ 2 nguồn", "từ các nguồn",
        ]
        return any(t in text for t in triggers)

    def _build_subagent_tasks(
        self,
        payload: InputPayload,
        knowledge: dict[str, Any],
    ) -> list[SubagentTask]:
        """Build parallel tasks for multi-source / complex queries.

        Uses URL context and knowledge sources as seed tasks.
        """
        text = payload.message.text or ""
        # Extract quoted or URL-like sources
        import re
        urls = re.findall(r'https?://[^\s]+', text)
        quoted = re.findall(r'"([^"]+)"', text)
        sources = urls + quoted
        if len(sources) < 2:
            # Fall back to semantic split based on knowledge results
            kb_sources = knowledge.get("knowledge_results", [])
            if len(kb_sources) >= 2:
                sources = [
                    s.get("title", f"source_{i}")
                    for i, s in enumerate(kb_sources[:3])
                ]
            else:
                return []
        return build_multi_source_tasks(
            instruction=text[:500],
            sources=sources,
            base_context={"user_id": payload.user.id, "thread_id": payload.conversation.thread_id},
        )

    def _plan_tool(self, knowledge: dict[str, Any], payload: InputPayload) -> ToolPlan:
        """Plan tool selection and validate required arguments."""
        # Legacy system_query path
        if knowledge.get("system_query_requested"):
            query_type = knowledge.get("query_type")
            if not query_type:
                return ToolPlan(
                    tool_name="system_query",
                    valid=False,
                    missing_fields=["query_type"],
                    reason="missing_query_type",
                )
            allowed_query_types = {
                "n8n",
                "system",
                "status",
                "metrics",
                "itc",
                "workflow",
            }
            if str(query_type).lower() not in allowed_query_types:
                return ToolPlan(
                    tool_name="system_query",
                    valid=False,
                    missing_fields=["query_type"],
                    reason=f"unsupported_query_type:{query_type}",
                )
            return ToolPlan(
                tool_name="system_query",
                arguments={"query_type": query_type},
                valid=True,
                reason="tool_planned",
            )

        # Hermes-style registry tool detection via simple keyword heuristic
        text = (payload.message.text or "").lower()
        registry_tools = {t["name"] for t in self.tool_registry.list_tools()}

        if "đọc file" in text or "read file" in text:
            return ToolPlan(tool_name="read_file", arguments={"path": text.split("file")[-1].strip()}, valid=True, reason="registry_tool_planned")
        if "tìm kiếm" in text or "web search" in text or "search web" in text:
            query = text.split("tìm kiếm")[-1].strip() if "tìm kiếm" in text else text.split("web search")[-1].strip()
            return ToolPlan(tool_name="web_search", arguments={"query": query, "top_k": 3}, valid=True, reason="registry_tool_planned")
        if "tính" in text or "calculate" in text or "execute code" in text or "chạy code" in text:
            return ToolPlan(tool_name="execute_code", arguments={"code": text}, valid=True, reason="registry_tool_planned")

        return ToolPlan(tool_name=None, reason="no_tool_requested")

    def _should_use_llm_tool_planning(self) -> bool:
        """Gate: enable LLM-driven tool planning via config flag."""
        return bool(getattr(get_settings(), "enable_llm_tool_planning", False))

    async def plan_with_tools(
        self,
        payload: InputPayload,
        memory: MemoryContextModel,
    ) -> tuple[str, float, list[str]]:
        """
        LLM-driven tool planning (Hermes-style).

        Sends the user's message + tool schemas to the LLM, which decides
        whether to call a tool or answer directly.

        Returns (answer, confidence, agents_used).
        Falls back to ""/0.5/[] if disabled or on error.
        """
        if not self._should_use_llm_tool_planning():
            return "", 0.5, []

        try:
            # Get tool schemas (OpenAI format) from the registry
            schemas = self.tool_registry.get_schemas()
            if not schemas:
                return "", 0.5, []

            # Build a focused system prompt for tool planning
            system_prompt = (
                "Bạn là trợ lý IT Support. "
                "Bạn có quyền gọi các công cụ (tools) để hoàn thành tác vụ. "
                "Nếu câu hỏi cần thao tác file, đọc file, chạy lệnh, hoặc tìm kiếm web — "
                "hãy gọi tool phù hợp. "
                "Nếu chỉ cần trả lời bằng kiến thức thì trả lời trực tiếp (không gọi tool). "
                "Trả lời bằng tiếng Việt."
            )

            user_message = payload.message.text or ""

            # Build conversation with tool results for multi-turn
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]

            max_turns = int(getattr(get_settings(), "llm_tool_max_turns", 3))
            agents_used: list[str] = []
            current_turn = 0

            while current_turn < max_turns:
                current_turn += 1

                response = await self.supervisor._llm.complete(
                    system_prompt=system_prompt,
                    user_message="",  # messages already has the full conversation
                    tools=schemas,
                    temperature=0.3,
                )

                if not response.tool_calls:
                    # LLM answered directly without tool calls
                    answer = response.content or ""
                    confidence = min(0.95, response.confidence + 0.05)
                    return answer, confidence, agents_used

                # Execute each tool call and append results to messages
                for tc in response.tool_calls:
                    tool_name = tc.name
                    tool_args = tc.arguments
                    agents_used.append(tool_name)

                    # Execute via registry
                    try:
                        result = await self.tool_registry.execute(
                            tool_name,
                            tool_args,
                            approval_context={
                                "request_id": payload.request_id,
                                "user_id": payload.user.id,
                                "display_name": payload.user.display_name,
                                "original_message": user_message,
                                "requested_via": "llm_tool_planning",
                                "thread_id": payload.conversation.thread_id,
                                "platform": payload.conversation.platform or payload.source,
                                "chat_type": payload.conversation.chat_type,
                                "chat_scope": payload.conversation.chat_scope,
                                "group_chat": payload.conversation.group_chat,
                            },
                        )
                        # Handle pending approval
                        if isinstance(result, dict) and result.get("pending_approval"):
                            tool_output = f"[Chờ phê duyệt Telegram: {result.get('approval_id')}]"
                            confidence = 0.5
                        else:
                            tool_output = result.output if hasattr(result, "output") else str(result)
                            confidence = 0.9
                    except Exception as exc:
                        tool_output = f"[Lỗi tool: {exc}]"
                        confidence = 0.55

                    # Append tool result as assistant message (simulating tool result)
                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                        }],
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_output,
                    })

            # Max turns reached without direct answer
            return "Mình đã thực hiện nhiều thao tác nhưng chưa có kết quả cuối cùng. Chuyển sang review.", 0.5, agents_used

        except Exception as exc:
            logger.warning("llm_tool_planning_failed", error=str(exc))
            return "", 0.5, []

    def _build_output_from_state(
        self,
        payload: InputPayload,
        state: ReasoningState,
        start_time: float,
        extra_trace: dict | None = None,
    ) -> OutputPayload:
        """Build OutputPayload from ReasoningState after LLM tool planning."""
        return self.supervisor._create_output(
            payload=payload,
            answer=state.answer,
            confidence=state.confidence,
            intent=None,  # may be None when coming from plan_with_tools
            risk=None,
            agents_used=state.agents_used,
            status="completed",
            processing_time=start_time,
            extra_metadata=extra_trace or {},
        )

    def _build_interrupt_clarification_question(
        self,
        payload: InputPayload,
        *,
        reason: str,
        missing_fields: list[str] | None = None,
    ) -> str:
        missing_fields = missing_fields or []
        labels = {
            "query_type": "loại truy vấn hệ thống bạn cần (ví dụ: status/metrics/n8n)",
            "system": "tên hệ thống/dịch vụ liên quan",
            "error_message": "thông báo lỗi chính xác",
            "error_code": "mã lỗi",
            "step": "bạn đang kẹt ở bước nào",
            "environment": "môi trường (prod/dev/staging)",
        }

        if missing_fields:
            asked = "; ".join(labels.get(field, field.replace("_", " ")) for field in missing_fields)
            return f"Mình cần thêm thông tin trước khi tiếp tục: {asked}. Bạn bổ sung giúp mình nhé?"

        text = (payload.message.text or "").lower()
        heuristic_fields: list[str] = []
        if "lỗi" in text or "error" in text:
            heuristic_fields.extend(["error_message", "error_code"])
        heuristic_fields.extend(["system", "step", "environment"])
        asked = "; ".join(labels.get(field, field) for field in heuristic_fields[:4])

        return (
            "Mình chưa đủ độ tin cậy để trả lời chắc chắn "
            f"({reason}). Bạn cho mình thêm: {asked}."
        )

    async def run(
        self,
        payload: InputPayload,
        memory: MemoryContextModel,
        *,
        start_time: float,
        url_context: str,
    ) -> OutputPayload:
        state = ReasoningState()
        settings = get_settings()
        max_iterations = max(1, int(getattr(settings, "reasoning_loop_max_iterations", 3)))
        tool_retry = max(0, int(getattr(settings, "reasoning_loop_tool_retry", 1)))
        clarify_threshold = 0.6
        iterations_used = 0
        budget_exhausted = False
        trace_steps: list[dict[str, Any]] = []

        def add_trace(stage: str, event: str, **details: Any) -> None:
            trace_steps.append(
                {
                    "ts": round(time.time(), 3),
                    "stage": stage,
                    "event": event,
                    **details,
                }
            )

        def consume_iteration(stage: str, event: str, **details: Any) -> bool:
            nonlocal iterations_used, budget_exhausted
            if iterations_used >= max_iterations:
                budget_exhausted = True
                add_trace(
                    stage,
                    "budget_exhausted",
                    max_iterations=max_iterations,
                    iterations_used=iterations_used,
                    blocked_event=event,
                )
                return False
            iterations_used += 1
            add_trace(stage, event, iteration=iterations_used, **details)
            return True

        def build_trace_metadata(extra: dict[str, Any] | None = None) -> dict[str, Any]:
            metadata: dict[str, Any] = {
                "reasoning_loop": True,
                "reasoning_trace": {
                    "max_iterations": max_iterations,
                    "iterations_used": iterations_used,
                    "tool_retry": tool_retry,
                    "budget_exhausted": budget_exhausted,
                    "steps": trace_steps,
                },
            }
            if extra:
                metadata.update(extra)
            return metadata

        def budget_review_output(
            *,
            intent_obj: Any,
            risk_obj: Any,
            agents: list[str],
            message: str,
        ) -> OutputPayload:
            return self.supervisor._create_output(
                payload=payload,
                answer=message,
                confidence=0.55,
                intent=intent_obj,
                risk=risk_obj,
                agents_used=agents,
                status="needs_review",
                processing_time=start_time,
                extra_metadata=build_trace_metadata(
                    {
                        "kb_hit": False,
                        "kb_sources": state.kb_sources,
                        "pattern_hit": state.pattern_hit,
                        "agents_used": agents,
                    }
                ),
            )

        # Plan
        intent = await self.supervisor._classify_intent(payload, memory)
        risk = self.supervisor._evaluate_risk(payload, memory)
        use_subagents = self.supervisor.decision_engine.should_use_subagents(intent, risk, payload)
        add_trace(
            "plan",
            "classified",
            intent=getattr(intent.intent, "value", str(intent.intent)),
            intent_confidence=float(getattr(intent, "confidence", 0.0)),
            risk_level=getattr(risk.risk_level, "value", str(risk.risk_level)),
            use_subagents=use_subagents,
        )

        # Act
        if use_subagents:
            state.decision = "subagents"
            state.agents_used = self.supervisor._get_agents_from_path(intent, risk, payload, memory)

            if not consume_iteration("act", "subagent_pipeline_start", agents=state.agents_used):
                return budget_review_output(
                    intent_obj=intent,
                    risk_obj=risk,
                    agents=state.agents_used,
                    message="Đã vượt giới hạn số bước suy luận, mình chuyển sang cần review để xử lý an toàn.",
                )

            context = self.supervisor.context_agent.build(payload, memory)
            policy = await self.supervisor.policy_agent.extract(payload, memory, self.supervisor._llm)
            knowledge = await self.supervisor.knowledge_agent.retrieve(payload, memory, self.supervisor._llm)
            state.kb_sources = knowledge.get("knowledge_results", [])

            # ── LLM-driven tool planning (Hermes-style) ──────────────────────
            if self._should_use_llm_tool_planning():
                add_trace("plan", "llm_tool_planning_start")
                llm_answer, llm_conf, llm_agents = await self.plan_with_tools(
                    payload, memory,
                )
                if llm_answer:
                    state.answer = llm_answer
                    state.confidence = llm_conf
                    state.agents_used = state.agents_used + llm_agents
                    state.kb_hit = True
                    add_trace("plan", "llm_tool_planning_done", agents=llm_agents, confidence=llm_conf)
                    # Skip rest of the pipeline, go directly to observe
                    return self._build_output_from_state(
                        payload=payload, state=state,
                        start_time=start_time,
                        extra_trace=build_trace_metadata({
                            "llm_tool_planning": True,
                            "llm_agents": llm_agents,
                        }),
                    )
                else:
                    add_trace("plan", "llm_tool_planning_fallback", reason="no_answer")
            # ──────────────────────────────────────────────────────────────────

            tool_plan = self._plan_tool(knowledge, payload)
            add_trace(
                "plan",
                "tool_plan",
                tool_name=tool_plan.tool_name,
                valid=tool_plan.valid,
                reason=tool_plan.reason,
                missing_fields=tool_plan.missing_fields,
            )

            if tool_plan.tool_name and not tool_plan.valid:
                clarification_question = self._build_interrupt_clarification_question(
                    payload,
                    reason=tool_plan.reason,
                    missing_fields=tool_plan.missing_fields,
                )
                return self.supervisor._create_output(
                    payload=payload,
                    answer=clarification_question,
                    confidence=0.6,
                    intent=intent,
                    risk=risk,
                    agents_used=state.agents_used + ["tool_planner", "clarification"],
                    status="needs_clarification",
                    processing_time=start_time,
                    extra_metadata=build_trace_metadata(
                        {
                            "kb_hit": bool(state.kb_sources),
                            "kb_sources": state.kb_sources,
                            "kb_clarification_needed": True,
                            "kb_missing_fields": tool_plan.missing_fields,
                            "agents_used": state.agents_used + ["tool_planner", "clarification"],
                        }
                    ),
                )

            if knowledge.get("knowledge_clarification_needed") and state.kb_sources:
                clarification_question = knowledge.get(
                    "knowledge_clarification_question"
                ) or self.supervisor._build_kb_clarification_question(
                    state.kb_sources[0], knowledge.get("knowledge_missing_fields", [])
                )
                return self.supervisor._create_output(
                    payload=payload,
                    answer=clarification_question,
                    confidence=knowledge.get("confidence", 0.6),
                    intent=intent,
                    risk=risk,
                    agents_used=state.agents_used + ["kb_clarification"],
                    status="needs_clarification",
                    processing_time=start_time,
                    extra_metadata=build_trace_metadata(
                        {
                            "kb_hit": True,
                            "kb_clarification_needed": True,
                            "kb_missing_fields": knowledge.get("knowledge_missing_fields", []),
                            "kb_required_fields": knowledge.get("knowledge_required_fields", []),
                            "kb_sources": state.kb_sources,
                        }
                    ),
                )

            if policy.get("guide_requested") and policy.get("guide_id"):
                add_trace("act", "guide_requested", guide_id=policy.get("guide_id"))
                state.answer = await self.supervisor._handle_guide_request(payload, policy)
                state.confidence = 0.95
                state.agents_used.append("guide_delivery")
                state.kb_hit = True
            elif tool_plan.tool_name and tool_plan.tool_name in {t["name"] for t in self.tool_registry.list_tools()}:
                # Hermes-style registry tool dispatch
                add_trace("act", "registry_tool_requested", tool=tool_plan.tool_name)
                tool_result = None
                tool_error = None
                for attempt in range(1, tool_retry + 2):
                    if not consume_iteration(
                        "act",
                        "registry_tool_attempt",
                        tool=tool_plan.tool_name,
                        attempt=attempt,
                    ):
                        return budget_review_output(
                            intent_obj=intent,
                            risk_obj=risk,
                            agents=state.agents_used + [tool_plan.tool_name],
                            message="Đã vượt giới hạn số bước khi gọi tool, mình chuyển sang cần review để xử lý an toàn.",
                        )
                    try:
                        tool_result = await self.tool_registry.execute(
                            tool_plan.tool_name,
                            tool_plan.arguments,
                            approval_context={
                                "request_id": payload.request_id,
                                "user_id": payload.user.id,
                                "display_name": payload.user.display_name,
                                "original_message": payload.message.text,
                                "requested_via": "reasoning_loop",
                                "thread_id": payload.conversation.thread_id,
                                "platform": payload.conversation.platform or payload.source,
                                "chat_type": payload.conversation.chat_type,
                                "chat_scope": payload.conversation.chat_scope,
                                "group_chat": payload.conversation.group_chat,
                                "metadata": build_trace_metadata(
                                    {
                                        "kb_hit": bool(state.kb_sources),
                                        "kb_sources": state.kb_sources,
                                        "pattern_hit": state.pattern_hit,
                                        "agents_used": state.agents_used,
                                    }
                                ),
                            },
                        )
                        tool_pending_approval = isinstance(tool_result, dict) and bool(tool_result.get("pending_approval"))
                        tool_success = bool(getattr(tool_result, "success", False)) if hasattr(tool_result, "success") else bool(tool_result.get("success", False) if isinstance(tool_result, dict) else tool_result)
                        add_trace(
                            "act",
                            "registry_tool_pending_approval" if tool_pending_approval else ("registry_tool_success" if tool_success else "registry_tool_failed"),
                            tool=tool_plan.tool_name,
                            attempt=attempt,
                        )
                        break
                    except Exception as exc:
                        tool_error = str(exc)
                        tool_pending_approval = False
                        tool_success = False
                        add_trace(
                            "act",
                            "registry_tool_failed",
                            tool=tool_plan.tool_name,
                            attempt=attempt,
                            error=tool_error,
                        )

                if isinstance(tool_result, dict) and tool_result.get("pending_approval"):
                    approval_id = tool_result.get("approval_id")
                    return self.supervisor._create_output(
                        payload=payload,
                        answer=tool_result.get("message") or f"Thao tác '{tool_plan.tool_name}' đang chờ phê duyệt Telegram.",
                        confidence=0.5,
                        intent=intent,
                        risk=risk,
                        agents_used=state.agents_used + [tool_plan.tool_name],
                        status="pending_approval",
                        processing_time=start_time,
                        extra_metadata=build_trace_metadata(
                            {
                                "kb_hit": False,
                                "kb_sources": state.kb_sources,
                                "pattern_hit": state.pattern_hit,
                                "agents_used": state.agents_used + [tool_plan.tool_name],
                                "tool_pending_approval": True,
                                "approval_id": approval_id,
                                "tool_name": tool_plan.tool_name,
                                "tool_arguments": tool_plan.arguments,
                            }
                        ),
                    )

                if tool_success:
                    state.answer = tool_result.output if hasattr(tool_result, "output") else (tool_result.get("output") if isinstance(tool_result, dict) else str(tool_result))
                    state.confidence = 0.9
                    state.agents_used.append(tool_plan.tool_name)
                    state.kb_hit = True
                else:
                    return self.supervisor._create_output(
                        payload=payload,
                        answer=f"Mình chưa thực hiện được thao tác '{tool_plan.tool_name}'. Mình chuyển yêu cầu sang chế độ review.",
                        confidence=0.55,
                        intent=intent,
                        risk=risk,
                        agents_used=state.agents_used + [tool_plan.tool_name],
                        status="needs_review",
                        processing_time=start_time,
                        extra_metadata=build_trace_metadata(
                            {
                                "kb_hit": False,
                                "kb_sources": state.kb_sources,
                                "pattern_hit": state.pattern_hit,
                                "agents_used": state.agents_used + [tool_plan.tool_name],
                                "tool_failed": True,
                                "tool_error": tool_error or (tool_result.output if hasattr(tool_result, "output") else (tool_result.get("error") if isinstance(tool_result, dict) else None)),
                            }
                        ),
                    )
            elif tool_plan.tool_name == "system_query":
                query_type = tool_plan.arguments.get("query_type")
                add_trace("act", "system_query_requested", query_type=query_type)

                query_result = None
                last_error = None
                for attempt in range(1, tool_retry + 2):
                    if not consume_iteration(
                        "act",
                        "tool_attempt",
                        tool="system_query",
                        attempt=attempt,
                        query_type=query_type,
                    ):
                        return budget_review_output(
                            intent_obj=intent,
                            risk_obj=risk,
                            agents=state.agents_used + ["system_query"],
                            message="Đã vượt giới hạn số bước khi gọi tool, mình chuyển sang cần review để xử lý an toàn.",
                        )

                    try:
                        query_result = await self.supervisor._handle_system_query(
                            payload,
                            memory,
                            query_type,
                        )
                        add_trace("act", "tool_attempt_success", tool="system_query", attempt=attempt)
                        break
                    except Exception as exc:
                        last_error = str(exc)
                        add_trace(
                            "act",
                            "tool_attempt_failed",
                            tool="system_query",
                            attempt=attempt,
                            error=last_error,
                        )

                if query_result is None:
                    return self.supervisor._create_output(
                        payload=payload,
                        answer="Mình chưa truy vấn được hệ thống sau nhiều lần thử. Mình chuyển yêu cầu sang chế độ review để xử lý chính xác.",
                        confidence=0.55,
                        intent=intent,
                        risk=risk,
                        agents_used=state.agents_used + ["system_query"],
                        status="needs_review",
                        processing_time=start_time,
                        extra_metadata=build_trace_metadata(
                            {
                                "kb_hit": False,
                                "kb_sources": state.kb_sources,
                                "pattern_hit": state.pattern_hit,
                                "agents_used": state.agents_used + ["system_query"],
                                "tool_failed": True,
                                "tool_error": last_error,
                            }
                        ),
                    )

                state.answer = self.supervisor._format_system_query_response(query_result)
                state.confidence = query_result.get("confidence", 0.9)
                state.agents_used.append("system_query")
                state.kb_hit = True
            elif self._should_delegate_to_subagent_pool(payload):
                # Multi-source / complex task → parallel subagent delegation
                add_trace("act", "subagent_delegation_start")
                subagent_tasks = self._build_subagent_tasks(payload, knowledge)
                if subagent_tasks:
                    pool = SubagentPool(max_concurrency=min(5, len(subagent_tasks)))

                    async def _subagent_handler(task: SubagentTask) -> SubagentResult:
                        try:
                            # Simple handler: use knowledge retrieval + draft for each task
                            sub_knowledge = await self.supervisor.knowledge_agent.retrieve(
                                payload, memory, self.supervisor._llm
                            )
                            ctx = self.supervisor.context_agent.build(payload, memory)
                            pol = await self.supervisor.policy_agent.extract(payload, memory, self.supervisor._llm)
                            draft = await self.supervisor.draft_agent.generate(
                                payload, ctx, pol, sub_knowledge, self.supervisor._llm
                            )
                            return SubagentResult(
                                task_id=task.task_id,
                                success=True,
                                output=draft or "(no output)",
                                confidence=0.75,
                            )
                        except Exception as exc:
                            return SubagentResult(
                                task_id=task.task_id,
                                success=False,
                                output="",
                                error=str(exc),
                            )

                    results = await pool.run(subagent_tasks, handler=_subagent_handler)
                    aggregator = SubagentAggregator()
                    state.answer = aggregator.aggregate(results)
                    state.confidence = aggregator.confidence(results)
                    state.agents_used.append("subagent_pool")
                    state.agents_used.extend([r.task_id for r in results if r.success])
                    state.kb_hit = any(r.success for r in results)
                    add_trace(
                        "act",
                        "subagent_delegation_done",
                        tasks=len(subagent_tasks),
                        successful=sum(1 for r in results if r.success),
                        confidence=state.confidence,
                    )
                else:
                    add_trace("act", "subagent_delegation_no_tasks")
                    pattern_result = await self.supervisor._check_patterns(payload)
                    if pattern_result:
                        add_trace("act", "pattern_matched")
                        state.answer, similarity = pattern_result
                        state.confidence = min(1.0, similarity + 0.05)
                        state.kb_hit = True
                        state.pattern_hit = True
                        state.agents_used.append("pattern_match")
                    else:
                        if not consume_iteration("act", "draft_and_validate"):
                            return budget_review_output(
                                intent_obj=intent,
                                risk_obj=risk,
                                agents=state.agents_used,
                                message="Đã vượt giới hạn số bước trước khi tạo câu trả lờ",
                            )
                        context_with_urls = dict(context)
                        if url_context:
                            context_with_urls["url_context"] = url_context
                        draft = await self.supervisor.draft_agent.generate(
                            payload,
                            context_with_urls,
                            policy,
                            knowledge,
                            self.supervisor._llm,
                        )
                        validation = await self.supervisor._enhanced_validate(
                            draft,
                            payload,
                            context,
                            policy,
                            knowledge,
                        )
                        state.answer = self.supervisor.qa_agent.refine(validation, payload, context)
                        state.confidence = validation["confidence"]
                        state.kb_hit = bool(knowledge.get("knowledge_results"))
                        state.qa_needs_review = bool(validation.get("needs_review"))
            else:
                pattern_result = await self.supervisor._check_patterns(payload)
                if pattern_result:
                    add_trace("act", "pattern_matched")
                    state.answer, similarity = pattern_result
                    state.confidence = min(1.0, similarity + 0.05)
                    state.kb_hit = True
                    state.pattern_hit = True
                    state.agents_used.append("pattern_match")
                else:
                    if not consume_iteration("act", "draft_and_validate"):
                        return budget_review_output(
                            intent_obj=intent,
                            risk_obj=risk,
                            agents=state.agents_used,
                            message="Đã vượt giới hạn số bước trước khi tạo câu trả lời, mình chuyển sang cần review.",
                        )

                    context_with_urls = dict(context)
                    if url_context:
                        context_with_urls["url_context"] = url_context

                    draft = await self.supervisor.draft_agent.generate(
                        payload,
                        context_with_urls,
                        policy,
                        knowledge,
                        self.supervisor._llm,
                    )
                    validation = await self.supervisor._enhanced_validate(
                        draft,
                        payload,
                        context,
                        policy,
                        knowledge,
                    )
                    state.answer = self.supervisor.qa_agent.refine(validation, payload, context)
                    state.confidence = validation["confidence"]
                    state.kb_hit = bool(knowledge.get("knowledge_results"))
                    state.qa_needs_review = bool(validation.get("needs_review"))
        else:
            if not consume_iteration("act", "direct_path"):
                return budget_review_output(
                    intent_obj=intent,
                    risk_obj=risk,
                    agents=["draft"],
                    message="Đã vượt giới hạn số bước suy luận cho câu hỏi này, mình chuyển sang cần review.",
                )

            pattern_result = await self.supervisor._check_patterns(payload)
            if pattern_result:
                add_trace("act", "pattern_matched")
                state.answer, similarity = pattern_result
                state.confidence = min(1.0, similarity + 0.05)
                state.kb_hit = True
                state.agents_used = ["pattern_match"]
            else:
                state.agents_used = ["draft"]
                state.answer, state.confidence = await self.supervisor._generate_direct_answer(payload, memory)

        # Calculate confidence dynamically based on evidence quality
        if state.kb_hit and hasattr(state, 'kb_sources') and state.kb_sources:
            # Use dynamic confidence based on KB evidence
            final_confidence = self.supervisor._calculate_dynamic_confidence(
                kb_sources=state.kb_sources,
                question_length=len(payload.message.text or ""),
                answer_length=len(state.answer or ""),
                llm_confidence=state.confidence if state.confidence > 0.5 else None,
            )
        else:
            # Non-KB: cap at 0.89 to prevent auto-send without KB evidence
            final_confidence = min(0.89, state.confidence)

        final_confidence = round(final_confidence, 2)

        if final_confidence < clarify_threshold and not state.kb_hit:
            clarification_question = self._build_interrupt_clarification_question(
                payload,
                reason=f"low_confidence:{final_confidence}",
            )
            add_trace(
                "observe",
                "interrupt_clarification",
                final_confidence=final_confidence,
                threshold=clarify_threshold,
            )
            return self.supervisor._create_output(
                payload=payload,
                answer=clarification_question,
                confidence=max(0.5, final_confidence),
                intent=intent,
                risk=risk,
                agents_used=state.agents_used + ["clarification"],
                status="needs_clarification",
                processing_time=start_time,
                extra_metadata=build_trace_metadata(
                    {
                        "kb_hit": state.kb_hit,
                        "kb_sources": state.kb_sources,
                        "pattern_hit": state.pattern_hit,
                        "agents_used": state.agents_used + ["clarification"],
                        "interrupt_reason": "low_confidence",
                    }
                ),
            )

        response_route = self.supervisor.decision_engine.response_route(
            final_confidence,
            kb_hit=state.kb_hit,
        )
        if response_route == "skip":
            state.decision = "skipped"
            state.answer = ""
            status = "skipped"
        elif response_route == "approve":
            state.decision = "review"
            status = "needs_review"
        else:
            status = "completed"
        add_trace(
            "observe",
            "response_routed",
            response_route=response_route,
            final_confidence=final_confidence,
            status=status,
        )

        processing_time_ms = int((time.time() - start_time) * 1000)
        await self.supervisor._log_audit(
            request_id=payload.request_id,
            decision=state.decision,
            risk_level=risk.risk_level.value,
            agents_used=state.agents_used,
            input_summary=payload.message.text[:200],
            output_summary=state.answer[:200],
            processing_time_ms=processing_time_ms,
        )

        if status == "completed" and final_confidence >= 0.6:
            self.supervisor._cache_response(payload, state.answer, final_confidence)

        extra_metadata = build_trace_metadata(
            {
                "kb_hit": state.kb_hit,
                "kb_sources": state.kb_sources,
                "pattern_hit": state.pattern_hit,
                "agents_used": state.agents_used,
            }
        )

        if state.kb_sources:
            extra_metadata["kb_evidence"] = [
                {
                    "id": item.get("id"),
                    "type": item.get("type"),
                    "title": item.get("title"),
                    "category": item.get("category"),
                    "similarity": item.get("similarity"),
                    "content": item.get("content", "")[:200],
                }
                for item in state.kb_sources[:3]
            ]

        return self.supervisor._create_output(
            payload=payload,
            answer=state.answer,
            confidence=final_confidence,
            risk=risk,
            intent=intent,
            agents_used=state.agents_used,
            status=status,
            processing_time=start_time,
            extra_metadata=extra_metadata,
        )
