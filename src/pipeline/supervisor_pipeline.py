"""
Supervisor Pipeline Integration
Brings together all improvements: BM25 search, Bayesian confidence, LRU cache, agent routing, ensemble
"""

from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
import structlog

from src.knowledge.bm25_search import HybridSearch
from src.core.bayesian_confidence import (
    BayesianConfidence, ResponseValidator
)
from src.memory.lru_cache import LRUCache, PolicyCache, KnowledgeCache
from src.agents.router import create_router
from src.llm.ensemble import MultiModelEnsemble, EnsembleConfig, EnsembleStrategy

logger = structlog.get_logger()


@dataclass
class PipelineConfig:
    """Configuration for the supervisor pipeline"""
    # Cache settings
    enable_l1_cache: bool = True
    l1_cache_size: int = 500
    l2_cache_ttl: int = 3600
    
    # Search settings
    enable_bm25_search: bool = True
    search_top_k: int = 5
    
    # Routing settings
    enable_agent_routing: bool = True
    routing_strategy: str = "adaptive"
    
    # Ensemble settings
    enable_ensemble: bool = False
    ensemble_models: List[str] = field(default_factory=lambda: ["llama3.1:8b"])
    ensemble_strategy: str = "weighted_vote"
    
    # Confidence settings
    min_confidence_threshold: float = 0.6
    require_review_threshold: float = 0.7


@dataclass
class PipelineContext:
    """Context passed through the pipeline"""
    user_id: str
    query: str
    query_type: str = "general"
    user_context: Dict = field(default_factory=dict)
    conversation_id: Optional[str] = None
    session_id: Optional[str] = None


@dataclass
class PipelineResult:
    """Result from the pipeline"""
    response: str
    confidence: float
    confidence_breakdown: Dict[str, float]
    sources: List[Dict] = field(default_factory=list)
    agents_used: List[str] = field(default_factory=list)
    cache_hit: bool = False
    metadata: Dict = field(default_factory=dict)
    needs_review: bool = False
    error: Optional[str] = None


