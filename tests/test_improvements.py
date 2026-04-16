"""
Comprehensive Tests for All Supervisor Pipeline Improvements
Tests: BM25 search, Bayesian confidence, LRU cache, agent routing, ensemble
"""

import pytest

# Import all components
from src.knowledge.bm25_search import BM25Search, HybridSearch
from src.core.bayesian_confidence import (
    BayesianConfidence, ConfidenceFactors, BetaDistribution
)
from src.memory.lru_cache import LRUCache, PolicyCache, KnowledgeCache
from src.agents.router import AgentType, create_router
from src.llm.ensemble import MultiModelEnsemble, EnsembleConfig, ModelResult
# Pipeline module removed - functionality integrated into src/core/supervisor.py
# from src.pipeline.supervisor_pipeline import create_pipeline


class TestBM25Search:
    """Tests for BM25/TF-IDF search"""
    
    def test_bm25_basic_search(self):
        """Test basic BM25 search functionality"""
        search = BM25Search()
        
        docs = [
            ("doc1", "Chính sách nghỉ phép năm 2024", "Nghỉ phép"),
            ("doc2", "Giờ làm việc công ty", "Giờ làm"),
            ("doc3", "Quy định bảo hiểm xã hội", "Bảo hiểm"),
        ]
        
        for doc_id, text, title in docs:
            search.add_document(doc_id, text, title)
        
        # Search for phép
        results = search.search("nghỉ phép", top_k=3)
        
        assert len(results) > 0
        assert results[0]["doc_id"] == "doc1"
        assert results[0]["score"] > 0
    
    def test_bm25_tokenization(self):
        """Test that BM25 properly tokenizes Vietnamese text"""
        search = BM25Search()
        
        search.add_document("viet", "Tiếng Việt là ngôn ngữ tuyệt vời", "Vietnamese")
        search.add_document("english", "English is a great language", "English")
        
        results = search.search("tiếng việt", top_k=2)
        
        assert len(results) > 0
        assert results[0]["doc_id"] == "viet"
    
    def test_hybrid_search(self):
        """Test hybrid BM25 + TF-IDF search"""
        search = HybridSearch(bm25_weight=0.7, tfidf_weight=0.3)
        
        docs = [
            ("p1", "Chính sách bảo hiểm", "Bảo hiểm"),
            ("p2", "Quy định bảo mật", "Bảo mật"),
            ("p3", "Chính sách lương", "Lương"),
        ]
        
        for doc_id, text, title in docs:
            search.add_document(doc_id, text, title)
        
        results = search.search("bảo hiểm", top_k=3)
        
        assert len(results) >= 2  # Should find both bảo hiểm and bảo mật related
    
    def test_empty_query(self):
        """Test handling of empty query"""
        search = BM25Search()
        search.add_document("doc1", "some text", "Title")
        
        results = search.search("", top_k=5)
        assert len(results) == 0
    
    def test_search_top_k_limit(self):
        """Test that search respects top_k parameter"""
        search = BM25Search()
        
        for i in range(10):
            search.add_document(f"doc{i}", f"document {i} content", f"Doc {i}")
        
        results = search.search("document", top_k=3)
        assert len(results) == 3


class TestBayesianConfidence:
    """Tests for Bayesian confidence scoring"""
    
    def test_beta_distribution_basic(self):
        """Test Beta distribution basic operations"""
        dist = BetaDistribution(alpha=10, beta=2)
        
        assert 0.5 < dist.mean < 1.0
        assert dist.variance > 0
    
    def test_beta_distribution_update(self):
        """Test Beta distribution update with new evidence"""
        dist = BetaDistribution(alpha=5, beta=5)  # Start neutral
        
        # Add 5 successes, 1 failure
        updated = dist.update(5, 1)
        
        assert updated.alpha == 10  # 5 + 5
        assert updated.beta == 6   # 5 + 1
    
    def test_confidence_calculation(self):
        """Test overall confidence calculation"""
        calc = BayesianConfidence()
        
        factors = ConfidenceFactors(
            context_relevance=0.8,
            policy_match=0.7,
            knowledge_freshness=0.6,
            user_satisfaction=0.75,
            agent_experience=0.8
        )
        
        confidence, scores = calc.calculate_confidence(factors, "llama3")
        
        assert 0 <= confidence <= 1
        assert len(scores) == 5  # All 5 factors scored
    
    def test_confidence_with_weak_context(self):
        """Test that weak context reduces confidence"""
        calc = BayesianConfidence()
        
        factors_weak = ConfidenceFactors(context_relevance=0.2)
        factors_strong = ConfidenceFactors(context_relevance=0.9)
        
        weak_conf, _ = calc.calculate_confidence(factors_weak, "llama3")
        strong_conf, _ = calc.calculate_confidence(factors_strong, "llama3")
        
        assert strong_conf > weak_conf
    
    def test_model_recommendation(self):
        """Test model recommendation based on performance"""
        calc = BayesianConfidence()
        
        # Clear default models and add test models
        calc.model_performance = {}
        
        # Simulate different model performance
        calc.model_performance["model_a"] = BetaDistribution(alpha=90, beta=10)
        calc.model_performance["model_b"] = BetaDistribution(alpha=70, beta=30)
        
        rec = calc.get_model_recommendation()
        
        assert "recommended" in rec
        assert rec["recommended"] in ["model_a", "model_b", "llama3"]


