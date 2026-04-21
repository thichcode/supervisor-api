"""
Bayesian Confidence Scoring for Response Validation
Uses probabilistic methods to calculate and improve response confidence
"""

import math
from typing import Dict, List, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
import structlog

logger = structlog.get_logger()


@dataclass
class ConfidenceFactors:
    """Individual confidence factors with weights"""
    context_relevance: float = 0.0      # How relevant is context
    policy_match: float = 0.0            # How well policies match
    knowledge_freshness: float = 0.0    # How recent is knowledge
    user_satisfaction: float = 0.0     # Historical user satisfaction
    agent_experience: float = 0.0       # Agent's track record
    
    # Weights for each factor (must sum to 1.0)
    weights: Dict[str, float] = field(default_factory=lambda: {
        "context_relevance": 0.25,
        "policy_match": 0.25,
        "knowledge_freshness": 0.15,
        "user_satisfaction": 0.20,
        "agent_experience": 0.15
    })


class BetaDistribution:
    """
    Beta distribution for Bayesian probability estimation.
    Used to model success/failure rates with prior beliefs.
    """
    
    def __init__(self, alpha: float = 1.0, beta: float = 1.0):
        """
        Args:
            alpha: Number of successes + 1 (prior)
            beta: Number of failures + 1 (prior)
        """
        self.alpha = alpha
        self.beta = beta
    
    @property
    def mean(self) -> float:
        """Expected value (mean) of the distribution"""
        return self.alpha / (self.alpha + self.beta)
    
    @property
    def variance(self) -> float:
        """Variance of the distribution"""
        return (self.alpha * self.beta) / (
            (self.alpha + self.beta) ** 2 * (self.alpha + self.beta + 1)
        )
    
    def pdf(self, x: float) -> float:
        """Probability density function"""
        if x <= 0 or x >= 1:
            return 0
        # Beta function approximation using log gamma
        from math import lgamma
        ln_beta = lgamma(self.alpha) + lgamma(self.beta) - lgamma(self.alpha + self.beta)
        return math.exp(
            (self.alpha - 1) * math.log(x) + 
            (self.beta - 1) * math.log(1 - x) - 
            ln_beta
        )
    
    def sample(self) -> float:
        """Sample from the distribution (for simulation)"""
        # Using approximation: transform from uniform
        import random
        return random.betavariate(self.alpha, self.beta)
    
    def update(self, successes: int, failures: int) -> 'BetaDistribution':
        """Update with new evidence"""
        return BetaDistribution(
            alpha=self.alpha + successes,
            beta=self.beta + failures
        )
    
    def probability_greater_than(self, other: 'BetaDistribution') -> float:
        """
        Calculate P(self > other) using Monte Carlo approximation.
        Used for A/B testing - which model is better.
        """
        samples = 10000
        count = 0
        
        for _ in range(samples):
            if self.sample() > other.sample():
                count += 1
        
        return count / samples