class SupervisorPipeline:
    """
    Integrated supervisor pipeline with all improvements.
    Flow: Cache -> Routing -> Search -> LLM -> Validation -> Response
    """
    
    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or PipelineConfig()
        
        # Initialize components
        self._init_cache()
        self._init_search()
        self._init_routing()
        self._init_validation()
        self._init_ensemble()
        
        logger.info("SupervisorPipeline initialized", 
                   cache=self.config.enable_l1_cache,
                   bm25=self.config.enable_bm25_search,
                   routing=self.config.enable_agent_routing,
                   ensemble=self.config.enable_ensemble)
    
    def _init_cache(self):
        """Initialize caching layer"""
        if self.config.enable_l1_cache:
            self.policy_cache = PolicyCache(maxsize=200)
            self.knowledge_cache = KnowledgeCache(maxsize=500)
            self.response_cache = LRUCache(maxsize=300, ttl_seconds=600)
            logger.info("L1 cache initialized")
    
    def _init_search(self):
        """Initialize search engine"""
        if self.config.enable_bm25_search:
            self.policy_search = HybridSearch(bm25_weight=0.7, tfidf_weight=0.3)
            self.knowledge_search = HybridSearch(bm25_weight=0.6, tfidf_weight=0.4)
            logger.info("BM25 search initialized")
    
    def _init_routing(self):
        """Initialize agent router"""
        if self.config.enable_agent_routing:
            self.router = create_router(self.config.routing_strategy)
            logger.info("Agent router initialized", strategy=self.config.routing_strategy)
    
    def _init_validation(self):
        """Initialize confidence validator"""
        self.confidence_calc = BayesianConfidence()
        self.validator = ResponseValidator()
    
    def _init_ensemble(self):
        """Initialize ensemble"""
        if self.config.enable_ensemble:
            ensemble_config = EnsembleConfig(
                models=self.config.ensemble_models,
                strategy=EnsembleStrategy(self.config.ensemble_strategy)
            )
            self.ensemble = MultiModelEnsemble(ensemble_config)
            logger.info("Ensemble initialized", models=self.config.ensemble_models)
    
    async def process(
        self,
        context: PipelineContext,
        llm_callable: Callable,
        policy_docs: List[Dict] = None,
        knowledge_base: List[Dict] = None
    ) -> PipelineResult:
        """
        Process a query through the full pipeline.
        
        Args:
            context: Query context
            llm_callable: Async function to call LLM
            policy_docs: Optional policy documents for indexing
            knowledge_base: Optional knowledge base for indexing
            
        Returns:
            PipelineResult with response and metadata
        """
        start_time = datetime.now()
        
        try:
            # 1. Check cache first
            if self.config.enable_l1_cache:
                cache_result = await self._check_cache(context)
                if cache_result:
                    cache_result.metadata["cache_hit"] = True
                    cache_result.metadata["latency_ms"] = (
                        datetime.now() - start_time
                    ).total_seconds() * 1000
                    return cache_result
            
            # 2. Route to appropriate agents
            agents = []
            if self.config.enable_agent_routing:
                agents = await self._route_query(context)
            
            # 3. Search knowledge/policy
            search_results = await self._search(context, policy_docs, knowledge_base)
            
            # 4. Build context for LLM
            llm_context = await self._build_llm_context(context, search_results)
            
            # 5. Generate response (with optional ensemble)
            if self.config.enable_ensemble and self.ensemble:
                response = await self._generate_ensemble(context, llm_context)
            else:
                response = await self._generate_single(context, llm_context, llm_callable)
            
            # 6. Validate and score confidence
            validation = await self._validate_response(context, response, search_results)
            
            # 7. Build final result
            result = PipelineResult(
                response=response.get("content", ""),
                confidence=validation.get("confidence", 0.5),
                confidence_breakdown=validation.get("factor_scores", {}),
                sources=search_results.get("sources", []),
                agents_used=agents,
                metadata={
                    "latency_ms": (datetime.now() - start_time).total_seconds() * 1000,
                    "search_results_count": len(search_results.get("sources", [])),
                },
                needs_review=validation.get("needs_review", False)
            )
            
            # 8. Cache successful responses
            if self.config.enable_l1_cache and result.confidence >= self.config.min_confidence_threshold:
                await self._cache_response(context, result)
            
            # 9. Record for feedback
            await self._record_interaction(context, result)
            
            return result
            
        except Exception as e:
            logger.error("Pipeline error", error=str(e), user_id=context.user_id)
            return PipelineResult(
                response="",
                confidence=0.0,
                confidence_breakdown={},
                error=str(e)
            )
    
    async def _check_cache(self, context: PipelineContext) -> Optional[PipelineResult]:
        """Check L1 cache for existing response"""
        cache_key = f"{context.user_id}:{context.query[:100]}"
        
        cached = self.response_cache.get(cache_key)
        if cached:
            logger.debug("Cache hit", user_id=context.user_id)
            return PipelineResult(
                response=cached.get("response", ""),
                confidence=cached.get("confidence", 0.5),
                confidence_breakdown=cached.get("breakdown", {}),
                cache_hit=True
            )
        
        return None
    
    async def _cache_response(self, context: PipelineContext, result: PipelineResult):
        """Cache successful response"""
        cache_key = f"{context.user_id}:{context.query[:100]}"
        
        self.response_cache.set(cache_key, {
            "response": result.response,
            "confidence": result.confidence,
            "breakdown": result.confidence_breakdown
        })
    
    async def _route_query(self, context: PipelineContext) -> List[str]:
        """Route query through agent graph"""
        try:
            path, info = self.router.route_with_feedback(
                query=context.query,
                query_type=context.query_type,
                user_context=context.user_context
            )
            return info.get("path", [])
        except Exception as e:
            logger.warning("Routing failed", error=str(e))
            return ["context", "draft", "qa"]
    
    async def _search(
        self,
        context: PipelineContext,
        policy_docs: List[Dict],
        knowledge_base: List[Dict]
    ) -> Dict:
        """Search policy and knowledge using BM25"""
        sources = []
        
        # Search policies
        if policy_docs and self.config.enable_bm25_search:
            # Index policies if not already
            if not hasattr(self, '_policies_indexed'):
                for i, doc in enumerate(policy_docs):
                    self.policy_search.add_document(
                        f"policy_{i}",
                        doc.get("text", str(doc)),
                        doc.get("title", ""),
                        doc.get("metadata", {})
                    )
                self._policies_indexed = True
            
            # Search
            policy_results = self.policy_search.search(context.query, self.config.search_top_k)
            for r in policy_results:
                sources.append({
                    "type": "policy",
                    "title": r.get("title", ""),
                    "content": r.get("text", "")[:200],
                    "score": r.get("score", 0)
                })
        
        # Search knowledge
        if knowledge_base and self.config.enable_bm25_search:
            if not hasattr(self, '_knowledge_indexed'):
                for i, doc in enumerate(knowledge_base):
                    self.knowledge_search.add_document(
                        f"kb_{i}",
                        doc.get("text", str(doc)),
                        doc.get("title", ""),
                        doc.get("metadata", {})
                    )
                self._knowledge_indexed = True
            
            kb_results = self.knowledge_search.search(context.query, self.config.search_top_k)
            for r in kb_results:
                sources.append({
                    "type": "knowledge",
                    "title": r.get("title", ""),
                    "content": r.get("text", "")[:200],
                    "score": r.get("score", 0)
                })
        
        return {"sources": sources}
    
    async def _build_llm_context(
        self,
        context: PipelineContext,
        search_results: Dict
    ) -> Dict:
        """Build context for LLM call"""
        context_parts = []
        
        # Add user context if available
        if context.user_context:
            user_info = context.user_context.get("user_info", {})
            if user_info.get("name"):
                context_parts.append(f"Khách hàng: {user_info['name']}")
        
        # Add search results
        if search_results.get("sources"):
            context_parts.append("Thông tin tham khảo:")
            for src in search_results["sources"][:3]:
                context_parts.append(f"- {src.get('title', 'N/A')}: {src.get('content', '')[:100]}")
        
        return {
            "search_sources": search_results.get("sources", []),
            "context_string": "\n".join(context_parts)
        }
    
    async def _generate_single(
        self,
        context: PipelineContext,
        llm_context: Dict,
        llm_callable: Callable
    ) -> Dict:
        """Generate response from single model"""
        # Build prompt
        prompt = f"""
{llm_context.get('context_string', '')}

Câu hỏi: {context.query}

Trả lời dựa trên thông tin được cung cấp.
"""
        
        try:
            response = await llm_callable(
                prompt=prompt,
                context=llm_context
            )
            
            return {
                "content": response.get("content", ""),
                "confidence": response.get("confidence", 0.7),
                "model": response.get("model", "unknown")
            }
        except Exception as e:
            logger.error("LLM call failed", error=str(e))
            return {"content": "", "confidence": 0.0}
    
    async def _generate_ensemble(
        self,
        context: PipelineContext,
        llm_context: Dict
    ) -> Dict:
        """Generate response using ensemble"""
        # Note: This requires actual LLM integration
        # For now, return placeholder
        return await self._generate_single(context, llm_context, None)
    
    async def _validate_response(
        self,
        context: PipelineContext,
        response: Dict,
        search_results: Dict
    ) -> Dict:
        """Validate response and calculate confidence"""
        try:
            validation = await self.validator.validate_response(
                response=response.get("content", ""),
                query=context.query,
                context=context.user_context,
                policy={"relevant_policies": [s for s in search_results.get("sources", []) if s.get("type") == "policy"]},
                knowledge={"knowledge_results": [s for s in search_results.get("sources", []) if s.get("type") == "knowledge"]}
            )
            return validation
        except Exception as e:
            logger.warning("Validation failed", error=str(e))
            return {"confidence": 0.5, "needs_review": False, "factor_scores": {}}
    
    async def _record_interaction(self, context: PipelineContext, result: PipelineResult):
        """Record interaction for learning"""
        # This would update the router's feedback
        if hasattr(self, 'router') and self.router:
            try:
                self.router.record_feedback(
                    query=context.query,
                    path=result.agents_used,
                    user_satisfied=result.confidence >= 0.7
                )
            except Exception:
                pass
    
    def get_stats(self) -> Dict:
        """Get pipeline statistics"""
        stats = {
            "cache_enabled": self.config.enable_l1_cache,
            "bm25_enabled": self.config.enable_bm25_search,
            "routing_enabled": self.config.enable_agent_routing,
            "ensemble_enabled": self.config.enable_ensemble,
        }
        
        if self.config.enable_l1_cache:
            stats["cache"] = {
                "policy_cache": self.policy_cache.get_stats(),
                "knowledge_cache": self.knowledge_cache.get_stats(),
                "response_cache": self.response_cache.get_stats()
            }
        
        if hasattr(self.router, 'get_routing_stats'):
            stats["routing"] = self.router.get_routing_stats()
        
        return stats


