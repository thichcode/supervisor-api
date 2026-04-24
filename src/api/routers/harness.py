from fastapi import APIRouter, HTTPException

from src.harness import HookType, get_harness, get_tool_registry

router = APIRouter(prefix="/harness", tags=["harness"])


@router.get("/status")
async def get_harness_status():
    """Get current harness status and statistics"""
    harness = get_harness()
    tool_registry = get_tool_registry()

    return {
        "harness": {
            "name": harness.config.name,
            "status": harness.status.value,
            "execution_id": harness.execution_id,
            "config": {
                "max_iterations": harness.config.max_iterations,
                "max_tool_calls": harness.config.max_tool_calls,
                "timeout_seconds": harness.config.timeout_seconds,
                "enable_planning": harness.config.enable_planning,
                "enable_evaluation": harness.config.enable_evaluation,
                "enable_context_compaction": harness.config.enable_context_compaction,
            },
        },
        "tools": tool_registry.get_stats(),
        "lifecycle": harness.lifecycle.get_registered_hooks(),
        "context": harness.context_manager.get_stats() if harness.context_manager else {},
        "evaluator": harness.evaluator.get_stats() if harness.evaluator else {},
    }


@router.post("/execute")
async def harness_execute(request: dict):
    """Execute a task through the harness with full management"""
    harness = get_harness()

    prompt = request.get("prompt", "")
    tools = request.get("tools")
    context = request.get("context")

    try:
        result = await harness.execute(
            prompt=prompt,
            tools=tools,
            context=context,
        )
        return {
            "status": "success",
            "result": result,
            "metrics": harness.get_metrics(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tools")
async def list_harness_tools():
    """List all registered tools in the harness"""
    tool_registry = get_tool_registry()
    tools = tool_registry.list_all()

    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "category": t.category.value,
                "requires_approval": t.requires_approval,
                "dangerous": t.dangerous,
            }
            for t in tools
        ],
        "total": len(tools),
        "schemas": tool_registry.get_schemas(),
    }


@router.post("/tools/{tool_name}/execute")
async def execute_tool(tool_name: str, arguments: dict):
    """Execute a specific tool through the harness"""
    tool_registry = get_tool_registry()

    try:
        result = await tool_registry.execute(tool_name, arguments)
        if isinstance(result, dict) and result.get("pending_approval"):
            return {
                "status": "pending_approval",
                "tool": tool_name,
                "result": result,
            }
        return {
            "status": "success",
            "tool": tool_name,
            "result": result,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/hooks")
async def register_hook(request: dict):
    """Register a lifecycle hook"""
    get_harness()
    hook_type_str = request.get("hook_type")
    callback_url = request.get("callback_url")

    try:
        hook_type = HookType(hook_type_str)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid hook type. Valid types: {[h.value for h in HookType]}",
        )

    return {
        "status": "registered",
        "hook_type": hook_type.value,
        "callback_url": callback_url,
        "message": "Hook registered successfully",
    }


@router.get("/evaluations")
async def get_evaluations(limit: int = 100, only_failed: bool = False):
    """Get evaluation history"""
    harness = get_harness()

    if not harness.evaluator:
        return {"error": "Evaluator not enabled"}

    return {
        "evaluations": harness.evaluator.get_history(limit, only_failed),
        "stats": harness.evaluator.get_stats(),
    }


@router.post("/benchmark")
async def run_benchmark(request: dict):
    """Run a benchmark with test cases"""
    harness = get_harness()

    if not harness.evaluator:
        raise HTTPException(status_code=400, detail="Evaluator not enabled")

    test_name = request.get("test_name", "unnamed_benchmark")
    test_cases = request.get("test_cases", [])
    iterations = request.get("iterations", 3)

    if not test_cases:
        raise HTTPException(status_code=400, detail="test_cases required")

    run = await harness.evaluator.run_benchmark(
        test_name=test_name,
        test_cases=test_cases,
        iterations=iterations,
    )

    return {
        "run_id": run.run_id,
        "test_name": run.test_name,
        "duration": run.duration,
        "iterations": run.iterations,
        "avg_score": run.avg_score,
        "success_rate": run.success_rate,
    }


@router.post("/compare")
async def compare_versions(request: dict):
    """Compare performance between two agent versions"""
    harness = get_harness()

    if not harness.evaluator:
        raise HTTPException(status_code=400, detail="Evaluator not enabled")

    version_a = request.get("version_a", [])
    version_b = request.get("version_b", [])
    return harness.evaluator.compare_versions(version_a, version_b)


@router.post("/reset")
async def reset_harness():
    """Reset harness state"""
    harness = get_harness()
    harness.context_manager.reset() if harness.context_manager else None
    harness.planner.clear_cache() if harness.planner else None
    return {"status": "reset", "message": "Harness state cleared"}
