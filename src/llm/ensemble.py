"""
Multi-Model Ensemble for Improved Response Quality
Combines outputs from multiple LLMs using weighted voting/scoring
"""

import asyncio
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import Counter
import structlog

logger = structlog.get_logger()


class EnsembleStrategy(Enum):
    """Ensemble strategies for combining model outputs"""
    MAJORITY_VOTE = "majority_vote"      # Pick most common answer
    WEIGHTED_VOTE = "weighted_vote"       # Weight by model performance
    BEST_CONFIDENCE = "best_confidence"   # Pick highest confidence
    SCORING = "scoring"                   # Score all responses and pick best
    CASCADE = "cascade"                   # Try models in order until success


@dataclass
class ModelResult:
    """Result from a single model"""
    model: str
    content: str
    confidence: float = 0.4
    latency_ms: float = 0
    success: bool = True
    error: Optional[str] = None
    metadata: Dict = field(default_factory=dict)


@dataclass
class EnsembleConfig:
    """Configuration for ensemble"""
    models: List[str] = field(default_factory=lambda: ["llama3.1:8b", "qwen2.5:7b"])
    strategy: EnsembleStrategy = EnsembleStrategy.WEIGHTED_VOTE
    max_parallel: int = 2
    timeout_per_model: float = 60.0
    min_responses: int = 1
    weights: Dict[str, float] = field(default_factory=lambda: {
        "llama3.1:8b": 0.5,
        "qwen2.5:7b": 0.5,
        "gpt-4": 0.8,
        "gpt-3.5-turbo": 0.6,
    })
    
    # Scoring criteria weights
    scoring_weights: Dict[str, float] = field(default_factory=lambda: {
        "length_score": 0.1,      # Prefer medium length
        "language_score": 0.2,    # Vietnamese quality
        "confidence_score": 0.3,  # Model confidence
        "relevance_score": 0.4,  # Query relevance
    })


