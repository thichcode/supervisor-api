"""
Evaluator - Benchmark and evaluate agent performance

Provides:
- Execution metrics collection
- Quality scoring
- Performance benchmarking
- Comparative evaluation
"""

import time
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import statistics

import logging
from src.config import get_settings

settings = get_settings()
logger = logging.getLogger("harness.evaluator")


class MetricType(Enum):
    """Types of evaluation metrics"""
    # Performance metrics
    LATENCY = "latency"  # Response time
    THROUGHPUT = "throughput"  # Requests per second
    TOKEN_USAGE = "token_usage"  # Token consumption
    ITERATION_COUNT = "iterations"  # Number of iterations
    
    # Quality metrics
    ACCURACY = "accuracy"  # Correct output
    RELEVANCE = "relevance"  # Relevance to query
    COHERENCE = "coherence"  # Logical consistency
    COMPLETENESS = "completeness"  # Full coverage of task
    
    # Reliability metrics
    SUCCESS_RATE = "success_rate"  # Successful completions
    ERROR_RATE = "error_rate"  # Error frequency
    DRIFT_SCORE = "drift_score"  # Instruction following over time
    RECOVERY_RATE = "recovery_rate"  # Recovery from errors


@dataclass
class MetricResult:
    """Result of a single metric evaluation"""
    metric_type: MetricType
    value: float
    threshold: Optional[float] = None
    passed: bool = True
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationResult:
    """Complete evaluation result"""
    execution_id: str
    timestamp: datetime
    metrics: List[MetricResult]
    overall_score: float  # 0-100
    passed: bool
    recommendations: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkRun:
    """A single benchmark run"""
    run_id: str
    test_name: str
    start_time: datetime
    end_time: Optional[datetime] = None
    iterations: int = 1
    results: List[EvaluationResult] = field(default_factory=list)
    
    @property
    def duration(self) -> float:
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return (datetime.now() - self.start_time).total_seconds()
    
    @property
    def avg_score(self) -> float:
        if not self.results:
            return 0
        return statistics.mean(r.overall_score for r in self.results)
    
    @property
    def success_rate(self) -> float:
        if not self.results:
            return 0
        return sum(1 for r in self.results if r.passed) / len(self.results)