class BayesianConfidence:
    """
    Bayesian confidence calculator for response quality.
    Combines multiple evidence sources using Bayes' theorem.
    """
    
    def __init__(self):
        # Model performance tracking (Beta distributions)
        self.model_performance: Dict[str, BetaDistribution] = {
            "llama3": BetaDistribution(alpha=85, beta=15),
            "qwen2": BetaDistribution(alpha=80, beta=20),
            "gpt4": BetaDistribution(alpha=92, beta=8),
        }
        
        # User satisfaction history
        self.user_feedback: Dict[str, List[bool]] = defaultdict(list)
        
        # Factor performance tracking
        self.factor_history: List[ConfidenceFactors] = []
    
    def to_state(self) -> Dict:
        """Serialize the Bayesian state for persistence."""
        return {
            "model_performance": {
                name: {"alpha": dist.alpha, "beta": dist.beta}
                for name, dist in self.model_performance.items()
            },
            "user_feedback": {
                user_id: list(values)
                for user_id, values in self.user_feedback.items()
            },
        }
    
    def load_state(self, state: Dict) -> None:
        """Load persisted Bayesian state."""
        if not state:
            return

        model_performance = state.get("model_performance", {})
        for name, payload in model_performance.items():
            try:
                self.model_performance[name] = BetaDistribution(
                    alpha=float(payload.get("alpha", 1.0)),
                    beta=float(payload.get("beta", 1.0)),
                )
            except Exception:
                continue

        user_feedback = state.get("user_feedback", {})
        self.user_feedback = defaultdict(list)
        for user_id, values in user_feedback.items():
            if isinstance(values, list):
                self.user_feedback[user_id] = [bool(v) for v in values]
    
    def calculate_confidence(
        self,
        factors: ConfidenceFactors,
        model_name: str = "llama3",
        prior_strength: float = 1.0
    ) -> Tuple[float, Dict[str, float]]:
        """
        Calculate overall confidence using weighted Bayesian combination.
        
        Args:
            factors: Individual confidence factors
            model_name: LLM model being used
            prior_strength: Strength of prior belief (1.0 = neutral)
            
        Returns:
            Tuple of (overall_confidence, factor_scores)
        """
        # Get model performance as prior
        model_prior = self.model_performance.get(
            model_name, 
            BetaDistribution(alpha=50, beta=50)
        )
        
        # Calculate weighted factor score
        factor_scores = {}
        weighted_sum = 0.0
        weight_total = 0.0
        
        for factor_name in factors.weights:
            value = getattr(factors, factor_name, 0.0)
            weight = factors.weights[factor_name]
            
            # Apply Bayesian smoothing
            smoothed_value = (
                (value * weight * prior_strength + model_prior.mean * weight) / 
                (weight + prior_strength)
            )
            
            factor_scores[factor_name] = smoothed_value
            weighted_sum += smoothed_value * weight
            weight_total += weight
        
        # Normalize and apply model prior
        base_confidence = weighted_sum / weight_total if weight_total > 0 else 0.5
        
        # Combine with model performance (Bayesian update)
        final_confidence = (
            base_confidence * 0.7 + 
            model_prior.mean * 0.3
        )
        
        # Apply confidence adjustments
        final_confidence = self._apply_adjustments(final_confidence, factor_scores)
        
        return max(0.0, min(1.0, final_confidence)), factor_scores
    
    def _apply_adjustments(self, confidence: float, factor_scores: Dict[str, float]) -> float:
        """Apply confidence adjustments based on factor analysis."""
        # Boost if all factors are strong
        strong_factors = sum(1 for v in factor_scores.values() if v > 0.7)
        if strong_factors >= 4:
            confidence = min(1.0, confidence * 1.1)
        
        # Reduce if any critical factor is weak
        if factor_scores.get("context_relevance", 0) < 0.3:
            confidence *= 0.8
        if factor_scores.get("policy_match", 0) < 0.3:
            confidence *= 0.85
        
        return confidence
    
    def update_with_feedback(
        self,
        user_id: str,
        response_id: str,
        is_positive: bool,
        model_name: str = "llama3"
    ) -> None:
        """Update confidence model with user feedback."""
        # Update user feedback history
        self.user_feedback[user_id].append(is_positive)
        
        # Update model performance
        if model_name in self.model_performance:
            current = self.model_performance[model_name]
            if is_positive:
                self.model_performance[model_name] = current.update(1, 0)
            else:
                self.model_performance[model_name] = current.update(0, 1)
    
    def get_model_recommendation(self) -> Dict:
        """
        Get model recommendation using Bayesian model comparison.
        Used for multi-model ensemble selection.
        """
        models = list(self.model_performance.keys())
        
        if len(models) < 2:
            return {"recommended": models[0] if models else "llama3", "reason": "single_model"}
        
        # Compare all pairs
        best_model = None
        best_prob = 0.0
        comparisons = []
        
        for i, model_a in enumerate(models):
            for model_b in models[i+1:]:
                prob = self.model_performance[model_a].probability_greater_than(
                    self.model_performance[model_b]
                )
                comparisons.append({
                    "model_a": model_a,
                    "model_b": model_b,
                    "prob_a_better": prob
                })
                
                if prob > best_prob:
                    best_prob = prob
                    best_model = model_a
                elif (1 - prob) > best_prob:
                    best_prob = 1 - prob
                    best_model = model_b
        
        return {
            "recommended": best_model or "llama3",
            "confidence": best_prob,
            "comparisons": comparisons,
            "all_models": {
                m: {
                    "mean": self.model_performance[m].mean,
                    "alpha": self.model_performance[m].alpha,
                    "beta": self.model_performance[m].beta
                }
                for m in models
            }
        }
    
    def get_user_satisfaction(self, user_id: str) -> float:
        """Calculate user satisfaction score from history."""
        feedback = self.user_feedback.get(user_id, [])
        
        if not feedback:
            return 0.4  # Neutral for new users
        
        return sum(feedback) / len(feedback)