class MultiModelEnsemble:
    """
    Ensemble that combines outputs from multiple LLMs.
    Supports parallel execution, weighted voting, and scoring-based selection.
    """
    
    def __init__(self, config: Optional[EnsembleConfig] = None):
        self.config = config or EnsembleConfig()
        self._result_cache: Dict[str, List[ModelResult]] = {}
    
    async def query_ensemble(
        self,
        prompt: str,
        system_prompt: str = "",
        models: Optional[List[str]] = None,
        strategy: Optional[EnsembleStrategy] = None
    ) -> Tuple[str, List[ModelResult], Dict]:
        """
        Query multiple models and combine results.
        
        Args:
            prompt: User message
            system_prompt: System instructions
            models: Override models to use
            strategy: Override ensemble strategy
            
        Returns:
            Tuple of (final_response, all_results, metadata)
        """
        target_models = models or self.config.models
        target_strategy = strategy or self.config.strategy
        
        # Execute models in parallel (limited)
        results = await self._execute_parallel(
            prompt, system_prompt, target_models
        )
        
        if not results:
            return "", [], {"error": "No models responded successfully"}
        
        # Combine results based on strategy
        if target_strategy == EnsembleStrategy.MAJORITY_VOTE:
            final_response = self._majority_vote(results)
        elif target_strategy == EnsembleStrategy.WEIGHTED_VOTE:
            final_response = self._weighted_vote(results)
        elif target_strategy == EnsembleStrategy.BEST_CONFIDENCE:
            final_response = self._best_confidence(results)
        elif target_strategy == EnsembleStrategy.SCORING:
            final_response, scores = self._scoring_selection(results, prompt)
            return final_response, results, {"scores": scores}
        elif target_strategy == EnsembleStrategy.CASCADE:
            final_response = self._cascade(results)
        else:
            final_response = results[0].content
        
        metadata = {
            "strategy": target_strategy.value,
            "models_queried": target_models,
            "successful": [r.model for r in results if r.success],
            "best_model": results[0].model if results else None,
        }
        
        return final_response, results, metadata
    
    async def _execute_parallel(
        self,
        prompt: str,
        system_prompt: str,
        models: List[str]
    ) -> List[ModelResult]:
        """Execute models in parallel with semaphore limiting"""
        semaphore = asyncio.Semaphore(self.config.max_parallel)
        
        async def run_with_limit(model: str) -> ModelResult:
            async with semaphore:
                return await self._query_single_model(model, prompt, system_prompt)
        
        tasks = [run_with_limit(model) for model in models]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter successful results
        successful = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                successful.append(ModelResult(
                    model=models[i],
                    content="",
                    success=False,
                    error=str(result)
                ))
            elif result.success:
                successful.append(result)
        
        return successful
    
    async def _query_single_model(
        self,
        model: str,
        prompt: str,
        system_prompt: str
    ) -> ModelResult:
        """Query a single model (placeholder - integrate with actual LLM client)"""
        import time
        
        start_time = time.time()
        
        try:
            # This would integrate with the actual LLM provider
            # For now, return a placeholder
            content = f"[{model} response placeholder]"
            latency = (time.time() - start_time) * 1000
            
            return ModelResult(
                model=model,
                content=content,
                confidence=0.7,
                latency_ms=latency,
                success=True
            )
        except Exception as e:
            return ModelResult(
                model=model,
                content="",
                success=False,
                error=str(e),
                latency_ms=(time.time() - start_time) * 1000
            )
    
    def _majority_vote(self, results: List[ModelResult]) -> str:
        """Select response with most common content (simplified)"""
        # For text, use similarity matching
        if len(results) == 1:
            return results[0].content
        
        # Simple: return longest response (usually more detailed)
        return max(results, key=lambda r: len(r.content)).content
    
    def _weighted_vote(self, results: List[ModelResult]) -> str:
        """Weight votes by model performance"""
        if len(results) == 1:
            return results[0].content
        
        # Score each result by confidence * weight
        scored = []
        for r in results:
            weight = self.config.weights.get(r.model, 0.5)
            score = r.confidence * weight
            scored.append((r, score))
        
        # Return highest scored
        best = max(scored, key=lambda x: x[1])
        return best[0].content
    
    def _best_confidence(self, results: List[ModelResult]) -> str:
        """Select highest confidence response"""
        if not results:
            return ""
        best = max(results, key=lambda r: r.confidence)
        return best.content
    
    def _cascade(self, results: List[ModelResult]) -> str:
        """Try models in order until one succeeds"""
        # Results are already ordered by model list
        for r in results:
            if r.success and r.content:
                return r.content
        return ""
    
    def _scoring_selection(
        self,
        results: List[ModelResult],
        query: str
    ) -> Tuple[str, Dict]:
        """
        Score responses on multiple criteria and select best.
        Returns both selected response and all scores.
        """
        if len(results) == 1:
            return results[0].content, {"winner": results[0].model}
        
        weights = self.config.scoring_weights
        query_terms = set(query.lower().split())
        
        scored_results = []
        
        for r in results:
            scores = {}
            
            # Length score (prefer medium)
            length = len(r.content)
            if 100 <= length <= 500:
                scores["length_score"] = 1.0
            elif 50 <= length < 100 or 500 < length <= 1000:
                scores["length_score"] = 0.7
            else:
                scores["length_score"] = 0.3
            
            # Language score (Vietnamese characters)
            vietnamese_chars = sum(1 for c in r.content if 'à' <= c <= 'ỹ')
            if vietnamese_chars > len(r.content) * 0.3:
                scores["language_score"] = 0.9
            else:
                scores["language_score"] = 0.5
            
            # Confidence score (from model)
            scores["confidence_score"] = r.confidence
            
            # Relevance score (query term overlap)
            response_terms = set(r.content.lower().split())
            overlap = len(query_terms & response_terms)
            scores["relevance_score"] = min(1.0, overlap / max(1, len(query_terms)))
            
            # Weighted total
            total = sum(scores[k] * weights[k] for k in weights)
            scores["total"] = total
            
            scored_results.append({
                "model": r.model,
                "content": r.content,
                "scores": scores
            })
        
        # Sort by total score
        scored_results.sort(key=lambda x: x["scores"]["total"], reverse=True)
        
        winner = scored_results[0]
        return winner["content"], {
            "winner": winner["model"],
            "all_scores": scored_results
        }


class AdaptiveEnsemble(MultiModelEnsemble):
    """
    Ensemble that adapts model weights based on performance.
    Uses Bayesian updating for model quality estimation.
    """
    
    def __init__(self, config: Optional[EnsembleConfig] = None):
        super().__init__(config)
        # Track per-task-type performance
        self.task_performance: Dict[str, Dict[str, List[bool]]] = {}
    
    async def query_with_adaptation(
        self,
        prompt: str,
        system_prompt: str,
        task_type: str = "general"
    ) -> Tuple[str, List[ModelResult], Dict]:
        """
        Query ensemble with task-type specific model weights.
        """
        # Adjust weights based on task performance
        self._adjust_weights_for_task(task_type)
        
        result = await self.query_ensemble(prompt, system_prompt)
        
        # Record for future adaptation
        self._record_task_type(task_type)
        
        return result
    
    def _adjust_weights_for_task(self, task_type: str):
        """Adjust model weights based on historical task performance"""
        if task_type not in self.task_performance:
            return
        
        model_scores = self.task_performance[task_type]
        
        # Calculate per-model success rates
        new_weights = {}
        for model, results in model_scores.items():
            if results:
                success_rate = sum(results) / len(results)
                new_weights[model] = 0.3 + (success_rate * 0.7)  # Blend with base
        
        if new_weights:
            # Smooth update (blend with existing weights)
            for model in self.config.weights:
                if model in new_weights:
                    self.config.weights[model] = (
                        0.7 * self.config.weights[model] + 
                        0.3 * new_weights[model]
                    )
    
    def _record_task_type(self, task_type: str):
        """Record that we used this task type (for future adaptation)"""
        if task_type not in self.task_performance:
            self.task_performance[task_type] = {}
    
    def record_feedback(
        self,
        task_type: str,
        model: str,
        success: bool
    ):
        """Record feedback for a model on a task type"""
        if task_type not in self.task_performance:
            self.task_performance[task_type] = {}
        
        if model not in self.task_performance[task_type]:
            self.task_performance[task_type][model] = []
        
        self.task_performance[task_type][model].append(success)
        
        # Keep only last 100 records per model
        if len(self.task_performance[task_type][model]) > 100:
            self.task_performance[task_type][model] = (
                self.task_performance[task_type][model][-100:]
            )
    
    def get_model_recommendations(self, task_type: str) -> List[Tuple[str, float]]:
        """Get recommended models for a task type based on history"""
        if task_type not in self.task_performance:
            return list(self.config.weights.items())
        
        model_scores = self.task_performance[task_type]
        recommendations = []
        
        for model, results in model_scores.items():
            if results:
                avg_score = sum(results) / len(results)
                recommendations.append((model, avg_score))
        
        recommendations.sort(key=lambda x: x[1], reverse=True)
        return recommendations