# Factory function
def create_pipeline(config: Optional[Dict] = None) -> SupervisorPipeline:
    """Create a configured supervisor pipeline"""
    pipeline_config = PipelineConfig()
    
    if config:
        for key, value in config.items():
            if hasattr(pipeline_config, key):
                setattr(pipeline_config, key, value)
    
    return SupervisorPipeline(pipeline_config)


# Example usage
if __name__ == "__main__":
    print("=== Supervisor Pipeline Demo ===\n")
    
    # Create pipeline
    pipeline = SupervisorPipeline(PipelineConfig(
        enable_l1_cache=True,
        enable_bm25_search=True,
        enable_agent_routing=True,
        enable_ensemble=False
    ))
    
    # Add sample knowledge
    pipeline._init_search()
    
    # Index some policies
    policies = [
        {"title": "Chính sách nghỉ phép", "text": "Nhân viên được nghỉ 12 ngày phép/năm"},
        {"title": "Giờ làm việc", "text": "Giờ làm việc từ 8h-17h30 thứ 2-6"},
        {"title": "Bảo hiểm", "text": "Công ty đóng BHXH 17%, nhân viên đóng 8%"},
    ]
    
    for i, doc in enumerate(policies):
        pipeline.policy_search.add_document(f"policy_{i}", doc["text"], doc["title"])
    
    # Search test
    print("=== BM25 Search Test ===")
    results = pipeline.policy_search.search("nghỉ phép", top_k=2)
    for r in results:
        print(f"  [{r['score']:.3f}] {r['title']}")
    
    # Stats
    print("\n=== Pipeline Stats ===")
    stats = pipeline.get_stats()
    print(f"Enabled features: {[k for k, v in stats.items() if v]}")
    
    if "cache" in stats:
        print(f"Response cache hit rate: {stats['cache']['response_cache']['hit_rate']:.1%}")
