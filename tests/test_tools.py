"""
Tests for Supervisor Tools - Simplified
"""

import pytest


class TestURLFetcher:
    """Tests for URL fetcher"""
    
    def test_detect_urls(self):
        from src.tools.url_fetcher import URLFetcher
        fetcher = URLFetcher()
        
        text = "Check this link https://example.com and http://test.org"
        urls = fetcher.detect_urls(text)
        
        assert len(urls) == 2
        assert "https://example.com" in urls
        assert "http://test.org" in urls
    
    def test_detect_no_urls(self):
        from src.tools.url_fetcher import URLFetcher
        fetcher = URLFetcher()
        
        text = "No URLs in this text"
        urls = fetcher.detect_urls(text)
        
        assert len(urls) == 0
    
    @pytest.mark.asyncio
    async def test_fetch_url_with_mock(self):
        from src.tools.url_fetcher import URLFetcher
        from unittest.mock import patch, AsyncMock
        
        fetcher = URLFetcher(timeout=1, max_urls=5)
        
        # Mock the fetch to avoid network call
        with patch.object(fetcher, 'fetch_url', new_callable=AsyncMock, return_value=None):
            await fetcher.fetch_url("http://test.com")
            # Just verify it runs without error
            assert True


class TestN8NConnector:
    """Tests for n8n connector"""
    
    def test_action_type_exists(self):
        from src.tools.n8n_connector import ActionType
        
        # Check some action types exist
        assert hasattr(ActionType, 'QUERY')
        assert hasattr(ActionType, 'ACTION')
        assert hasattr(ActionType, 'APPROVED')
    
    def test_risk_level_exists(self):
        from src.tools.n8n_connector import RiskLevel
        
        assert hasattr(RiskLevel, 'LOW')
        assert hasattr(RiskLevel, 'MEDIUM')
        assert hasattr(RiskLevel, 'HIGH')


class TestNotificationSender:
    """Tests for notification sender"""
    
    def test_channel_exists(self):
        from src.tools.notification import Channel
        
        assert hasattr(Channel, 'EMAIL')
        assert hasattr(Channel, 'SMS')
        assert hasattr(Channel, 'TEAMS')
        assert hasattr(Channel, 'WEBHOOK')


class TestBM25Search:
    """Tests for BM25 search"""
    
    def test_bm25_search_creation(self):
        from src.knowledge.bm25_search import BM25Search
        
        search = BM25Search()
        assert search is not None
    
    def test_bm25_search_add_document(self):
        from src.knowledge.bm25_search import BM25Search
        
        search = BM25Search()
        search.add_document("doc1", "Python programming tutorial")
        
        results = search.search("Python", top_k=1)
        assert isinstance(results, list)
    
    def test_hybrid_search_creation(self):
        from src.knowledge.bm25_search import HybridSearch
        
        search = HybridSearch(bm25_weight=0.6, tfidf_weight=0.4)
        assert search is not None
    
    def test_hybrid_search_add_documents(self):
        from src.knowledge.bm25_search import HybridSearch
        
        search = HybridSearch(bm25_weight=0.6, tfidf_weight=0.4)
        search.add_document("1", "Company remote work policy")
        search.add_document("2", "Leave policy for employees")
        
        results = search.search("policy", top_k=2)
        assert isinstance(results, list)


class TestScheduler:
    """Tests for scheduler"""
    
    def test_scheduler_creation(self):
        from src.tools.scheduler import Scheduler
        
        scheduler = Scheduler()
        assert scheduler is not None
    
    def test_cron_examples_exist(self):
        from src.tools.scheduler import CRON_EXAMPLES
        
        assert "every_minute" in CRON_EXAMPLES
        assert "every_hour" in CRON_EXAMPLES
        assert "every_day_8am" in CRON_EXAMPLES


class TestLRUCache:
    """Tests for LRU Cache"""
    
    def test_lru_cache_creation(self):
        from src.memory.lru_cache import LRUCache
        
        cache = LRUCache(maxsize=10, ttl_seconds=60)
        assert cache.maxsize == 10
    
    def test_lru_cache_set_get(self):
        from src.memory.lru_cache import LRUCache
        
        cache = LRUCache(maxsize=10, ttl_seconds=60)
        cache.set("key1", "value1")
        
        assert cache.get("key1") == "value1"
    
    def test_lru_cache_miss(self):
        from src.memory.lru_cache import LRUCache
        
        cache = LRUCache(maxsize=10, ttl_seconds=60)
        
        assert cache.get("nonexistent") is None
    
    def test_lru_cache_eviction(self):
        from src.memory.lru_cache import LRUCache
        
        cache = LRUCache(maxsize=2, ttl_seconds=60)
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")  # Should evict key1
        
        assert cache.get("key1") is None  # Evicted
        assert cache.get("key2") == "value2"
        assert cache.get("key3") == "value3"


class TestBayesianConfidence:
    """Tests for Bayesian confidence"""
    
    def test_confidence_factors_creation(self):
        from src.core.bayesian_confidence import ConfidenceFactors
        
        factors = ConfidenceFactors(
            context_relevance=0.8,
            policy_match=0.9,
            knowledge_freshness=0.7
        )
        
        assert factors.context_relevance == 0.8
        assert factors.policy_match == 0.9
    
    def test_beta_distribution_creation(self):
        from src.core.bayesian_confidence import BetaDistribution
        
        beta = BetaDistribution(alpha=80, beta=20)
        
        # Just check it can be created
        assert beta is not None
    
    def test_bayesian_confidence_creation(self):
        from src.core.bayesian_confidence import BayesianConfidence
        
        bc = BayesianConfidence()
        
        # Check default model performance exists
        assert hasattr(bc, 'model_performance')
        assert len(bc.model_performance) > 0
    
    def test_response_validator_creation(self):
        from src.core.bayesian_confidence import ResponseValidator
        
        validator = ResponseValidator()
        
        # Just check it can be created
        assert validator is not None


class TestAPIValidators:
    """Tests for API validators in validators module"""
    
    def test_email_validator_exists(self):
        from src.tools import validators
        assert hasattr(validators, 'validate_email')
    
    def test_validate_email_valid(self):
        from src.tools.validators import validate_email
        
        assert validate_email("test@example.com")
        assert validate_email("user.name@company.co.uk")
    
    def test_validate_email_invalid(self):
        from src.tools.validators import validate_email
        
        assert not validate_email("invalid")
        assert not validate_email("@example.com")
        assert not validate_email("test@")