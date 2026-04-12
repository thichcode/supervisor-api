"""
Planner - Strategy planning for complex tasks

Provides:
- Task decomposition
- Step planning
- Strategy selection
- Re-planning on failure
"""

import asyncio
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import logging
from src.config import get_settings

settings = get_settings()
logger = logging.getLogger("harness.planner")


class StrategyType(Enum):
    """Types of planning strategies"""
    LINEAR = "linear"  # Sequential steps
    BRANCHING = "branching"  # Multiple paths
    PARALLEL = "parallel"  # Concurrent tasks
    HIERARCHICAL = "hierarchical"  # Nested subtasks
    REFLEXIVE = "reflexive"  # Self-reflection


@dataclass
class PlanStep:
    """A single step in a plan"""
    id: int
    description: str
    action: str
    depends_on: List[int] = field(default_factory=list)
    estimated_tokens: int = 100
    required_tools: List[str] = field(default_factory=list)
    on_failure: str = "abort"  # abort, retry, skip, replan
    
    def can_execute(self, completed: List[int]) -> bool:
        """Check if dependencies are met"""
        return all(dep in completed for dep in self.depends_on)


@dataclass
class Plan:
    """A complete plan for a task"""
    task: str
    strategy: StrategyType
    steps: List[PlanStep]
    estimated_duration: int = 60  # seconds
    total_tokens: int = 0
    context_needed: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    
    def get_executable_steps(self, completed: List[int]) -> List[PlanStep]:
        """Get steps that can be executed now"""
        return [s for s in self.steps if s.can_execute(completed)]


@dataclass
class ExecutionTrace:
    """Trace of plan execution"""
    plan_id: str
    step_id: int
    start_time: datetime
    end_time: Optional[datetime] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    retry_count: int = 0


