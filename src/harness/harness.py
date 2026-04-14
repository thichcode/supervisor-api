"""
Agent Harness - The "Operating System" for AI Agents

Wraps around the agent to provide:
- Tool registry and standardized handling
- Lifecycle hooks (boot, callbacks)
- Context management
- Planning and strategy
- Evaluation and benchmarking
"""

import asyncio
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, TypeVar
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import json

import logging
from src.config import get_settings
from .lifecycle import HookType

settings = get_settings()
logger = logging.getLogger("harness")


class HarnessStatus(Enum):
    """Harness operational status"""
    IDLE = "idle"
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


@dataclass
class HarnessConfig:
    """Configuration for Agent Harness"""
    name: str = "supervisor-harness"
    max_iterations: int = 100
    max_tool_calls: int = 50
    timeout_seconds: int = 300
    enable_planning: bool = True
    enable_evaluation: bool = True
    enable_context_compaction: bool = True
    checkpoint_interval: int = 10  # Save state every N iterations


@dataclass
class ExecutionMetrics:
    """Metrics for a single execution"""
    execution_id: str
    start_time: float
    end_time: Optional[float] = None
    iterations: int = 0
    tool_calls: int = 0
    tokens_used: int = 0
    errors: List[str] = field(default_factory=list)
    checkpoints: List[Dict] = field(default_factory=list)
    
    @property
    def duration(self) -> float:
        if self.end_time:
            return self.end_time - self.start_time
        return time.time() - self.start_time
    
    @property
    def success(self) -> bool:
        return len(self.errors) == 0 and self.iterations > 0


T = TypeVar('T')