class TestLRUCache:
    """Tests for LRU cache"""
    
    def test_basic_cache_operations(self):
        """Test basic get/set operations"""
        cache = LRUCache(maxsize=3)
        
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        
        assert cache.get("key1") == "value1"
        assert cache.get("key2") == "value2"
    
    def test_lru_eviction(self):
        """Test that LRU eviction works correctly"""
        cache = LRUCache(maxsize=3)
        
        cache.set("a", "1")
        cache.set("b", "2")
        cache.set("c", "3")
        
        # Access 'a' to make it recently used
        cache.get("a")
        
        # Add new item - should evict 'b' (least recently used)
        cache.set("d", "4")
        
        assert cache.get("a") == "1"  # Still there (was accessed)
        assert cache.get("b") is None  # Evicted
        assert cache.get("c") == "3"   # Still there
        assert cache.get("d") == "4"    # New item
    
    def test_cache_update(self):
        """Test that updating existing key works"""
        cache = LRUCache(maxsize=3)
        
        cache.set("key", "value1")
        cache.set("key", "value2")
        
        assert cache.get("key") == "value2"
        assert len(cache) == 1
    
    def test_cache_stats(self):
        """Test cache statistics tracking"""
        cache = LRUCache(maxsize=3)
        
        cache.set("a", "1")
        cache.get("a")  # Hit
        cache.get("b")  # Miss
        
        stats = cache.get_stats()
        
        assert stats["hit_count"] == 1
        assert stats["miss_count"] == 1
        assert stats["hit_rate"] == 0.5
    
    def test_policy_cache(self):
        """Test specialized policy cache"""
        cache = PolicyCache(maxsize=10)
        
        results = [{"policy": "policy1"}, {"policy": "policy2"}]
        cache.set_policy("query about leave", "leave", results)
        
        cached = cache.get_policy("query about leave", "leave")
        assert cached == results
    
    def test_knowledge_cache(self):
        """Test specialized knowledge cache"""
        cache = KnowledgeCache(maxsize=10)
        
        results = [{"kb": "result1"}]
        cache.set_results("how to reset password", "faq", results)
        
        cached = cache.get_results("how to reset password", "faq")
        assert cached == results


class TestAgentRouter:
    """Tests for agent routing"""
    
    def test_basic_routing(self):
        """Test basic query routing"""
        router = create_router("basic")
        
        path = router.route("chính sách nghỉ phép", query_type="policy")
        
        assert len(path) > 0
        assert path[0] in AgentType
    
    def test_different_query_types(self):
        """Test routing for different query types"""
        router = create_router("basic")
        
        policy_path = router.route("chính sách nghỉ", query_type="policy")
        faq_path = router.route("cách reset password", query_type="faq")
        
        # Different query types should potentially route differently
        assert policy_path is not None
        assert faq_path is not None
    
    def test_path_cost_calculation(self):
        """Test path cost calculation"""
        router = create_router("basic")
        
        path = router.route("test query", query_type="general")
        cost = router.get_path_cost(path)
        
        assert "total_latency_ms" in cost
        assert "expected_success_rate" in cost
        assert cost["total_latency_ms"] > 0
    
    def test_adaptive_router_feedback(self):
        """Test adaptive router with feedback"""
        adaptive = create_router("adaptive")
        
        path, info = adaptive.route_with_feedback(
            "test query", 
            query_type="support"
        )
        
        # Record feedback
        adaptive.record_feedback(
            "test query",
            path=info.get("path", []),
            user_satisfied=True
        )
        
        stats = adaptive.get_recommendations()
        assert "overall_satisfaction" in stats


class TestEnsemble:
    """Tests for multi-model ensemble"""
    
    def test_weighted_vote(self):
        """Test weighted voting"""
        ensemble = MultiModelEnsemble(EnsembleConfig(
            weights={"model_a": 0.8, "model_b": 0.4}
        ))
        
        results = [
            ModelResult(model="model_a", content="answer A", confidence=0.7, success=True),
            ModelResult(model="model_b", content="answer A", confidence=0.6, success=True),
        ]
        
        winner = ensemble._weighted_vote(results)
        assert "answer A" in winner
    
    def test_best_confidence(self):
        """Test best confidence selection"""
        ensemble = MultiModelEnsemble()
        
        results = [
            ModelResult(model="m1", content="low conf", confidence=0.3, success=True),
            ModelResult(model="m2", content="high conf", confidence=0.9, success=True),
        ]
        
        winner = ensemble._best_confidence(results)
        assert winner == "high conf"
    
    def test_cascade_selection(self):
        """Test cascade selection"""
        ensemble = MultiModelEnsemble()
        
        results = [
            ModelResult(model="m1", content="failed", success=False, error="timeout"),
            ModelResult(model="m2", content="success", success=True),
        ]
        
        winner = ensemble._cascade(results)
        assert winner == "success"
    
    def test_scoring_selection(self):
        """Test scoring-based selection"""
        ensemble = MultiModelEnsemble()
        
        results = [
            ModelResult(model="m1", content="short", confidence=0.9, success=True),
            ModelResult(model="m2", content="Đây là một câu trả lời dài bằng tiếng Việt với nhiều thông tin hữu ích", confidence=0.7, success=True),
        ]
        
        winner, scores = ensemble._scoring_selection(results, "câu hỏi bằng tiếng Việt")
        
        assert winner is not None
        assert "all_scores" in scores


# Pipeline tests removed - functionality integrated into src/core/supervisor.py
# TestSupervisorPipeline and TestIntegration classes removed (pipeline deleted)
# See test_core.py and test_router_smoke.py for equivalent tests


# Run with: pytest tests/test_improvements.py -v
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])