class Evaluator:
    """
    Agent performance evaluator
    
    Features:
    - Collect execution metrics
    - Calculate quality scores
    - Run benchmarks
    - Compare agent versions
    """
    
    def __init__(self):
        self._evaluation_history: List[EvaluationResult] = []
        self._benchmark_runs: List[BenchmarkRun] = []
        self._metric_thresholds: Dict[MetricType, float] = {
            MetricType.LATENCY: 5.0,  # seconds
            MetricType.TOKEN_USAGE: 10000,
            MetricType.ITERATION_COUNT: 50,
            MetricType.ACCURACY: 80.0,
            MetricType.SUCCESS_RATE: 90.0,
        }
    
    async def evaluate(
        self,
        execution_id: str,
        prompt: str,
        result: Any,
        metrics: Any,
        quality_score: Optional[float] = None,
    ) -> EvaluationResult:
        """
        Evaluate a single execution
        
        Calculates:
        - Performance metrics (latency, tokens, iterations)
        - Quality metrics (accuracy, coherence)
        - Overall score
        """
        metric_results = []
        
        # Performance metrics
        if metrics:
            # Latency
            if hasattr(metrics, 'duration'):
                metric_results.append(MetricResult(
                    metric_type=MetricType.LATENCY,
                    value=metrics.duration,
                    threshold=self._metric_thresholds.get(MetricType.LATENCY),
                    passed=metrics.duration < self._metric_thresholds[MetricType.LATENCY],
                    details={"unit": "seconds"},
                ))
            
            # Iterations
            if hasattr(metrics, 'iterations'):
                metric_results.append(MetricResult(
                    metric_type=MetricType.ITERATION_COUNT,
                    value=metrics.iterations,
                    threshold=self._metric_thresholds.get(MetricType.ITERATION_COUNT),
                    passed=metrics.iterations <= self._metric_thresholds[MetricType.ITERATION_COUNT],
                ))
            
            # Tool calls
            if hasattr(metrics, 'tool_calls'):
                metric_results.append(MetricResult(
                    metric_type=MetricType.THROUGHPUT,
                    value=metrics.tool_calls / metrics.duration if metrics.duration > 0 else 0,
                    details={"tool_calls": metrics.tool_calls, "duration": metrics.duration},
                ))
            
            # Error rate
            error_count = len(metrics.errors) if hasattr(metrics, 'errors') else 0
            metric_results.append(MetricResult(
                metric_type=MetricType.ERROR_RATE,
                value=error_count,
                passed=error_count == 0,
            ))
        
        # Quality metrics (using provided score or heuristic)
        if quality_score is not None:
            metric_results.append(MetricResult(
                metric_type=MetricType.ACCURACY,
                value=quality_score,
                threshold=self._metric_thresholds.get(MetricType.ACCURACY),
                passed=quality_score >= self._metric_thresholds[MetricType.ACCURACY],
            ))
        
        # Calculate overall score
        if metric_results:
            overall_score = self._calculate_overall_score(metric_results)
        else:
            overall_score = 50.0  # Default score
        
        # Determine if passed
        passed = all(m.passed for m in metric_results) and overall_score >= 70
        
        # Generate recommendations
        recommendations = self._generate_recommendations(metric_results, overall_score)
        
        evaluation = EvaluationResult(
            execution_id=execution_id,
            timestamp=datetime.now(),
            metrics=metric_results,
            overall_score=overall_score,
            passed=passed,
            recommendations=recommendations,
            metadata={
                "prompt_length": len(prompt),
                "result_length": len(str(result)),
            },
        )
        
        self._evaluation_history.append(evaluation)
        
        logger.info(
            f"Evaluation complete: {execution_id} - "
            f"score: {overall_score:.1f}, passed: {passed}"
        )
        
        return evaluation
    
    def _calculate_overall_score(self, metrics: List[MetricResult]) -> float:
        """Calculate weighted overall score"""
        total_score = 0
        total_weight = 0
        
        for metric in metrics:
            # Normalize value to 0-100
            if metric.threshold and metric.threshold > 0:
                normalized = max(0, min(100, 100 - (metric.value / metric.threshold * 100)))
            else:
                normalized = metric.value if metric.value <= 100 else 100
            
            if metric.passed:
                normalized = max(normalized, 70)  # Minimum for passed
        
        # Simple average if no weights matched
        if total_weight == 0:
            passed_count = sum(1 for m in metrics if m.passed)
            total_score = (passed_count / len(metrics)) * 100 if metrics else 50
        
        return total_score
    
    def _generate_recommendations(
        self,
        metrics: List[MetricResult],
        overall_score: float,
    ) -> List[str]:
        """Generate improvement recommendations"""
        recommendations = []
        
        for metric in metrics:
            if not metric.passed:
                if metric.metric_type == MetricType.LATENCY:
                    recommendations.append(
                        f"Reduce latency: current {metric.value:.2f}s, "
                        f"target <{metric.threshold}s"
                    )
                elif metric.metric_type == MetricType.ITERATION_COUNT:
                    recommendations.append(
                        f"Reduce iterations: current {metric.value}, "
                        f"target <{metric.threshold}"
                    )
                elif metric.metric_type == MetricType.ACCURACY:
                    recommendations.append(
                        f"Improve accuracy: current {metric.value:.1f}%, "
                        f"target >{metric.threshold}%"
                    )
                elif metric.metric_type == MetricType.ERROR_RATE:
                    recommendations.append("Reduce error rate for better reliability")
        
        if overall_score < 70:
            recommendations.append("Overall score below threshold - review agent configuration")
        
        if not recommendations:
            recommendations.append("Performance is within acceptable ranges")
        
        return recommendations
    
    async def run_benchmark(
        self,
        test_name: str,
        test_cases: List[Dict[str, Any]],
        iterations: int = 3,
    ) -> BenchmarkRun:
        """
        Run a benchmark with multiple test cases
        
        Each test case should have:
        - prompt: str
        - expected: Any (optional)
        """
        run_id = f"bench_{int(time.time())}"
        
        run = BenchmarkRun(
            run_id=run_id,
            test_name=test_name,
            start_time=datetime.now(),
            iterations=iterations,
        )
        
        logger.info(f"Starting benchmark: {test_name} with {len(test_cases)} cases")
        
        for i, test_case in enumerate(test_cases):
            for iteration in range(iterations):
                prompt = test_case.get("prompt", "")
                # Run evaluation (would need actual execution here)
                # For now, simulate with mock metrics
                mock_metrics = type('MockMetrics', (), {
                    'duration': 1.5,
                    'iterations': 5,
                    'tool_calls': 3,
                    'errors': [],
                })()
                
                result = await self.evaluate(
                    execution_id=f"{run_id}_{i}_{iteration}",
                    prompt=prompt,
                    result=test_case.get("result", ""),
                    metrics=mock_metrics,
                    quality_score=test_case.get("quality_score", 85),
                )
                
                run.results.append(result)
        
        run.end_time = datetime.now()
        self._benchmark_runs.append(run)
        
        logger.info(
            f"Benchmark complete: {test_name} - "
            f"avg score: {run.avg_score:.1f}, success rate: {run.success_rate:.1%}"
        )
        
        return run
    
    def compare_versions(
        self,
        version_a: List[str],  # execution_ids
        version_b: List[str],  # execution_ids
    ) -> Dict[str, Any]:
        """
        Compare performance between two agent versions
        """
        # Get evaluations for each version
        eval_a = [e for e in self._evaluation_history if e.execution_id in version_a]
        eval_b = [e for e in self._evaluation_history if e.execution_id in version_b]
        
        if not eval_a or not eval_b:
            return {"error": "Insufficient data for comparison"}
        
        # Calculate averages
        avg_a = statistics.mean(e.overall_score for e in eval_a)
        avg_b = statistics.mean(e.overall_score for e in eval_b)
        
        # Calculate improvement
        improvement = avg_b - avg_a
        improvement_pct = (improvement / avg_a * 100) if avg_a > 0 else 0
        
        return {
            "version_a": {
                "executions": len(eval_a),
                "avg_score": avg_a,
                "success_rate": sum(1 for e in eval_a if e.passed) / len(eval_a),
            },
            "version_b": {
                "executions": len(eval_b),
                "avg_score": avg_b,
                "success_rate": sum(1 for e in eval_b if e.passed) / len(eval_b),
            },
            "comparison": {
                "improvement": improvement,
                "improvement_percent": improvement_pct,
                "winner": "b" if improvement > 0 else "a",
            },
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get evaluator statistics"""
        recent_evals = self._evaluation_history[-100:]
        
        return {
            "total_evaluations": len(self._evaluation_history),
            "benchmark_runs": len(self._benchmark_runs),
            "recent_avg_score": statistics.mean(
                e.overall_score for e in recent_evals
            ) if recent_evals else 0,
            "recent_success_rate": sum(
                1 for e in recent_evals if e.passed
            ) / len(recent_evals) if recent_evals else 0,
            "metric_thresholds": {
                m.value: t for m, t in self._metric_thresholds.items()
            },
        }
    
    def get_history(
        self,
        limit: int = 100,
        only_failed: bool = False,
    ) -> List[Dict[str, Any]]:
        """Get evaluation history"""
        history = self._evaluation_history
        
        if only_failed:
            history = [e for e in history if not e.passed]
        
        return [
            {
                "execution_id": e.execution_id,
                "timestamp": e.timestamp.isoformat(),
                "overall_score": e.overall_score,
                "passed": e.passed,
                "metrics": [
                    {
                        "type": m.metric_type.value,
                        "value": m.value,
                        "passed": m.passed,
                    }
                    for m in e.metrics
                ],
                "recommendations": e.recommendations,
            }
            for e in history[-limit:]
        ]
