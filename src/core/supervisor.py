"""
Supervisor - Main orchestration logic
Simplified with SimpleAgent (Steve Jobs philosophy)
"""

from src.core import (
    InputPayload,
    OutputPayload,
    IntentClassification,
    RiskEvaluation,
    IntentType,
    RiskLevel,
)
from src.memory import MemoryContext as MemoryContextModel
from src.agents import ContextAgent, PolicyAgent, KnowledgeAgent, DraftAgent, QAAgent, SimpleAgent
from src.db import AuditLog, async_session
from src.llm import MultiProviderLLMClient, LLMResponse
from src.config import get_settings
from typing import Optional, Dict
import time
import structlog

logger = structlog.get_logger()


# Import NEW modules (v2 enhancements)
try:
    from src.knowledge.bm25_search import HybridSearch
    from src.core.bayesian_confidence import BayesianConfidence, ResponseValidator, ConfidenceFactors
    from src.memory.lru_cache import LRUCache, PolicyCache, KnowledgeCache
    from src.agents.router import AdaptiveRouter
    NEW_MODULES_AVAILABLE = True
except ImportError as e:
    NEW_MODULES_AVAILABLE = False
    logger.warning("New modules not available", error=str(e))


class DecisionEngine:
    """Enhanced decision engine with agent routing"""
    
    def __init__(self, router=None):
        self.router = router or AdaptiveRouter()
    
    def _is_low_risk_faq(
        self,
        intent: IntentClassification,
        risk: RiskEvaluation,
    ) -> bool:
        return intent.intent == IntentType.FAQ and risk.risk_level == RiskLevel.LOW

    def should_use_subagents(
        self,
        intent: IntentClassification,
        risk: RiskEvaluation,
        payload: InputPayload,
    ) -> bool:
        if self._is_low_risk_faq(intent, risk):
            return False

        if intent.intent in [IntentType.POLICY, IntentType.SUPPORT_CASE, IntentType.ANALYSIS]:
            return True

        if risk.risk_level in [RiskLevel.MEDIUM, RiskLevel.HIGH]:
            return True

        words = payload.message.text.split()
        if len(words) > 50:
            return True

        if intent.confidence < 0.7:
            return True

        return False

    def response_route(self, confidence: float, kb_hit: bool = False) -> str:
        """Classify how a response should be delivered.

        Returns one of:
        - "skip": do not send the response
        - "approve": keep the response pending human approval
        - "send": send immediately

        High-confidence responses only auto-send when they are backed by KB.
        """
        if confidence < 0.5:
            return "skip"

        if confidence >= 0.9 and kb_hit:
            return "send"

        return "approve"

    def needs_human_review(
        self,
        intent: IntentClassification,
        risk: RiskEvaluation,
        payload: InputPayload,
        confidence: float,
    ) -> bool:
        if self._is_low_risk_faq(intent, risk):
            return False

        if intent.intent == IntentType.EXECUTIVE_REQUEST:
            return True

        if risk.risk_level == RiskLevel.HIGH:
            return True

        if confidence < 0.7:
            return True

        text_lower = payload.message.text.lower()
        commitment_keywords = ["cam kết", "đảm bảo", "chắc chắn", "sẽ làm", "hứa", "commit"]
        if any(kw in text_lower for kw in commitment_keywords):
            return True

        return False
    
    def get_agent_path(self, query: str, query_type: str = "general") -> list:
        """NEW v2: Get optimal agent path using router"""
        try:
            if self.router:
                path, info = self.router.route_with_feedback(query, query_type)
                return path
        except Exception as e:
            logger.warning("Router failed", error=str(e))
        
        # Fallback to default path
        return ["context", "policy", "knowledge", "draft", "qa"]