class ResponseValidator:
    """
    Validates and improves response quality using Bayesian methods.
    """
    
    def __init__(self):
        self.confidence_calculator = BayesianConfidence()
    
    async def validate_response(
        self,
        response: str,
        query: str,
        context: Dict,
        policy: Dict,
        knowledge: Dict,
        model_name: str = "llama3"
    ) -> Dict:
        """
        Validate a response and calculate confidence scores.
        
        Returns:
            Validation result with confidence, issues, and suggestions
        """
        factors = self._extract_factors(response, query, context, policy, knowledge)
        
        confidence, factor_scores = self.confidence_calculator.calculate_confidence(
            factors, model_name
        )
        
        issues = self._detect_issues(response, query, context)
        suggestions = self._generate_suggestions(issues, factor_scores)
        
        needs_review = confidence < 0.7 or len(issues) > 2
        
        return {
            "is_valid": confidence >= 0.6 and len(issues) < 3,
            "confidence": confidence,
            "factor_scores": factor_scores,
            "issues": issues,
            "suggestions": suggestions,
            "needs_review": needs_review,
            "recommended_action": "accept" if confidence >= 0.8 else ("review" if confidence >= 0.6 else "reject")
        }
    
    def _extract_factors(
        self,
        response: str,
        query: str,
        context: Dict,
        policy: Dict,
        knowledge: Dict
    ) -> ConfidenceFactors:
        """Extract confidence factors from response analysis."""
        factors = ConfidenceFactors()
        
        # Context relevance
        query_terms = set(query.lower().split())
        response_terms = set(response.lower().split())
        overlap = len(query_terms & response_terms)
        factors.context_relevance = min(1.0, overlap / max(1, len(query_terms)))
        
        # Policy match
        if policy.get("relevant_policies"):
            factors.policy_match = min(1.0, len(policy["relevant_policies"]) / 3)
        else:
            factors.policy_match = 0.5
        
        # Knowledge freshness (simple heuristic)
        if knowledge.get("knowledge_results"):
            factors.knowledge_freshness = 0.8
        elif knowledge.get("patterns"):
            factors.knowledge_freshness = 0.6
        else:
            factors.knowledge_freshness = 0.4
        
        return factors
    
    def _detect_issues(self, response: str, query: str, context: Dict) -> List[str]:
        """Detect issues in the response."""
        issues = []
        
        # Too short
        if len(response) < 50:
            issues.append("Response too short")
        
        # Too long
        if len(response) > 2000:
            issues.append("Response too long")
        
        # Doesn't answer question
        query_lower = query.lower()
        question_words = ["what", "why", "how", "when", "who", "where", "là gì", "tại sao", "như thế nào"]
        is_question = any(qw in query_lower for qw in question_words)
        
        if is_question and "?" not in response:
            issues.append("Question not directly answered")
        
        # Missing context references
        if context.get("case_info") and "case" not in response.lower():
            issues.append("Case context not referenced")
        
        return issues
    
    def _generate_suggestions(
        self,
        issues: List[str],
        factor_scores: Dict[str, float]
    ) -> List[str]:
        """Generate improvement suggestions."""
        suggestions = []
        
        if "Response too short" in issues:
            suggestions.append("Add more details or explanation")
        
        if "Response too long" in issues:
            suggestions.append("Condense the response")
        
        if factor_scores.get("context_relevance", 0) < 0.5:
            suggestions.append("Better relate to user's query")
        
        if factor_scores.get("policy_match", 0) < 0.5:
            suggestions.append("Include relevant policies or guidelines")
        
        if not suggestions:
            suggestions.append("Response looks good!")
        
        return suggestions