class Planner:
    """
    Strategy planner for complex tasks
    
    Features:
    - Automatic task decomposition
    - Strategy selection based on task type
    - Step planning with dependencies
    - Re-planning on failure
    """
    
    def __init__(self):
        self._plan_cache: Dict[str, Plan] = {}
        self._execution_traces: List[ExecutionTrace] = []
        self._replan_threshold = 2  # Replan after N failures
    
    async def create_plan(
        self,
        task: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a plan for a task
        
        Flow:
        1. Analyze task
        2. Select strategy
        3. Decompose into steps
        4. Estimate resources
        """
        # Check cache
        cache_key = self._get_cache_key(task)
        if cache_key in self._plan_cache:
            logger.debug(f"Using cached plan for: {task[:50]}...")
            return self._plan_to_dict(self._plan_cache[cache_key])
        
        # Analyze task complexity
        complexity = self._analyze_complexity(task, context)
        
        # Select strategy
        strategy = self._select_strategy(complexity, context)
        
        # Decompose task
        steps = await self._decompose_task(task, strategy, context)
        
        # Create plan
        plan = Plan(
            task=task,
            strategy=strategy,
            steps=steps,
            estimated_duration=sum(s.estimated_tokens for s in steps) // 10,
            total_tokens=sum(s.estimated_tokens for s in steps),
        )
        
        # Cache plan
        self._plan_cache[cache_key] = plan
        
        logger.info(
            f"Created plan: {strategy.value}, {len(steps)} steps, "
            f"~{plan.total_tokens} tokens"
        )
        
        return self._plan_to_dict(plan)
    
    def _analyze_complexity(
        self,
        task: str,
        context: Optional[Dict[str, Any]],
    ) -> str:
        """Analyze task complexity"""
        # Simple heuristic-based analysis
        task_lower = task.lower()
        
        # Indicators of complexity
        multi_step_indicators = [
            "first", "then", "after", "before", "and also",
            "next", "finally", "step", "phases",
        ]
        
        parallel_indicators = [
            "all", "both", "simultaneously", "in parallel",
            "at the same time", "concurrently",
        ]
        
        decision_indicators = [
            "if", "when", "depending", "maybe", "either",
            "check", "determine", "decide",
        ]
        
        # Count indicators
        multi_count = sum(1 for ind in multi_step_indicators if ind in task_lower)
        parallel_count = sum(1 for ind in parallel_indicators if ind in task_lower)
        decision_count = sum(1 for ind in decision_indicators if ind in task_lower)
        
        # Classify complexity
        if parallel_count > 0:
            return "high"  # Needs parallel execution
        elif multi_count > 1 or decision_count > 0:
            return "medium"  # Needs branching
        else:
            return "low"  # Simple linear task
    
    def _select_strategy(
        self,
        complexity: str,
        context: Optional[Dict[str, Any]],
    ) -> StrategyType:
        """Select appropriate strategy"""
        if complexity == "high":
            return StrategyType.PARALLEL
        elif complexity == "medium":
            # Check if we have tools for parallel execution
            if context and "parallel_tools" in context:
                return StrategyType.PARALLEL
            return StrategyType.BRANCHING
        else:
            return StrategyType.LINEAR
    
    async def _decompose_task(
        self,
        task: str,
        strategy: StrategyType,
        context: Optional[Dict[str, Any]],
    ) -> List[PlanStep]:
        """Decompose task into steps"""
        # Use LLM or heuristics to decompose
        # For now, use simple heuristics
        
        steps = []
        
        if strategy == StrategyType.LINEAR:
            # Simple single-step task
            steps.append(PlanStep(
                id=0,
                description=task,
                action="execute",
                estimated_tokens=500,
            ))
        
        elif strategy == StrategyType.BRANCHING:
            # Task with conditional logic
            steps.append(PlanStep(
                id=0,
                description="Analyze requirements",
                action="analyze",
                estimated_tokens=200,
            ))
            steps.append(PlanStep(
                id=1,
                description="Execute main path",
                action="execute",
                depends_on=[0],
                estimated_tokens=300,
            ))
            steps.append(PlanStep(
                id=2,
                description="Handle edge cases",
                action="handle_edge_cases",
                depends_on=[0],
                estimated_tokens=200,
            ))
        
        elif strategy == StrategyType.PARALLEL:
            # Task that can be parallelized
            steps.append(PlanStep(
                id=0,
                description="Gather information (parallel)",
                action="gather",
                estimated_tokens=400,
            ))
            steps.append(PlanStep(
                id=1,
                description="Process results",
                action="process",
                depends_on=[0],
                estimated_tokens=200,
            ))
        
        else:
            # Fallback to linear
            steps.append(PlanStep(
                id=0,
                description=task,
                action="execute",
                estimated_tokens=500,
            ))
        
        return steps
    
    def _get_cache_key(self, task: str) -> str:
        """Get cache key for task"""
        # Simple hash-based key
        return hash(task) % 1000000
    
    def _plan_to_dict(self, plan: Plan) -> Dict[str, Any]:
        """Convert plan to dictionary"""
        return {
            "task": plan.task,
            "strategy": plan.strategy.value,
            "steps": [
                {
                    "id": s.id,
                    "description": s.description,
                    "action": s.action,
                    "depends_on": s.depends_on,
                    "required_tools": s.required_tools,
                }
                for s in plan.steps
            ],
            "estimated_duration": plan.estimated_duration,
            "total_tokens": plan.total_tokens,
            "created_at": plan.created_at.isoformat(),
        }
    
    async def replan(
        self,
        failed_step_id: int,
        error: str,
        current_plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Re-plan after a step failure
        
        Options:
        - Retry the step
        - Skip the step
        - Add error handling
        - Create entirely new plan
        """
        logger.warning(f"Re-planning after step {failed_step_id} failed: {error}")
        
        # Simple re-planning: add error handling step
        new_plan = current_plan.copy()
        new_steps = new_plan.get("steps", [])
        
        # Add error handling step
        new_steps.append({
            "id": len(new_steps),
            "description": f"Handle error: {error[:100]}",
            "action": "handle_error",
            "depends_on": [failed_step_id],
            "on_failure": "replan",
        })
        
        new_plan["steps"] = new_steps
        new_plan["replanned"] = True
        
        return new_plan
    
    def get_stats(self) -> Dict[str, Any]:
        """Get planner statistics"""
        return {
            "cached_plans": len(self._plan_cache),
            "execution_traces": len(self._execution_traces),
            "strategies_used": {},
        }
    
    def clear_cache(self) -> None:
        """Clear plan cache"""
        self._plan_cache.clear()
        logger.info("Plan cache cleared")
