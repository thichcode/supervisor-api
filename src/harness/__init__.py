"""
Agent Harness Module for supervisor-api

Provides the "Operating System" layer for AI agents:
- Tool registry and handling
- Lifecycle hooks
- Context management
- Planning and strategy
- Evaluation and benchmarking
"""

from .harness import AgentHarness, get_harness, HarnessConfig, HarnessStatus, ExecutionMetrics
from .tool_registry import ToolRegistry, get_tool_registry, ToolCategory, ToolDefinition, ToolExecutionResult
from .lifecycle import LifecycleHooks, HookType, HookEvent, HookRegistration
from .context_manager import ContextManager, ContextConfig, CompactionRecord
from .planner import Planner, Plan, PlanStep, StrategyType, ExecutionTrace
from .evaluator import Evaluator, MetricType, MetricResult, EvaluationResult, BenchmarkRun
from .supervisor_agent import SupervisorAgent, SupervisorAgentConfig, HarnessSupervisorBridge

__all__ = [
    # Harness
    "AgentHarness",
    "get_harness",
    "HarnessConfig",
    "HarnessStatus",
    "ExecutionMetrics",
    # Tool Registry
    "ToolRegistry",
    "get_tool_registry",
    "ToolCategory",
    "ToolDefinition",
    "ToolExecutionResult",
    # Lifecycle
    "LifecycleHooks",
    "HookType",
    "HookEvent",
    "HookRegistration",
    # Context
    "ContextManager",
    "ContextConfig",
    "CompactionRecord",
    # Planner
    "Planner",
    "Plan",
    "PlanStep",
    "StrategyType",
    "ExecutionTrace",
    # Evaluator
    "Evaluator",
    "MetricType",
    "MetricResult",
    "EvaluationResult",
    "BenchmarkRun",
    # Supervisor Bridge
    "SupervisorAgent",
    "SupervisorAgentConfig",
    "HarnessSupervisorBridge",
]