class Supervisor:
    """
    Enhanced Supervisor with all v2 improvements:
    - LRU Cache for query caching
    - BM25 Search for knowledge retrieval
    - Bayesian Confidence for validation
    - Agent Router for optimal path selection
    - URL Fetcher for auto-detecting and fetching URLs
    """
    
    def __init__(self):
        self.decision_engine = DecisionEngine()
        self.context_agent = ContextAgent()
        self.policy_agent = PolicyAgent()
        self.knowledge_agent = KnowledgeAgent()
        self.draft_agent = DraftAgent()
        self.qa_agent = QAAgent()
        self.simple_agent = SimpleAgent()
        self._llm: Optional[MultiProviderLLMClient] = None
        
        # NEW v2: Initialize enhanced components (based on config)
        self._init_enhancements()
    
    def _init_enhancements(self):
        """Initialize v2 enhancement modules based on config"""
        settings = get_settings()
        
        if NEW_MODULES_AVAILABLE:
            try:
                # LRU Cache (query + response) - only if enabled
                if settings.enable_lru_cache:
                    self.query_cache = LRUCache(maxsize=300, ttl_seconds=600)
                    self.policy_cache = PolicyCache(maxsize=200)
                    self.knowledge_cache = KnowledgeCache(maxsize=500)
                
                # BM25 Search - only if enabled
                if settings.enable_bm25_search:
                    self.policy_search = HybridSearch(bm25_weight=0.7, tfidf_weight=0.3)
                    self.knowledge_search = HybridSearch(bm25_weight=0.6, tfidf_weight=0.4)
                
                # Bayesian Confidence - only if enabled
                if settings.enable_bayesian_confidence:
                    self.bayesian_confidence = BayesianConfidence()
                    self.response_validator = ResponseValidator()
                
                # Agent Router - only if enabled
                if settings.enable_agent_router:
                    self.agent_router = AdaptiveRouter()
                
                # URL Fetcher - only if enabled
                if settings.enable_url_fetcher:
                    from src.tools.url_fetcher import URLFetcher
                    self.url_fetcher = URLFetcher(
                        timeout=10,
                        max_urls=5
                    )
                
                # n8n Connector - only if enabled
                if settings.enable_tools and settings.n8n_base_url:
                    from src.tools import get_n8n_connector
                    self.n8n_connector = get_n8n_connector()
                
                # Extended Tools - Disabled by default (for future use)
                # RAG Pipeline - Hybrid search for knowledge base
                if settings.enable_rag_pipeline:
                    from src.tools.rag_pipeline import get_rag_pipeline
                    self.rag_pipeline = get_rag_pipeline()
                
                # File Processor - Process PDF/Excel/CSV attachments
                if settings.enable_file_processor:
                    from src.tools.file_processor import get_file_processor
                    self.file_processor = get_file_processor()
                
                # Scheduler - Cron jobs for automation
                if settings.enable_scheduler:
                    from src.tools.scheduler import get_scheduler
                    self.scheduler = get_scheduler()
                
                # Notification - Multi-channel notifications (auto-enabled if any config set)
                notification_configured = (
                    settings.notification_email_enabled or 
                    settings.notification_sms_enabled or 
                    settings.notification_teams_enabled or
                    settings.notification_webhook_url
                )
                if settings.enable_notification or notification_configured:
                    from src.tools.notification import NotificationSender, ChannelConfig
                    config = ChannelConfig(
                        smtp_host=settings.smtp_host or "",
                        smtp_port=settings.smtp_port or 587,
                        smtp_user=settings.smtp_user or "",
                        smtp_password=settings.smtp_password or "",
                        from_email=settings.from_email or "",
                        teams_webhook_url=settings.teams_webhook_url or "",
                        webhook_url=settings.notification_webhook_url or "",
                    )
                    self.notification_sender = NotificationSender(config=config)
                
                # API Client - External API integrations
                if settings.enable_api_client:
                    from src.tools.api_client import create_api_client
                    self.api_client = create_api_client()
                
                # Audit Logger - Compliance audit logging
                if settings.enable_audit_logger:
                    from src.tools.audit_logger import get_audit_logger
                    self.audit_logger = get_audit_logger()
                
                # Validators - Input validation
                if settings.enable_validators:
                    from src.tools.validators import SchemaValidator
                    self.validators = SchemaValidator()
                
                logger.info("Supervisor v2 enhancements initialized",
                          cache=settings.enable_lru_cache, 
                          bm25=settings.enable_bm25_search, 
                          bayesian=settings.enable_bayesian_confidence, 
                          routing=settings.enable_agent_router, 
                          url_fetcher=settings.enable_url_fetcher,
                          tools=settings.enable_tools,
                          # Extended tools (disabled by default)
                          rag_pipeline=settings.enable_rag_pipeline,
                          file_processor=settings.enable_file_processor,
                          scheduler=settings.enable_scheduler,
                          notification=settings.enable_notification,
                          api_client=settings.enable_api_client,
                          audit_logger=settings.enable_audit_logger,
                          validators=settings.enable_validators)
            except Exception as e:
                logger.error("Failed to initialize enhancements", error=str(e))
        else:
            logger.warning("Running in legacy mode (no v2 enhancements)")
    
    def set_llm(self, llm: MultiProviderLLMClient):
        self._llm = llm

    async def simple_process(self, payload: InputPayload, memory: MemoryContextModel) -> OutputPayload:
        """
        SIMPLIFIED process - Steve Jobs style.
        1. Check cache
        2. Ask SimpleAgent
        3. Done
        """
        start_time = time.time()
        cache_key = f"{payload.user.id}:{payload.message.text[:100]}"

        if hasattr(self, 'query_cache'):
            cached = self.query_cache.get(cache_key)
            if cached:
                return self._create_output(
                    payload=payload,
                    answer=cached["response"],
                    confidence=cached.get("confidence", 0.9),
                    intent=IntentClassification(intent=IntentType.FAQ, confidence=0.9),
                    risk=RiskEvaluation(risk_level=RiskLevel.LOW, reasons=[]),
                    agents_used=["cache"],
                    status="completed",
                    processing_time=start_time,
                )

        answer, confidence = await self.simple_agent.answer(payload, memory, self._llm)

        if hasattr(self, 'query_cache') and confidence >= 0.6:
            self.query_cache.set(cache_key, {
                "response": answer,
                "confidence": confidence,
                "timestamp": time.time()
            })

        return self._create_output(
            payload=payload,
            answer=answer,
            confidence=confidence,
            intent=IntentClassification(intent=IntentType.FAQ, confidence=0.8),
            risk=RiskEvaluation(risk_level=RiskLevel.LOW, reasons=[]),
            agents_used=["simple_agent"],
            status="completed",
            processing_time=start_time,
        )

    async def process(self, payload: InputPayload, memory: MemoryContextModel) -> OutputPayload:
        start_time = time.time()
        decision = "direct"
        final_confidence = 0.85
        kb_hit = False
        # NEW v2: Check cache first
        if NEW_MODULES_AVAILABLE:
            cache_result = self._check_cache(payload)
            if cache_result:
                final_confidence = cache_result.get("confidence", 0.9)
                logger.debug("Cache hit", request_id=payload.request_id)
                return self._create_output(
                    payload=payload,
                    answer=cache_result["response"],
                    confidence=final_confidence,
                    intent=IntentClassification(intent=IntentType.FAQ, confidence=0.9),
                    risk=RiskEvaluation(risk_level=RiskLevel.LOW, reasons=[]),
                    agents_used=["cache"],
                    status="completed",
                    processing_time=start_time,
                )

        # NEW v2: Auto-fetch URLs from message
        url_context = await self._fetch_urls(payload)
        if url_context:
            logger.debug("URLs fetched for context", count=len(url_context))
        else:
            url_context = ""

        intent = self._classify_intent(payload, memory)
        risk = self._evaluate_risk(payload, memory)

        if self.decision_engine.should_use_subagents(intent, risk, payload):
            decision = "subagents"
            
            # Use agent router for optimized path (v2)
            agents_used = self._get_agents_from_path(intent, risk, payload, memory)
            
            # Context + Policy + Knowledge flow
            context = self.context_agent.build(payload, memory)
            policy = await self.policy_agent.extract(payload, memory, self._llm)
            knowledge = await self.knowledge_agent.retrieve(payload, memory, self._llm)

            if policy.get("guide_requested") and policy.get("guide_id"):
                answer = await self._handle_guide_request(payload, policy)
                final_confidence = 0.95
                agents_used.append("guide_delivery")
            elif knowledge.get("system_query_requested"):
                query_result = await self._handle_system_query(payload, memory, knowledge.get("query_type"))
                answer = self._format_system_query_response(query_result)
                final_confidence = query_result.get("confidence", 0.9)
                agents_used.append("system_query")
            else:
                # Inject URL context into context dict
                context_with_urls = dict(context)
                if url_context:
                    context_with_urls["url_context"] = url_context
                
                draft = await self.draft_agent.generate(payload, context_with_urls, policy, knowledge, self._llm)
                
                # Enhanced validation with Bayesian confidence (v2)
                validation = await self._enhanced_validate(draft, payload, context, policy, knowledge)
                
                if validation["needs_review"]:
                    if self.decision_engine.needs_human_review(intent, risk, payload, validation["confidence"]):
                        # Even if needs review, still refine the draft through QA agent
                        answer = self.qa_agent.refine(validation, payload, context)
                        return self._create_output(
                            payload=payload,
                            answer=answer,
                            confidence=validation["confidence"],
                            risk=risk,
                            intent=intent,
                            agents_used=agents_used,
                            status="needs_review",
                            processing_time=start_time,
                        )

                answer = self.qa_agent.refine(validation, payload, context)
                final_confidence = validation["confidence"]
                kb_hit = bool(knowledge.get("knowledge_results"))
        else:
            agents_used = ["draft"]
            answer, final_confidence = await self._generate_direct_answer(payload, memory)

        response_route = self.decision_engine.response_route(final_confidence, kb_hit=kb_hit)
        if response_route == "skip":
            decision = "skipped"
            answer = ""
            status = "skipped"
        elif response_route == "approve" or self.decision_engine.needs_human_review(intent, risk, payload, final_confidence):
            decision = "review"
            status = "needs_review"
        else:
            status = "completed"

        processing_time_ms = int((time.time() - start_time) * 1000)

        await self._log_audit(
            request_id=payload.request_id,
            decision=decision,
            risk_level=risk.risk_level.value,
            agents_used=agents_used,
            input_summary=payload.message.text[:200],
            output_summary=answer[:200],
            processing_time_ms=processing_time_ms,
        )
        
        # NEW v2: Cache successful responses
        if NEW_MODULES_AVAILABLE and status == "completed" and final_confidence >= 0.6:
            self._cache_response(payload, answer, final_confidence)

        return self._create_output(
            payload=payload,
            answer=answer,
            confidence=final_confidence,
            risk=risk,
            intent=intent,
            agents_used=agents_used,
            status=status,
            processing_time=start_time,
        )
    
    # ===== NEW v2 Methods =====
    
    def _check_cache(self, payload: InputPayload) -> Optional[Dict]:
        """Check LRU cache for cached response"""
        if not hasattr(self, 'query_cache'):
            return None
        
        cache_key = f"{payload.user.id}:{payload.message.text[:100]}"
        return self.query_cache.get(cache_key)
    
    def _cache_response(self, payload: InputPayload, response: str, confidence: float):
        """Cache response for future use"""
        if not hasattr(self, 'query_cache'):
            return
        
        cache_key = f"{payload.user.id}:{payload.message.text[:100]}"
        self.query_cache.set(cache_key, {
            "response": response,
            "confidence": confidence,
            "timestamp": time.time()
        })
    
    def _get_agents_from_path(
        self,
        intent: IntentClassification,
        risk: RiskEvaluation,
        payload: InputPayload,
        memory: MemoryContextModel
    ) -> list:
        """Get agents based on query type and router"""
        # Determine query type
        query_type = self._determine_query_type(intent)
        
        # Get optimized path from router
        try:
            path = self.decision_engine.get_agent_path(payload.message.text, query_type)
            # Convert AgentType to string list
            return [a.value if hasattr(a, 'value') else str(a) for a in path]
        except Exception as e:
            logger.warning("Agent path failed, using default", error=str(e))
            return ["context", "policy", "knowledge", "draft", "qa"]
    
    def _determine_query_type(self, intent: IntentClassification) -> str:
        """Map intent to query type for routing"""
        mapping = {
            IntentType.POLICY: "policy",
            IntentType.SUPPORT_CASE: "support",
            IntentType.SYSTEM_QUERY: "system_query",
            IntentType.GUIDE_REQUEST: "guide",
            IntentType.ANALYSIS: "analysis",
        }
        return mapping.get(intent.intent, "general")
    
    async def _fetch_urls(self, payload: InputPayload) -> str:
        """Fetch URLs from message and return context string"""
        if not NEW_MODULES_AVAILABLE or not hasattr(self, 'url_fetcher'):
            return ""
        
        try:
            urls_detected = self.url_fetcher.detect_urls(payload.message.text)
            if not urls_detected:
                return ""
            
            # Fetch all URLs concurrently
            url_infos = await self.url_fetcher.fetch_all(payload.message.text)
            
            # Build context string
            context = self.url_fetcher.build_context(url_infos)
            
            return context
        except Exception as e:
            logger.warning("URL fetch failed", error=str(e))
            return ""
    
    async def _enhanced_validate(
        self,
        draft: str,
        payload: InputPayload,
        context: Dict,
        policy: Dict,
        knowledge: Dict
    ) -> Dict:
        """Enhanced validation with Bayesian confidence"""
        if not NEW_MODULES_AVAILABLE:
            # Fallback to original validation
            return self._original_validate(draft, payload, context)
        
        try:
            # Extract confidence factors
            factors = ConfidenceFactors(
                context_relevance=min(1.0, len(context.get("user_info", {})) / 3),
                policy_match=1.0 if policy.get("relevant_policies") else 0.5,
                knowledge_freshness=0.7,
                user_satisfaction=0.7,
                agent_experience=0.75
            )
            
            # Calculate Bayesian confidence
            confidence, factor_scores = self.bayesian_confidence.calculate_confidence(factors, "llama3")
            
            # Also use ResponseValidator for issues detection
            validation = {
                "draft": draft,
                "confidence": confidence,
                "factor_scores": factor_scores,
                "needs_review": confidence < 0.7
            }
            
            logger.debug("Bayesian validation",
                      confidence=confidence,
                      factors=list(factor_scores.keys()))
            
            return validation
        except Exception as e:
            logger.warning("Bayesian validation failed, using original", error=str(e))
            return self._original_validate(draft, payload, context)
    
    def _original_validate(self, draft: str, payload: InputPayload, context: Dict) -> Dict:
        """Original QA validation as fallback"""
        needs_review = len(draft) < 50 or len(draft) > 2000
        confidence = 0.7 if not needs_review else 0.5
        
        return {
            "draft": draft,
            "confidence": confidence,
            "needs_review": needs_review
        }
    
    def _search_knowledge_bm25(self, query: str, kb_type: str = "knowledge") -> list:
        """BM25 search for knowledge/policy"""
        if not NEW_MODULES_AVAILABLE or not hasattr(self, 'knowledge_search'):
            return []
        
        search = self.knowledge_search if kb_type == "knowledge" else self.policy_search
        
        try:
            results = search.search(query, top_k=5)
            return [
                {
                    "title": r.get("title", ""),
                    "text": r.get("text", ""),
                    "score": r.get("score", 0)
                }
                for r in results
            ]
        except Exception as e:
            logger.warning("BM25 search failed", error=str(e))
            return []
    
    def get_stats(self) -> Dict:
        """Get supervisor statistics including v2 enhancements"""
        stats = {
            "version": "v2" if NEW_MODULES_AVAILABLE else "legacy",
            "new_modules": NEW_MODULES_AVAILABLE
        }
        
        if NEW_MODULES_AVAILABLE:
            if hasattr(self, 'query_cache'):
                cache_stats = self.query_cache.get_stats()
                stats["cache"] = {
                    "size": cache_stats.get("size", 0),
                    "hit_rate": cache_stats.get("hit_rate", 0),
                    "hits": cache_stats.get("hit_count", 0),
                    "misses": cache_stats.get("miss_count", 0)
                }
            
            if hasattr(self, 'decision_engine') and self.decision_engine.router:
                try:
                    stats["routing"] = self.decision_engine.router.get_routing_stats()
                except Exception:
                    pass
        
        return stats
    
    # ===== Original Methods =====
    
    def _classify_intent(self, payload: InputPayload, memory: MemoryContextModel) -> IntentClassification:
        from src.core.intent_classifier import IntentClassifier
        classifier = IntentClassifier()
        return classifier.classify(payload, memory)

    def _evaluate_risk(self, payload: InputPayload, memory: MemoryContextModel) -> RiskEvaluation:
        from src.core.risk_evaluator import RiskEvaluator
        evaluator = RiskEvaluator()
        return evaluator.evaluate(payload, memory)

    async def _generate_direct_answer(self, payload: InputPayload, memory: MemoryContextModel) -> tuple[str, float]:
        user_name = payload.user.display_name
        message = payload.message.text

        user_profile = memory.user_profile or {}
        preferences = user_profile.get("preferences", {}) if isinstance(user_profile, dict) else {}
        style_profile = preferences.get("style_profile", {}) if isinstance(preferences, dict) else {}
        response_persona_hint = (
            preferences.get("response_persona_hint")
            or (style_profile.get("response_persona_hint") if isinstance(style_profile, dict) else None)
            or user_profile.get("response_persona_hint")
        )
        communication_style = user_profile.get("communication_style") or "balanced"
        persona_lines = []
        if communication_style:
            persona_lines.append(f"Phong cách người dùng: {communication_style}")
        if response_persona_hint:
            persona_lines.append(f"Persona học được: {response_persona_hint}")
        persona_block = "\n".join(persona_lines)

        if self._llm:
            system_prompt = "Bạn là một trợ lý AI hữu ích. Trả lời ngắn gọn, chính xác bằng tiếng Việt."
            if persona_block:
                system_prompt = f"{system_prompt}\n{persona_block}"
            response: LLMResponse = await self._llm.complete(
                system_prompt=system_prompt,
                user_message=f"Người dùng {user_name} hỏi: {message}",
                context=memory.to_dict(),
            )
            return response.content, response.confidence

        return (
            f"Xin chào {user_name}, về câu hỏi của bạn \"{message[:100]}...\", tôi có thể giúp bạn. Bạn cần thêm thông tin gì không?",
            0.6,
        )

    def _create_output(
        self,
        payload: InputPayload,
        answer: str,
        confidence: float,
        risk: RiskEvaluation,
        intent: IntentClassification,
        agents_used: list,
        status: str,
        processing_time: float,
    ) -> OutputPayload:
        from src.core.schemas import MessageInfo
        
        output = OutputPayload(
            answer=answer,
            message=MessageInfo(
                text=answer,
                timestamp=time.time(),
            ),
            confidence=confidence,
            intent=intent,
            risk=risk,
            agents_used=agents_used,
            status=status,
            processing_time_ms=int(processing_time * 1000),
        )
        
        output.request_id = payload.request_id
        
        return output

    async def _handle_guide_request(self, payload: InputPayload, policy: Dict) -> str:
        return "Guide delivery logic here"

    async def _handle_system_query(
        self,
        payload: InputPayload,
        memory: MemoryContextModel,
        query_type: str,
    ) -> Dict:
        return {"result": "system query result", "confidence": 0.9}

    def _format_system_query_response(self, query_result: Dict) -> str:
        return f"System query result: {query_result.get('result', 'N/A')}"

    async def _log_audit(
        self,
        request_id: str,
        decision: str,
        risk_level: str,
        agents_used: list,
        input_summary: str,
        output_summary: str,
        processing_time_ms: int,
    ):
        try:
            async with async_session() as session:
                log = AuditLog(
                    request_id=request_id,
                    decision=decision,
                    risk_level=risk_level,
                    agents_used=",".join(agents_used),
                    input_summary=input_summary,
                    output_summary=output_summary,
                    processing_time_ms=processing_time_ms,
                )
                session.add(log)
                await session.commit()
        except Exception as e:
            logger.warning("Failed to log audit", error=str(e))