# Ensemble scoring for multi-model responses
class EnsembleScorer:
    """
    Combines responses from multiple models to select the best one.
    Uses weighted scoring based on multiple criteria.
    """
    
    def __init__(self):
        self.confidence_calculator = BayesianConfidence()
    
    async def select_best_response(
        self,
        responses: List[Dict],
        query: str,
        context: Dict
    ) -> Tuple[int, Dict]:
        """
        Select the best response from multiple model outputs.
        
        Args:
            responses: List of response dicts with 'content', 'model', 'confidence'
            query: Original user query
            context: Conversation context
            
        Returns:
            Tuple of (best_index, scoring_details)
        """
        if not responses:
            return -1, {"error": "No responses to compare"}
        
        if len(responses) == 1:
            return 0, {"reason": "single_response"}
        
        scored_responses = []
        
        for i, resp in enumerate(responses):
            score = self._calculate_response_score(
                resp.get("content", ""),
                resp.get("model", "unknown"),
                query,
                context
            )
            scored_responses.append({
                "index": i,
                "model": resp.get("model", "unknown"),
                "score": score["total_score"],
                "details": score
            })
        
        # Sort by score
        scored_responses.sort(key=lambda x: x["score"], reverse=True)
        
        return scored_responses[0]["index"], {
            "best_model": scored_responses[0]["model"],
            "best_score": scored_responses[0]["score"],
            "all_scores": scored_responses
        }
    
    def _calculate_response_score(
        self,
        content: str,
        model: str,
        query: str,
        context: Dict
    ) -> Dict:
        """Calculate multi-criteria score for a response."""
        scores = {}
        
        # Length score (prefer medium length)
        length = len(content)
        if 100 <= length <= 500:
            scores["length"] = 1.0
        elif 50 <= length < 100 or 500 < length <= 1000:
            scores["length"] = 0.7
        else:
            scores["length"] = 0.3
        
        # Query relevance score
        query_terms = set(query.lower().split())
        content_terms = set(content.lower().split())
        relevance = len(query_terms & content_terms) / max(1, len(query_terms))
        scores["relevance"] = min(1.0, relevance * 2)
        
        # Model performance score
        model_dist = self.confidence_calculator.model_performance.get(
            model, BetaDistribution(alpha=50, beta=50)
        )
        scores["model_quality"] = model_dist.mean
        
        # Context awareness score
        if context.get("user_info") and "bạn" in content.lower():
            scores["context_aware"] = 0.8
        else:
            scores["context_aware"] = 0.5
        
        # Language quality (Vietnamese check)
        vietnamese_chars = sum(1 for c in content if '\u00C0' <= c <= '\u1EFF')
        if vietnamese_chars > len(content) * 0.3:
            scores["language"] = 0.9
        else:
            scores["language"] = 0.5
        
        # Calculate weighted total
        weights = {
            "length": 0.1,
            "relevance": 0.3,
            "model_quality": 0.3,
            "context_aware": 0.15,
            "language": 0.15
        }
        
        total = sum(scores[k] * weights[k] for k in weights)
        
        return {
            "total_score": total,
            "component_scores": scores,
            "weights_used": weights
        }


# Example usage
if __name__ == "__main__":
    print("=== Bayesian Confidence Scoring Demo ===\n")
    
    # Test confidence calculation
    calc = BayesianConfidence()
    
    factors = ConfidenceFactors(
        context_relevance=0.8,
        policy_match=0.7,
        knowledge_freshness=0.6,
        user_satisfaction=0.75,
        agent_experience=0.8
    )
    
    confidence, factor_scores = calc.calculate_confidence(factors, "llama3")
    print(f"Calculated Confidence: {confidence:.3f}")
    print(f"Factor Scores: {factor_scores}\n")
    
    # Test model recommendation
    recommendation = calc.get_model_recommendation()
    print(f"Recommended Model: {recommendation['recommended']}")
    print(f"Confidence in recommendation: {recommendation['confidence']:.2%}\n")
    
    # Test response validation
    validator = ResponseValidator()
    result = validator.validate_response(
        response="Đây là câu trả lời về chính sách nghỉ phép của công ty. Nhân viên được nghỉ 12 ngày/năm.",
        query="Chính sách nghỉ phép là gì?",
        context={"user_info": {"name": "Thuong"}},
        policy={"relevant_policies": ["nghỉ phép"]},
        knowledge={"knowledge_results": []}
    )
    print(f"Validation Result: {result['recommended_action']}")
    print(f"Confidence: {result['confidence']:.3f}")
    print(f"Issues: {result['issues']}")