class VotingEnsemble:
    """
    Specialized ensemble for structured outputs (choices, ratings, etc).
    Uses majority voting for discrete choices.
    """
    
    def __init__(self):
        self.results: List[ModelResult] = []
    
    def add_result(self, result: ModelResult):
        """Add a model result"""
        self.results.append(result)
    
    def vote(self) -> Tuple[str, float]:
        """
        Perform majority voting.
        
        Returns:
            Tuple of (winner, agreement_rate)
        """
        if not self.results:
            return "", 0.0
        
        # For text responses, use exact match voting
        contents = [r.content for r in self.results if r.success]
        
        if not contents:
            return "", 0.0
        
        # Count occurrences
        counter = Counter(contents)
        winner, count = counter.most_common(1)[0]
        
        agreement = count / len(contents)
        
        return winner, agreement
    
    def weighted_vote(self, weights: Dict[str, float]) -> Tuple[str, float]:
        """
        Weighted voting based on model quality.
        
        Returns:
            Tuple of (winner, confidence)
        """
        if not self.results:
            return "", 0.0
        
        # Score each response
        scored: Dict[str, Tuple[float, int]] = {}
        
        for r in self.results:
            if not r.success:
                continue
            
            weight = weights.get(r.model, 0.5)
            score = r.confidence * weight
            
            if r.content not in scored:
                scored[r.content] = (score, 1)
            else:
                old_score, count = scored[r.content]
                scored[r.content] = (old_score + score, count + 1)
        
        if not scored:
            return "", 0.0
        
        # Find best weighted score
        winner = max(scored.items(), key=lambda x: x[1][0])
        
        return winner[0], winner[1][0]


# Example usage
if __name__ == "__main__":
    print("=== Multi-Model Ensemble Demo ===\n")
    
    # Create ensemble
    config = EnsembleConfig(
        models=["llama3.1:8b", "qwen2.5:7b"],
        strategy=EnsembleStrategy.WEIGHTED_VOTE,
        weights={
            "llama3.1:8b": 0.6,
            "qwen2.5:7b": 0.4,
        }
    )
    
    ensemble = MultiModelEnsemble(config)
    
    # Simulate results
    print("Simulating ensemble responses...\n")
    
    # Test different strategies
    results = [
        ModelResult(
            model="llama3.1:8b",
            content="Để reset password, bạn vào Settings > Security > Reset Password",
            confidence=0.85,
            latency_ms=1200,
            success=True
        ),
        ModelResult(
            model="qwen2.5:7b",
            content="Cách reset password: Vào mục Cài đặt, chọn Bảo mật, nhấn Đặt lại mật khẩu",
            confidence=0.75,
            latency_ms=800,
            success=True
        ),
    ]
    
    print("=== Voting Results ===")
    
    # Majority vote
    majority = ensemble._majority_vote(results)
    print(f"Majority Vote: {majority[:50]}...")
    
    # Weighted vote
    weighted = ensemble._weighted_vote(results)
    print(f"Weighted Vote: {weighted[:50]}...")
    
    # Best confidence
    best = ensemble._best_confidence(results)
    print(f"Best Confidence: {best[:50]}...")
    
    # Scoring selection
    scored, details = ensemble._scoring_selection(results, "làm thế nào để reset password")
    print(f"\nScoring Selection Winner: {details.get('winner')}")
    if 'all_scores' in details:
        for item in details['all_scores']:
            print(f"  {item['model']}: {item['scores']['total']:.3f}")
    
    print("\n=== Adaptive Ensemble Demo ===")
    
    adaptive = AdaptiveEnsemble()
    adaptive.record_feedback("support", "llama3.1:8b", True)
    adaptive.record_feedback("support", "llama3.1:8b", True)
    adaptive.record_feedback("support", "qwen2.5:7b", False)
    
    recs = adaptive.get_model_recommendations("support")
    print(f"Recommendations for 'support': {recs}")