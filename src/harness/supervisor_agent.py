"""
SupervisorAgent - Wraps Supervisor for use with Agent Harness

This allows the Supervisor to be used as an agent within the harness framework,
providing:
- Standardized agent interface (.chat() method)
- Integration with lifecycle hooks
- Context management
- Evaluation and benchmarking
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass
import asyncio

logger = None  # Will be set by harness


@dataclass
class SupervisorAgentConfig:
    """Configuration for SupervisorAgent"""
    enable_hooks: bool = True
    enable_context_injection: bool = True
    enable_evaluation: bool = True
    async_mode: bool = True


class SupervisorAgent:
    """
    Wraps the Supervisor to work with the Agent Harness framework.
    
    This provides a standardized agent interface while delegating
    to the Supervisor's process() method.
    """
    
    def __init__(
        self,
        supervisor,  # The Supervisor instance
        config: Optional[SupervisorAgentConfig] = None,
    ):
        self.supervisor = supervisor
        self.config = config or SupervisorAgentConfig()
        self._hooks_enabled = self.config.enable_hooks
        
        # Import logger lazily to avoid circular imports
        global logger
        if logger is None:
            import structlog
            logger = structlog.get_logger("harness.supervisor_agent")
    
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Any:
        """
        Standard agent interface - called by harness for each turn.
        
        In this case, we don't use message-based chat.
        Instead, we use the Supervisor's process() method directly.
        
        Returns a response object with content and optional tool_calls.
        """
        
        # Extract the prompt from messages
        prompt = self._extract_prompt(messages)
        
        # Create InputPayload from prompt
        payload = self._create_payload(prompt, context, **kwargs)
        
        # Get memory context if available
        memory = context.get("memory") if context else None
        
        # Ensure memory is not None - create empty memory context if needed
        if memory is None:
            from src.memory.service import MemoryContext
            memory = MemoryContext(
                conversation_summary="",
                recent_messages=[],
                user_profile=None,
                case_memory=None,
            )
        
        # Call Supervisor.process()
        result = await self.supervisor.process(payload, memory)
        
        # Convert to standard agent response format
        return SupervisorResponse(
            content=result.answer,
            tool_calls=[],  # Supervisor handles tools internally
            metadata={
                "confidence": result.confidence,
                "status": result.status,
                "intent": result.metadata.get("intent"),
                "agents_used": result.metadata.get("agents_used", []),
                "risk_level": result.metadata.get("risk_level"),
            }
        )
    
    def _extract_prompt(self, messages: List[Dict[str, Any]]) -> str:
        """Extract the user prompt from messages"""
        # Find the last user message
        for msg in reversed(messages):
            if msg.get("role") == "user":
                return msg.get("content", "")
        return ""
    
    def _create_payload(
        self,
        prompt: str,
        context: Optional[Dict[str, Any]],
        **kwargs
    ):
        """Create InputPayload from prompt and context"""
        from src.core import UserInfo, ConversationInfo, MessageInfo, InputPayload, CaseInfo
        
        # Extract context info
        user_id = context.get("user_id", "unknown") if context else "unknown"
        display_name = context.get("display_name", "User") if context else "User"
        thread_id = context.get("thread_id", f"harness-{id(self)}") if context else f"harness-{id(self)}"
        request_id = context.get("request_id", "") if context else ""
        
        # Create payload
        return InputPayload(
            request_id=request_id,
            source="harness",
            timestamp=kwargs.get("timestamp", ""),
            user=UserInfo(
                id=user_id,
                display_name=display_name,
                role=context.get("role") if context else None,
                team=context.get("team") if context else None,
                vip_flag=context.get("vip_flag", False) if context else False,
            ),
            conversation=ConversationInfo(
                thread_id=thread_id,
                message_id=f"msg-{request_id}" if request_id else f"msg-{thread_id}",
            ),
            case=CaseInfo(case_id=context.get("case_id")) if context and context.get("case_id") else None,
            message=MessageInfo(text=prompt),
        )
    
    def process_direct(
        self,
        payload,  # InputPayload
        memory: Optional[Any] = None,
    ):
        """
        Direct process call - bypasses agent interface.
        
        This is used when the harness needs to call Supervisor
        directly with a pre-built payload.
        """
        return asyncio.run(self.supervisor.process(payload, memory))


@dataclass
class SupervisorResponse:
    """Standard response format for agent interface"""
    content: str
    tool_calls: List[Any]
    metadata: Dict[str, Any]
    
    @property
    def text(self) -> str:
        return self.content


class HarnessSupervisorBridge:
    """
    Bridge between Harness and Supervisor for integrated execution.
    
    This provides a seamless integration where:
    1. Harness handles lifecycle, hooks, context, planning
    2. Supervisor handles the actual agent logic
    3. Evaluation tracks performance
    """
    
    def __init__(self, supervisor, config: Optional[SupervisorAgentConfig] = None):
        self.supervisor = supervisor
        self.config = config or SupervisorAgentConfig()
        self.agent = SupervisorAgent(supervisor, config)
        
        # Import here to avoid circular imports
        from .harness import get_harness, HarnessConfig
        
        # Create harness with supervisor agent
        self.harness = get_harness(
            config=HarnessConfig(
                name="supervisor-harness",
                enable_planning=True,
                enable_evaluation=True,
                enable_context_compaction=True,
            ),
            agent=self.agent,
        )
    
    async def process(
        self,
        payload,  # InputPayload
        memory: Optional[Any] = None,
    ) -> Any:
        """
        Process a request through Harness → Supervisor.
        
        Flow:
        1. Pre-execution hooks
        2. Inject context
        3. Call Supervisor.process()
        4. Evaluate result
        5. Post-execution hooks
        """
        # Build context for harness
        context = {
            "user_id": payload.user.id,
            "display_name": payload.user.display_name,
            "role": payload.user.role,
            "team": payload.user.team,
            "thread_id": payload.conversation.thread_id,
            "request_id": payload.request_id,
            "memory": memory,
            "source": payload.source,
        }
        
        # Execute through harness
        result = await self.harness.execute(
            prompt=payload.message.text,
            context=context,
        )
        
        # The harness result contains:
        # - result: the agent's content response
        # - evaluation: evaluation object (if enabled)
        # - execution_id: harness execution ID
        # - iterations, tool_calls: metrics
        harness_result = result.get("result", "")
        harness_evaluation = result.get("evaluation")
        harness_execution_id = result.get("execution_id")
        harness_metrics = self.harness.get_metrics()
        
        # Build response - extract metadata from harness
        from src.core import OutputPayload
        
        # If result is an OutputPayload from Supervisor, extract it directly
        if hasattr(harness_result, 'answer'):
            supervisor_output = harness_result
            # Get intent from metadata
            intent_meta = supervisor_output.metadata.get("intent", {}) if supervisor_output.metadata else {}
            if isinstance(intent_meta, dict):
                intent_str = intent_meta.get("type", "faq")
            else:
                intent_str = str(intent_meta)
        else:
            # Otherwise create a new OutputPayload
            supervisor_output = None
            intent_str = "faq"
        
        # Return OutputPayload with harness metadata
        return OutputPayload(
            answer=supervisor_output.answer if supervisor_output else str(harness_result),
            confidence=supervisor_output.confidence if supervisor_output else 0.8,
            risk_level="low",
            metadata={
                "intent": intent_str,
                "agents_used": [],
                "harness_execution_id": harness_execution_id,
                "harness_metrics": harness_metrics,
                "harness_evaluation": harness_evaluation.__dict__ if harness_evaluation else None,
            },
        )