class AgentHarness:
    """
    Agent Harness - The "Operating System" for AI Agents
    
    Wraps around an agent to provide standardized infrastructure:
    - Tool registry with standardized handling
    - Lifecycle hooks for boot, callbacks, shutdown
    - Context management with compaction
    - Planning for complex tasks
    - Evaluation for benchmarking
    """
    
    def __init__(
        self,
        config: Optional[HarnessConfig] = None,
        agent=None,
    ):
        self.config = config or HarnessConfig()
        self.agent = agent
        self.status = HarnessStatus.IDLE
        self.execution_id: Optional[str] = None
        self.metrics: Optional[ExecutionMetrics] = None
        
        # Initialize sub-components
        from .tool_registry import get_tool_registry
        from .lifecycle import LifecycleHooks
        from .context_manager import ContextManager
        from .planner import Planner
        from .evaluator import Evaluator
        
        self.tool_registry = get_tool_registry()
        self.lifecycle = LifecycleHooks()
        self.context_manager = ContextManager(
            enable_compaction=self.config.enable_context_compaction
        )
        self.planner = Planner() if self.config.enable_planning else None
        self.evaluator = Evaluator() if self.config.enable_evaluation else None
        
        # State storage
        self._state: Dict[str, Any] = {}
        self._hooks: Dict[str, List[Callable]] = {}
        
        logger.info(f"AgentHarness initialized: {self.config.name}")
    
    async def boot(self, **kwargs) -> Dict[str, Any]:
        """
        Boot sequence - called before agent starts
        
        Executes:
        1. Pre-boot hooks
        2. Initialize tools
        3. Load state from storage
        4. Post-boot hooks
        """
        self.status = HarnessStatus.INITIALIZING
        self.execution_id = str(uuid.uuid4())
        self.metrics = ExecutionMetrics(execution_id=self.execution_id, start_time=time.time())
        
        logger.info(f"Booting harness: {self.execution_id}")
        
        # Run pre-boot hooks
        await self.lifecycle.run_hooks(HookType.PRE_BOOT, {"execution_id": self.execution_id, **kwargs})
        
        # Initialize tools
        await self.tool_registry.initialize()
        
        # Load state
        self._state = await self._load_state()
        
        # Run post-boot hooks
        await self.lifecycle.run_hooks(HookType.POST_BOOT, {"execution_id": self.execution_id, **kwargs})
        
        self.status = HarnessStatus.IDLE
        logger.info(f"Harness booted successfully: {self.execution_id}")
        
        return {"status": "booted", "execution_id": self.execution_id}
    
    async def execute(
        self,
        prompt: str,
        tools: Optional[List[Dict]] = None,
        context: Optional[Dict] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute a task through the agent with harness management
        
        Flow:
        1. Boot if not already booted
        2. Plan if planning enabled
        3. Execute with tool handling
        4. Compact context if needed
        5. Evaluate and checkpoint
        """
        # Boot if needed
        if self.status == HarnessStatus.IDLE:
            await self.boot()
        
        self.status = HarnessStatus.RUNNING
        
        # Run pre-execution hooks
        await self.lifecycle.run_hooks(HookType.PRE_EXECUTION, {
            "prompt": prompt,
            "execution_id": self.execution_id,
            **kwargs
        })
        
        # Planning phase
        plan = None
        if self.planner and self.config.enable_planning:
            plan = await self.planner.create_plan(prompt, context)
            logger.info(f"Created plan: {plan.get('strategy', 'default')}")
        
        # Execution phase with tool handling
        try:
            result = await self._execute_with_tools(
                prompt=prompt,
                tools=tools,
                context=context,
                plan=plan,
            )
            
            # Run post-execution hooks
            await self.lifecycle.run_hooks(HookType.POST_EXECUTION, {
                "result": result,
                "execution_id": self.execution_id,
                **kwargs
            })
            
            # Update metrics
            if self.metrics:
                self.metrics.end_time = time.time()
            
            # Evaluate if enabled
            if self.evaluator:
                evaluation = await self.evaluator.evaluate(
                    execution_id=self.execution_id,
                    prompt=prompt,
                    result=result,
                    metrics=self.metrics,
                )
                result["evaluation"] = evaluation
            
            self.status = HarnessStatus.IDLE
            return result
            
        except Exception as e:
            self.status = HarnessStatus.ERROR
            if self.metrics:
                self.metrics.errors.append(str(e))
            logger.error(f"Execution error: {e}")
            raise
    
    async def _execute_with_tools(
        self,
        prompt: str,
        tools: Optional[List[Dict]],
        context: Optional[Dict],
        plan: Optional[Dict],
    ) -> Dict[str, Any]:
        """Execute with standardized tool handling"""
        
        iteration = 0
        tool_call_count = 0
        messages = [{"role": "user", "content": prompt}]
        
        # Inject context
        if context:
            context_msg = self.context_manager.inject_context(context)
            messages.insert(0, context_msg)
        
        while iteration < self.config.max_iterations:
            iteration += 1
            if self.metrics:
                self.metrics.iterations = iteration
            
            # Checkpointing
            if iteration % self.config.checkpoint_interval == 0:
                await self._checkpoint()
            
            # Execute agent turn
            response = await self._agent_turn(messages, tools)
            
            # Check for tool calls
            if hasattr(response, 'tool_calls') and response.tool_calls:
                for tool_call in response.tool_calls:
                    tool_call_count += 1
                    if self.metrics:
                        self.metrics.tool_calls = tool_call_count
                    
                    # Standardized tool handling
                    tool_result = await self.tool_registry.execute(
                        name=tool_call.name,
                        arguments=tool_call.arguments,
                    )
                    
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(tool_result),
                    })
            else:
                # No more tool calls, return result
                return {
                    "execution_id": self.execution_id,
                    "result": response.content if hasattr(response, 'content') else str(response),
                    "iterations": iteration,
                    "tool_calls": tool_call_count,
                    "plan": plan,
                }
            
            # Context compaction check
            if self.config.enable_context_compaction:
                messages = self.context_manager.compact(messages)
        
        # Max iterations reached
        return {
            "execution_id": self.execution_id,
            "result": "Max iterations reached",
            "iterations": iteration,
            "tool_calls": tool_call_count,
            "status": "max_iterations",
        }
    
    async def _agent_turn(self, messages: List[Dict], tools: Optional[List[Dict]]) -> Any:
        """Single agent turn - delegates to actual agent"""
        if self.agent:
            # Use the actual agent
            return await self.agent.chat(messages)
        else:
            # Mock response for testing
            return type('Response', (), {'content': 'Mock response', 'tool_calls': []})()
    
    async def _checkpoint(self):
        """Save state checkpoint"""
        checkpoint = {
            "timestamp": datetime.now().isoformat(),
            "execution_id": self.execution_id,
            "metrics": {
                "iterations": self.metrics.iterations if self.metrics else 0,
                "tool_calls": self.metrics.tool_calls if self.metrics else 0,
            },
            "state": self._state.copy(),
        }
        
        if self.metrics:
            self.metrics.checkpoints.append(checkpoint)
        
        await self._save_state()
        logger.debug(f"Checkpoint saved: {checkpoint['timestamp']}")
    
    async def _load_state(self) -> Dict[str, Any]:
        """Load state from storage"""
        # TODO: Implement with Redis or database
        return {}
    
    async def _save_state(self):
        """Save state to storage"""
        # TODO: Implement with Redis or database
        pass
    
    def register_hook(self, hook_type: str, callback: Callable) -> None:
        """Register a lifecycle hook"""
        if hook_type not in self._hooks:
            self._hooks[hook_type] = []
        self._hooks[hook_type].append(callback)
    
    async def shutdown(self) -> Dict[str, Any]:
        """Shutdown sequence"""
        logger.info(f"Shutting down harness: {self.execution_id}")
        
        # Run shutdown hooks
        await self.lifecycle.run_hooks(HookType.SHUTDOWN, {
            "execution_id": self.execution_id,
            "metrics": self.metrics.__dict__ if self.metrics else None,
        })
        
        # Final checkpoint
        await self._checkpoint()
        
        self.status = HarnessStatus.IDLE
        return {
            "status": "shutdown",
            "execution_id": self.execution_id,
            "metrics": self.metrics.__dict__ if self.metrics else None,
        }
    
    def get_metrics(self) -> Optional[Dict[str, Any]]:
        """Get execution metrics"""
        if self.metrics:
            return {
                "execution_id": self.metrics.execution_id,
                "duration": self.metrics.duration,
                "iterations": self.metrics.iterations,
                "tool_calls": self.metrics.tool_calls,
                "tokens_used": self.metrics.tokens_used,
                "errors": len(self.metrics.errors),
                "checkpoints": len(self.metrics.checkpoints),
                "success": self.metrics.success,
            }
        return None


# Global harness instance
_harness: Optional[AgentHarness] = None


def get_harness(config: Optional[HarnessConfig] = None, agent=None) -> AgentHarness:
    """Get or create the global harness instance"""
    global _harness
    if _harness is None:
        _harness = AgentHarness(config=config, agent=agent)
    return _harness
