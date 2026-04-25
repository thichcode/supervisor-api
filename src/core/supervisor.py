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
from src.core.reasoning_loop import ReasoningLoopOrchestrator
from src.core.metrics import metrics
from typing import Optional, Dict, Any
from hashlib import sha256
import re
import time
import structlog

logger = structlog.get_logger()


# Import NEW modules (v2 enhancements)
try:
    from src.knowledge.bm25_search import HybridSearch
    from src.core.bayesian_confidence import (
        BayesianConfidence,
        ResponseValidator,
        ConfidenceFactors,
    )
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
        self.reasoning_orchestrator = ReasoningLoopOrchestrator(self)
        self._llm: Optional[MultiProviderLLMClient] = None
        self._image_llm: Optional[MultiProviderLLMClient] = None  # Separate LLM for image processing

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

                    self.url_fetcher = URLFetcher(timeout=10, max_urls=5)

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
                    settings.notification_email_enabled
                    or settings.notification_sms_enabled
                    or settings.notification_teams_enabled
                    or settings.notification_webhook_url
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

                logger.info(
                    "Supervisor v2 enhancements initialized",
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
                    validators=settings.enable_validators,
                )
            except Exception as e:
                logger.error("Failed to initialize enhancements", error=str(e))
        else:
            logger.warning("Running in legacy mode (no v2 enhancements)")

    def set_llm(self, llm: MultiProviderLLMClient):
        self._llm = llm

    def set_image_llm(self, llm: MultiProviderLLMClient):
        """Set separate LLM for image processing tasks"""
        self._image_llm = llm

    def _get_image_llm(self) -> Optional[MultiProviderLLMClient]:
        """Get image LLM if available, fallback to main LLM"""
        return self._image_llm or self._llm

    async def simple_process(
        self, payload: InputPayload, memory: MemoryContextModel
    ) -> OutputPayload:
        """
        SIMPLIFIED process - Steve Jobs style.
        1. Check cache
        2. Ask SimpleAgent
        3. Done
        """
        start_time = time.time()
        cache_key = f"{payload.user.id}:{payload.message.text[:100]}"

        if hasattr(self, "query_cache"):
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

        if hasattr(self, "query_cache") and confidence >= 0.6:
            self.query_cache.set(
                cache_key, {"response": answer, "confidence": confidence, "timestamp": time.time()}
            )

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
        settings = get_settings()
        start_time = time.time()
        decision = "direct"
        final_confidence = 0.8
        kb_hit = False
        kb_sources = []
        qa_needs_review = False
        pattern_hit = False
        # NEW v2: Check cache first
        if NEW_MODULES_AVAILABLE:
            cache_result = self._check_cache(payload)
            if cache_result:
                cached_answer = cache_result.get("response", "")
                final_confidence = self._normalize_final_confidence(
                    cache_result.get("confidence", 0.8),
                    kb_hit=False,
                    qa_needs_review=False,
                )
                logger.debug("Cache hit", request_id=payload.request_id)
                response_route = self.decision_engine.response_route(final_confidence, kb_hit=False)
                if response_route == "skip":
                    return self._create_output(
                        payload=payload,
                        answer="",
                        confidence=final_confidence,
                        intent=IntentClassification(intent=IntentType.FAQ, confidence=0.8),
                        risk=RiskEvaluation(risk_level=RiskLevel.LOW, reasons=[]),
                        agents_used=["cache"],
                        status="skipped",
                        processing_time=start_time,
                        extra_metadata={"cache_hit": True, "kb_hit": False, "agents_used": ["cache"]},
                    )
                if response_route == "approve":
                    return self._create_output(
                        payload=payload,
                        answer=cached_answer,
                        confidence=final_confidence,
                        intent=IntentClassification(intent=IntentType.FAQ, confidence=0.8),
                        risk=RiskEvaluation(risk_level=RiskLevel.LOW, reasons=[]),
                        agents_used=["cache"],
                        status="needs_review",
                        processing_time=start_time,
                        extra_metadata={"cache_hit": True, "kb_hit": False, "agents_used": ["cache"]},
                    )
                return self._create_output(
                    payload=payload,
                    answer=cached_answer,
                    confidence=final_confidence,
                    intent=IntentClassification(intent=IntentType.FAQ, confidence=0.8),
                    risk=RiskEvaluation(risk_level=RiskLevel.LOW, reasons=[]),
                    agents_used=["cache"],
                    status="completed",
                    processing_time=start_time,
                    extra_metadata={"cache_hit": True, "kb_hit": False},
                )

        # NEW v2: Auto-fetch URLs from message
        url_context = await self._fetch_urls(payload)
        if url_context:
            logger.debug("URLs fetched for context", count=len(url_context))
        else:
            url_context = ""

        image_case_context = self._build_image_case_context(payload)
        if image_case_context.get("needs_clarification"):
            return self._create_output(
                payload=payload,
                answer=self._build_image_case_clarification(image_case_context),
                confidence=0.45,
                intent=IntentClassification(intent=IntentType.SUPPORT_CASE, confidence=0.6),
                risk=RiskEvaluation(risk_level=RiskLevel.LOW, reasons=[]),
                agents_used=["image_case_clarification"],
                status="needs_clarification",
                processing_time=start_time,
                extra_metadata={
                    "image_case": True,
                    "has_image_attachments": True,
                    "image_case_context": image_case_context,
                    "kb_hit": False,
                    "kb_sources": [],
                    "draft_reply_hint": image_case_context.get("draft_reply_hint", ""),
                    "internal_note": image_case_context.get("internal_note", ""),
                    "image_case_signature": image_case_context.get("issue_signature", ""),
                },
            )

        # Check for ITC ticket request pattern early
        text_lower = payload.message.text.lower()
        if "itc" in text_lower and ("support request" in text_lower or "ticket" in text_lower or "woid" in text_lower):
            itc_result = await self._handle_itc_ticket_request(payload)
            itc_answer = itc_result.get("answer")
            itc_confidence = itc_result.get("confidence", 0.85)
            itc_ticket_id = itc_result.get("ticket_id")
            
            # Build extra_metadata with ticket_id if available
            itc_metadata = {"itc_ticket": True}
            if itc_ticket_id:
                itc_metadata["itc_requestid"] = itc_ticket_id
            
            return self._create_output(
                payload=payload,
                answer=itc_answer,
                confidence=itc_confidence,
                intent=IntentClassification(intent=IntentType.SUPPORT_CASE, confidence=0.9),
                risk=RiskEvaluation(risk_level=RiskLevel.LOW, reasons=[]),
                agents_used=["itc_ticket"],
                status="completed",
                processing_time=start_time,
                extra_metadata=itc_metadata
            )

        should_run_reasoning_loop, rollout_metadata = self._should_run_reasoning_loop(payload, settings)
        if should_run_reasoning_loop:
            result = await self.reasoning_orchestrator.run(
                payload,
                memory,
                start_time=start_time,
                url_context=url_context,
            )
            result.metadata = {
                **(result.metadata or {}),
                "reasoning_loop_rollout": rollout_metadata,
                "image_case": bool(image_case_context.get("image_case")),
                "has_image_attachments": bool(image_case_context.get("has_image_attachments")),
                "image_case_context": image_case_context,
                "image_case_signature": image_case_context.get("issue_signature", ""),
                "draft_reply_hint": image_case_context.get("draft_reply_hint", ""),
                "internal_note": image_case_context.get("internal_note", ""),
            }
            metrics.record_reasoning_loop_outcome(result.status)
            metrics.record_reasoning_loop_latency(time.time() - start_time)

            reasoning_trace = (result.metadata or {}).get("reasoning_trace", {})
            if reasoning_trace.get("budget_exhausted"):
                metrics.record_reasoning_loop_fallback("budget_exhausted")
            if (result.metadata or {}).get("tool_failed"):
                metrics.record_reasoning_loop_fallback("tool_failed")
            if result.status == "needs_review":
                metrics.record_reasoning_loop_fallback("needs_review")
            return result
        if settings.enable_reasoning_loop:
            metrics.record_reasoning_loop_fallback("rollout_disabled")

        intent = self._classify_intent(payload, memory)
        risk = self._evaluate_risk(payload, memory)

        if self.decision_engine.should_use_subagents(intent, risk, payload):
            decision = "subagents"

            # Use agent router for optimized path (v2)
            agents_used = self._get_agents_from_path(intent, risk, payload, memory)

            # Context + Policy + Knowledge flow
            context = self.context_agent.build(payload, memory)
            context["image_case_context"] = image_case_context
            policy = await self.policy_agent.extract(payload, memory, self._llm)
            knowledge = await self.knowledge_agent.retrieve(payload, memory, self._llm)
            kb_sources = knowledge.get("knowledge_results", [])

            if knowledge.get("knowledge_clarification_needed") and kb_sources:
                clarification_question = knowledge.get(
                    "knowledge_clarification_question"
                ) or self._build_kb_clarification_question(
                    kb_sources[0], knowledge.get("knowledge_missing_fields", [])
                )
                return self._create_output(
                    payload=payload,
                    answer=clarification_question,
                    confidence=knowledge.get("confidence", 0.6),
                    intent=intent,
                    risk=risk,
                    agents_used=agents_used + ["kb_clarification"],
                    status="needs_clarification",
                    processing_time=start_time,
                    extra_metadata={
                        "kb_hit": True,
                        "kb_clarification_needed": True,
                        "kb_missing_fields": knowledge.get("knowledge_missing_fields", []),
                        "kb_required_fields": knowledge.get("knowledge_required_fields", []),
                        "kb_sources": kb_sources,
                    },
                )

            if policy.get("guide_requested") and policy.get("guide_id"):
                answer = await self._handle_guide_request(payload, policy)
                final_confidence = 0.95
                agents_used.append("guide_delivery")
                kb_hit = True
            elif knowledge.get("system_query_requested"):
                query_result = await self._handle_system_query(
                    payload, memory, knowledge.get("query_type")
                )
                answer = self._format_system_query_response(query_result)
                final_confidence = query_result.get("confidence", 0.9)
                agents_used.append("system_query")
                kb_hit = True
            else:
                pattern_result = await self._check_patterns(payload)
                if pattern_result:
                    answer, similarity = pattern_result
                    final_confidence = min(1.0, similarity + 0.05)
                    kb_hit = True
                    pattern_hit = True
                    agents_used.append("pattern_match")
                else:
                    # Inject URL context into context dict
                    context_with_urls = dict(context)
                    if url_context:
                        context_with_urls["url_context"] = url_context

                    draft = await self.draft_agent.generate(
                        payload, context_with_urls, policy, knowledge, self._llm
                    )

                    # Enhanced validation with Bayesian confidence (v2)
                    validation = await self._enhanced_validate(
                        draft, payload, context, policy, knowledge
                    )

                    if validation["needs_review"]:
                        logger.debug(
                            "Validation suggests review, but routing will be decided by confidence thresholds",
                            confidence=validation["confidence"],
                            issues=validation.get("issues", []),
                        )

                    answer = self.qa_agent.refine(validation, payload, context)
                    final_confidence = validation["confidence"]
                    kb_hit = bool(knowledge.get("knowledge_results"))
                    qa_needs_review = bool(validation.get("needs_review"))
        else:
            # Check patterns first (SimpleAgent logic)
            pattern_result = await self._check_patterns(payload)
            if pattern_result:
                answer, similarity = pattern_result
                final_confidence = min(1.0, similarity + 0.05)
                kb_hit = True
                agents_used = ["pattern_match"]
            else:
                agents_used = ["draft"]
                answer, final_confidence = await self._generate_direct_answer(
                    payload,
                    memory,
                    support_context={"image_case_context": image_case_context},
                )

        final_confidence = self._normalize_final_confidence(
            final_confidence,
            kb_hit=kb_hit,
            qa_needs_review=qa_needs_review,
        )

        response_route = self.decision_engine.response_route(final_confidence, kb_hit=kb_hit)
        if response_route == "skip":
            decision = "skipped"
            answer = ""
            status = "skipped"
        elif response_route == "approve":
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

        extra_metadata = {
            "kb_hit": kb_hit,
            "kb_sources": kb_sources,
            "pattern_hit": pattern_hit,
            "agents_used": agents_used,
            "image_case": bool(image_case_context.get("image_case")),
            "has_image_attachments": bool(image_case_context.get("has_image_attachments")),
            "image_case_context": image_case_context,
            "image_case_signature": image_case_context.get("issue_signature", ""),
            "draft_reply_hint": image_case_context.get("draft_reply_hint", ""),
            "internal_note": image_case_context.get("internal_note", ""),
        }
        if settings.enable_reasoning_loop:
            extra_metadata["reasoning_loop_rollout"] = rollout_metadata
        knowledge_template = locals().get("knowledge", {}).get("knowledge_template") if "knowledge" in locals() else None
        if knowledge_template:
            extra_metadata["kb_template"] = knowledge_template
        if kb_sources:
            extra_metadata["kb_evidence"] = [
                {
                    "id": item.get("id"),
                    "type": item.get("type"),
                    "title": item.get("title"),
                    "category": item.get("category"),
                    "similarity": item.get("similarity"),
                    "content": item.get("content", "")[:200],
                }
                for item in kb_sources[:3]
            ]

        return self._create_output(
            payload=payload,
            answer=answer,
            confidence=final_confidence,
            risk=risk,
            intent=intent,
            agents_used=agents_used,
            status=status,
            processing_time=start_time,
            extra_metadata=extra_metadata,
        )

    # ===== NEW v2 Methods =====

    def _check_cache(self, payload: InputPayload) -> Optional[Dict]:
        """Check LRU cache for cached response"""
        if not hasattr(self, "query_cache"):
            return None

        cache_key = f"{payload.user.id}:{payload.message.text[:100]}"
        return self.query_cache.get(cache_key)

    def _cache_response(self, payload: InputPayload, response: str, confidence: float):
        """Cache response for future use"""
        if not hasattr(self, "query_cache"):
            return

        cache_key = f"{payload.user.id}:{payload.message.text[:100]}"
        self.query_cache.set(
            cache_key, {"response": response, "confidence": confidence, "timestamp": time.time()}
        )

    def _get_agents_from_path(
        self,
        intent: IntentClassification,
        risk: RiskEvaluation,
        payload: InputPayload,
        memory: MemoryContextModel,
    ) -> list:
        """Get agents based on query type and router"""
        # Determine query type
        query_type = self._determine_query_type(intent)

        # Get optimized path from router
        try:
            path = self.decision_engine.get_agent_path(payload.message.text, query_type)
            # Convert AgentType to string list
            return [a.value if hasattr(a, "value") else str(a) for a in path]
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
        if not NEW_MODULES_AVAILABLE or not hasattr(self, "url_fetcher"):
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
        self, draft: str, payload: InputPayload, context: Dict, policy: Dict, knowledge: Dict
    ) -> Dict:
        """Enhanced validation with Bayesian confidence"""
        if not NEW_MODULES_AVAILABLE:
            # Fallback to original validation
            return self._original_validate(draft, payload, context)

        try:
            # Extract confidence factors
            factors = ConfidenceFactors(
                context_relevance=min(1.0, len(context.get("user_info", {})) / 3),
                policy_match=1.0 if policy.get("relevant_policies") else 0.45,
                knowledge_freshness=0.45,
                user_satisfaction=0.45,
                agent_experience=0.5,
            )

            # Calculate Bayesian confidence
            confidence, factor_scores = self.bayesian_confidence.calculate_confidence(
                factors, "llama3"
            )

            # Also use ResponseValidator for issues detection
            validation = {
                "draft": draft,
                "confidence": confidence,
                "factor_scores": factor_scores,
                "needs_review": confidence < 0.7,
            }

            logger.debug(
                "Bayesian validation", confidence=confidence, factors=list(factor_scores.keys())
            )

            return validation
        except Exception as e:
            logger.warning("Bayesian validation failed, using original", error=str(e))
            return self._original_validate(draft, payload, context)

    def _original_validate(self, draft: str, payload: InputPayload, context: Dict) -> Dict:
        """Original QA validation as fallback"""
        needs_review = len(draft) < 50 or len(draft) > 2000
        confidence = 0.45 if not needs_review else 0.4

        return {"draft": draft, "confidence": confidence, "needs_review": needs_review}

    def _search_knowledge_bm25(self, query: str, kb_type: str = "knowledge") -> list:
        """BM25 search for knowledge/policy"""
        if not NEW_MODULES_AVAILABLE or not hasattr(self, "knowledge_search"):
            return []

        search = self.knowledge_search if kb_type == "knowledge" else self.policy_search

        try:
            results = search.search(query, top_k=5)
            return [
                {"title": r.get("title", ""), "text": r.get("text", ""), "score": r.get("score", 0)}
                for r in results
            ]
        except Exception as e:
            logger.warning("BM25 search failed", error=str(e))
            return []

    def get_stats(self) -> Dict:
        """Get supervisor statistics including v2 enhancements"""
        stats = {
            "version": "v2" if NEW_MODULES_AVAILABLE else "legacy",
            "new_modules": NEW_MODULES_AVAILABLE,
        }

        if NEW_MODULES_AVAILABLE:
            if hasattr(self, "query_cache"):
                cache_stats = self.query_cache.get_stats()
                stats["cache"] = {
                    "size": cache_stats.get("size", 0),
                    "hit_rate": cache_stats.get("hit_rate", 0),
                    "hits": cache_stats.get("hit_count", 0),
                    "misses": cache_stats.get("miss_count", 0),
                }

            if hasattr(self, "decision_engine") and self.decision_engine.router:
                try:
                    stats["routing"] = self.decision_engine.router.get_routing_stats()
                except Exception:
                    pass

        return stats

    # ===== Original Methods =====

    def _classify_intent(
        self, payload: InputPayload, memory: MemoryContextModel
    ) -> IntentClassification:
        from src.core.intent_classifier import IntentClassifier

        classifier = IntentClassifier()
        return classifier.classify(payload, memory)

    def _evaluate_risk(self, payload: InputPayload, memory: MemoryContextModel) -> RiskEvaluation:
        from src.core.risk_evaluator import RiskEvaluator

        evaluator = RiskEvaluator()
        return evaluator.evaluate(payload, memory)

    async def _check_patterns(self, payload: InputPayload) -> Optional[tuple[str, float]]:
        """Check for matching learned patterns before generating answer."""
        try:
            from src.services.pattern_learning_service import PatternLearningService

            async_session_maker = self._get_async_session()
            async with async_session_maker() as session:
                pattern_service = PatternLearningService(session)
                result = await pattern_service.find_similar_pattern(
                    question=payload.message.text,
                    user_id=payload.user.id,
                    team_id=payload.user.team,
                )

                if result:
                    pattern, similarity = result
                    await pattern_service.increment_usage(pattern.id)
                    logger.info(
                        "pattern_matched", similarity=similarity, question=payload.message.text[:50]
                    )
                    return pattern.answer_text, similarity

        except Exception as e:
            logger.warning("pattern_check_failed", error=str(e))

        return None

    def _get_async_session(self):
        """Get async session factory."""
        from src.db.session import async_session

        return async_session

    async def _generate_direct_answer(
        self, payload: InputPayload, memory: MemoryContextModel
    ) -> tuple[str, float]:
        user_name = payload.user.display_name
        message = payload.message.text

        user_profile = memory.user_profile or {}
        preferences = user_profile.get("preferences", {}) if isinstance(user_profile, dict) else {}
        style_profile = (
            preferences.get("style_profile", {}) if isinstance(preferences, dict) else {}
        )
        response_persona_hint = (
            preferences.get("response_persona_hint")
            or (
                style_profile.get("response_persona_hint")
                if isinstance(style_profile, dict)
                else None
            )
            or user_profile.get("response_persona_hint")
        )
        communication_style = user_profile.get("communication_style") or "balanced"

        persona_lines = []
        if communication_style:
            persona_lines.append(f"Phong cách người dùng: {communication_style}")
        if response_persona_hint:
            persona_lines.append(f"Persona học được: {response_persona_hint}")

        state_lines = []
        conversation_state = memory.conversation_state or {}
        chat_type = payload.conversation.chat_type or conversation_state.get("chat_type")
        chat_scope = payload.conversation.chat_scope or conversation_state.get("chat_scope")
        group_chat = (
            payload.conversation.group_chat
            if payload.conversation.group_chat is not None
            else conversation_state.get("group_chat")
        )
        platform = payload.conversation.platform or payload.source
        if platform:
            state_lines.append(f"Kênh: {platform}")
        if chat_type:
            state_lines.append(f"Loại chat: {chat_type}")
        if chat_scope:
            state_lines.append(f"Chat scope: {chat_scope}")
        if group_chat is not None:
            state_lines.append(f"Group chat: {group_chat}")
        if conversation_state.get("active_topic_title"):
            state_lines.append(f"Chủ đề hiện tại: {conversation_state['active_topic_title']}")
        if conversation_state.get("conversation_mode"):
            state_lines.append(f"Trạng thái hội thoại: {conversation_state['conversation_mode']}")
        if conversation_state.get("last_user_message_mode"):
            state_lines.append(f"Người dùng đang: {conversation_state['last_user_message_mode']}")
        if conversation_state.get("continuity_score") is not None:
            state_lines.append(f"Điểm liên tục: {conversation_state['continuity_score']}")
        if conversation_state.get("open_loops"):
            state_lines.append(f"Open loops: {conversation_state.get('open_loops', [])[:3]}")
        if conversation_state.get("key_entities"):
            state_lines.append(f"Entities: {conversation_state.get('key_entities', [])[:5]}")

        persona_block = "\n".join(persona_lines)
        state_block = "\n".join(state_lines)

        if self._llm:
            system_prompt = (
                "Bạn là một trợ lý AI hữu ích. Trả lời ngắn gọn, chính xác bằng tiếng Việt."
            )
            if persona_block:
                system_prompt = f"{system_prompt}\n{persona_block}"
            if state_block:
                system_prompt = f"{system_prompt}\n{state_block}"
            response: LLMResponse = await self._llm.complete(
                system_prompt=system_prompt,
                user_message=f"Người dùng {user_name} hỏi: {message}",
                context=memory.to_dict(),
            )
            return response.content, response.confidence

        return (
            f'Xin chào {user_name}, về câu hỏi của bạn "{message[:100]}...", tôi có thể giúp bạn. Bạn cần thêm thông tin gì không?',
            0.4,
        )

    def _normalize_final_confidence(
        self,
        confidence: float,
        kb_hit: bool,
        qa_needs_review: bool,
    ) -> float:
        """Keep confidence conservative unless KB evidence and QA both support 0.9."""
        confidence = max(0.0, min(1.0, confidence))
        if not kb_hit:
            return min(confidence, 0.49)
        if kb_hit and not qa_needs_review and confidence >= 0.85:
            return 0.9
        if confidence >= 0.9:
            return 0.89
        return round(confidence, 2)

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
        extra_metadata: Optional[dict] = None,
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

        output.metadata.update(extra_metadata or {})
        output.request_id = payload.request_id

        return output

    async def _handle_guide_request(self, payload: InputPayload, policy: Dict) -> str:
        """Handle guide delivery request - look up guide content and format nicely."""
        guide_id = policy.get("guide_id")
        guide_title = policy.get("guide_title", "Hướng dẫn")
        
        # Try to get full guide content from KB
        guide_content = None
        if guide_id:
            try:
                from src.db import async_session
                from src.knowledge.repository import KnowledgeBaseRepository
                async with async_session() as session:
                    repo = KnowledgeBaseRepository(session)
                    guide = await repo.get_guide(guide_id)
                    if guide:
                        guide_content = guide.content
            except Exception as e:
                logger.warning("Failed to get guide from KB", guide_id=guide_id, error=str(e))
        
        # Format response nicely
        if guide_content:
            # Get first few lines as summary
            lines = guide_content.strip().split('\n')
            summary_lines = []
            for line in lines[:5]:
                line = line.strip()
                if line:
                    summary_lines.append(line)
            summary = " | ".join(summary_lines) if summary_lines else guide_content[:200]
            
            answer = f"📖 **{guide_title}**\n\n{summary}\n\nXem chi tiết đầy đủ bên dưới:\n\n{guide_content}"
        else:
            # Fallback if no KB content
            answer = f"📖 **{guide_title}**\n\nTôi không tìm thấy nội dung chi tiết cho hướng dẫn này. Bạn cần hỗ trợ thêm không?"
        
        return answer

    async def _handle_itc_ticket_request(self, payload: InputPayload) -> tuple[str, float]:
        """Handle ITC ticket request - extract ticket ID, fetch from n8n, search KB, suggest solution."""
        import re
        
        text = payload.message.text
        
        # Pattern 1: "IT Center has received a support request" + ticket link
        ticket_patterns = [
            r'woID=(\d+)',  # woID=4711234
            r'woID=(\d+)',  # WorkOrder ID
            r'ticket[:\s]+(\d+)',  # ticket: 4711234 or ticket 4711234
            r'#(\d{7,})',  # 7+ digit number (ITC ticket IDs)
        ]
        
        ticket_id = None
        for pattern in ticket_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                ticket_id = match.group(1)
                break
        
        if not ticket_id:
            return {"answer": "Tôi không tìm thấy mã ticket trong tin nhắn. Bạn có thể cung cấp mã ticket không?", "confidence": 0.3, "ticket_id": None}
        
        # Try to get ticket details from n8n/ITC API
        ticket_content = None
        ticket_subject = None
        ticket_description = None
        
        try:
            if hasattr(self, 'n8n_connector') and self.n8n_connector:
                # Call n8n workflow to fetch ticket
                ticket_content = await self.n8n_connector.trigger_workflow(
                    "itc_ticket_fetch",
                    {"ticket_id": ticket_id}
                )
        except Exception as e:
            logger.warning("Failed to fetch ticket from n8n", ticket_id=ticket_id, error=str(e))
        
        # Try direct ITC API if n8n not available
        if not ticket_content:
            try:
                # Try direct IT service API call
                import httpx
                settings = get_settings()
                itc_api_url = getattr(settings, 'itc_api_url', None)
                if itc_api_url:
                    async with httpx.AsyncClient(timeout=30) as client:
                        response = await client.get(
                            f"{itc_api_url}/WorkOrder.do",
                            params={"woMode": "viewWO", "woID": ticket_id}
                        )
                        if response.status_code == 200:
                            ticket_content = response.text
            except Exception as e:
                logger.warning("Failed to fetch ticket directly", ticket_id=ticket_id, error=str(e))
        
        # Extract subject if we got content
        if ticket_content:
            subject_match = re.search(r'<subject>([^<]+)</subject>', ticket_content, re.IGNORECASE)
            if subject_match:
                ticket_subject = subject_match.group(1).strip()
            
            # Try to get description
            desc_match = re.search(r'<description>([^<]+)</description>', ticket_content, re.IGNORECASE)
            if desc_match:
                ticket_description = desc_match.group(1).strip()
        
        # Search KB for related solutions
        kb_suggestions = []
        search_query = ticket_subject or f"ticket {ticket_id}"
        
        try:
            from src.db import async_session
            from src.knowledge.service import KnowledgeRetrievalService
            async with async_session() as session:
                kb_service = KnowledgeRetrievalService(session, self._llm)
                kb_results = await kb_service.search(search_query, "faq")
                for r in kb_results.results[:3]:
                    kb_suggestions.append({
                        "title": r.title,
                        "content": r.content[:300],
                        "similarity": r.similarity
                    })
        except Exception as e:
            logger.warning("KB search failed", error=str(e))
        
        # Format response
        response_parts = [f"🎫 **Ticket #{ticket_id}**"]
        
        if ticket_subject:
            response_parts.append(f"**Subject:** {ticket_subject}")
        
        if kb_suggestions:
            response_parts.append("\n📚 **Gợi ý từ Knowledge Base:**")
            for i, sugg in enumerate(kb_suggestions, 1):
                response_parts.append(f"{i}. **{sugg['title']}**\n   {sugg['content']}")
        else:
            response_parts.append("\n🔍 Tôi đang phân tích và tìm giải pháp...")
            # Use AI to reason if LLM available
            if self._llm:
                system_prompt = f"""Bạn là chuyên gia IT support. Dựa vào thông tin ticket #{ticket_id}:
Subject: {ticket_subject or 'Unknown'}
Hãy đề xuất giải pháp hoặc các bước tiếp theo."""
                try:
                    llm_response = await self._llm.complete(system_prompt, "")
                    response_parts.append(f"\n💡 **Gợi ý:**\n{llm_response.content}")
                except Exception as e:
                    logger.warning("LLM reasoning failed", error=str(e))
        
        answer = "\n".join(response_parts)
        return {"answer": answer, "confidence": 0.85, "ticket_id": ticket_id}

    async def _handle_system_query(
        self,
        payload: InputPayload,
        memory: MemoryContextModel,
        query_type: str,
    ) -> Dict:
        return {"result": "system query result", "confidence": 0.9}

    def _format_system_query_response(self, query_result: Dict) -> str:
        return f"System query result: {query_result.get('result', 'N/A')}"

    def _build_kb_clarification_question(self, kb_source: Dict, missing_fields: list[str]) -> str:
        labels = {
            "error_message": "thông báo lỗi chính xác",
            "error_code": "mã lỗi",
            "system": "tên hệ thống/dịch vụ liên quan",
            "environment": "môi trường (prod/dev/staging)",
            "device": "thiết bị đang dùng",
            "os": "hệ điều hành/phiên bản máy",
            "step": "bạn đang kẹt ở bước nào",
            "user_id": "user_id/tài khoản liên quan",
            "policy_scope": "phạm vi áp dụng",
            "document_scope": "phạm vi tài liệu cần tra",
            "use_case": "trường hợp sử dụng cụ thể",
            "user_role": "vai trò/phòng ban liên quan",
        }
        friendly_fields = [
            labels.get(field, field.replace("_", " ")) for field in missing_fields[:4]
        ]
        fields_text = "; ".join(friendly_fields)
        title = kb_source.get("title") or kb_source.get("id") or "KB"
        return f"Mình tìm thấy KB phù hợp về '{title}'. Để support đúng theo KB, bạn cho mình thêm: {fields_text}."

    def _attachment_value(self, attachment: Any, key: str, default: Any = None) -> Any:
        if hasattr(attachment, key):
            return getattr(attachment, key, default)
        if isinstance(attachment, dict):
            return attachment.get(key, default)
        return default

    def _is_image_attachment(self, attachment: Any) -> bool:
        attachment_type = str(self._attachment_value(attachment, "type") or "").strip().lower()
        content_type = str(self._attachment_value(attachment, "content_type") or self._attachment_value(attachment, "mime_type") or "").strip().lower()
        name = str(self._attachment_value(attachment, "name") or self._attachment_value(attachment, "filename") or "").lower()
        return (
            attachment_type in {"image", "photo", "picture"}
            or content_type.startswith("image/")
            or name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".tif"))
        )

    def _normalize_issue_signature(self, *parts: str) -> str:
        combined = " ".join(part for part in parts if part)
        tokens = re.findall(r"[\wÀ-ỹ0-9]+", combined.lower(), flags=re.UNICODE)
        stopwords = {
            "attachment",
            "attachments",
            "image",
            "screenshot",
            "photo",
            "picture",
            "file",
            "files",
            "please",
            "send",
            "forwarded",
            "evidence",
            "ocr",
            "text",
            "the",
            "and",
            "for",
            "with",
            "from",
            "this",
            "that",
            "và",
            "cho",
            "mình",
            "tôi",
            "của",
            "đang",
            "lỗi",
            "ảnh",
            "hình",
        }
        kept: list[str] = []
        for token in tokens:
            token = token.strip()
            if not token or len(token) == 1:
                continue
            if token in stopwords:
                continue
            if token not in kept:
                kept.append(token)
        return " ".join(kept[:12])

    def _build_image_case_context(self, payload: InputPayload) -> dict[str, Any]:
        attachments = list(payload.message.attachments or [])
        image_attachments = [attachment for attachment in attachments if self._is_image_attachment(attachment)]
        if not image_attachments:
            return {
                "has_image_attachments": False,
                "image_case": False,
                "needs_clarification": False,
                "issue_signature": "",
                "clarification_question": "",
                "internal_note": "",
                "draft_reply_hint": "",
                "candidate_hint": False,
            }

        settings = get_settings()
        image_llm = self._get_image_llm()
        use_image_model = image_llm and settings.ollama_image_model

        attachment_texts: list[str] = []
        attachment_names: list[str] = []
        for attachment in image_attachments[:5]:
            name = str(self._attachment_value(attachment, "name") or self._attachment_value(attachment, "filename") or "image").strip()
            attachment_names.append(name)
            ocr_text = str(self._attachment_value(attachment, "ocr_text") or self._attachment_value(attachment, "text") or "").strip()
            if ocr_text:
                attachment_texts.append(ocr_text)
            elif use_image_model and image_llm:
                # Use image model to extract text from attachment if no OCR available
                # Note: This requires the attachment to have actual image data/b64
                pass  # Image model OCR handling can be added here

        payload_text = payload.message.text or ""
        issue_signature = self._normalize_issue_signature(payload_text, " ".join(attachment_texts), " ".join(attachment_names))
        has_original_text = bool(payload_text.strip() and not payload_text.strip().startswith("[Attachment"))
        has_ocr_text = bool(attachment_texts)
        needs_clarification = not has_original_text and not has_ocr_text
        clarification_question = (
            "Mình thấy ảnh đính kèm nhưng chưa đọc được đủ nội dung. Bạn gửi lại ảnh rõ hơn hoặc chép lại mã lỗi / bước đang bị kẹt nhé."
            if needs_clarification
            else ""
        )
        issue_summary = issue_signature or " / ".join(attachment_names[:2])
        internal_note = ""
        if issue_summary:
            internal_note = f"Image case summary: {issue_summary}"
            if has_ocr_text:
                internal_note += f" | OCR: {self._normalize_issue_signature(' '.join(attachment_texts))}"
        draft_reply_hint = issue_signature or clarification_question
        return {
            "has_image_attachments": True,
            "image_case": True,
            "needs_clarification": needs_clarification,
            "issue_signature": issue_signature,
            "issue_summary": issue_summary,
            "attachment_names": attachment_names,
            "attachment_texts": attachment_texts,
            "clarification_question": clarification_question,
            "internal_note": internal_note,
            "draft_reply_hint": draft_reply_hint,
            "candidate_hint": bool(issue_signature),
            "image_model_used": settings.ollama_image_model if use_image_model else None,
        }

    def _build_image_case_clarification(self, image_case_context: dict[str, Any], fallback_question: str = "") -> str:
        question = image_case_context.get("clarification_question") or fallback_question
        if question:
            return question
        return "Mình thấy ảnh đính kèm nhưng chưa đọc được đủ nội dung. Bạn gửi lại ảnh rõ hơn hoặc chép lại mã lỗi / bước đang bị kẹt nhé."

    async def _record_image_case_learning(self, payload: InputPayload, image_case_context: dict[str, Any], result: Dict[str, Any]) -> None:
        try:
            from src.services.learning_events import record_learning_event

            async with async_session() as session:
                await record_learning_event(
                    session,
                    request_id=payload.request_id,
                    event_type="image_case_observed",
                    event_payload={
                        "issue_signature": image_case_context.get("issue_signature", ""),
                        "issue_summary": image_case_context.get("issue_summary", ""),
                        "has_image_attachments": image_case_context.get("has_image_attachments", False),
                        "kb_hit": bool(result.get("kb_hit")),
                        "status": result.get("status"),
                    },
                    user_id=payload.user.id,
                    thread_id=payload.conversation.thread_id,
                    ticket_id=payload.case.ticket_id if payload.case else None,
                    ticket_system=payload.case.ticket_system if payload.case else None,
                )
                await session.commit()
        except Exception as exc:
            logger.warning("image_case_learning_record_failed", error=str(exc), request_id=payload.request_id)

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

    async def _generate_direct_answer(
        self,
        payload: InputPayload,
        memory: MemoryContextModel,
        support_context: Optional[dict[str, Any]] = None,
    ) -> tuple[str, float]:
        user_name = payload.user.display_name
        message = payload.message.text
        support_context = support_context or {}
        image_case_context = support_context.get("image_case_context") or {}

        user_profile = memory.user_profile or {}
        preferences = user_profile.get("preferences", {}) if isinstance(user_profile, dict) else {}
        style_profile = (
            preferences.get("style_profile", {}) if isinstance(preferences, dict) else {}
        )
        response_persona_hint = (
            preferences.get("response_persona_hint")
            or (
                style_profile.get("response_persona_hint")
                if isinstance(style_profile, dict)
                else None
            )
            or user_profile.get("response_persona_hint")
        )
        communication_style = user_profile.get("communication_style") or "balanced"

        persona_lines = []
        if communication_style:
            persona_lines.append(f"Phong cách người dùng: {communication_style}")
        if response_persona_hint:
            persona_lines.append(f"Persona học được: {response_persona_hint}")
        if image_case_context.get("image_case"):
            persona_lines.append(f"Image case signature: {image_case_context.get('issue_signature') or image_case_context.get('issue_summary')}")

        state_lines = []
        conversation_state = memory.conversation_state or {}
        chat_type = payload.conversation.chat_type or conversation_state.get("chat_type")
        chat_scope = payload.conversation.chat_scope or conversation_state.get("chat_scope")
        group_chat = (
            payload.conversation.group_chat
            if payload.conversation.group_chat is not None
            else conversation_state.get("group_chat")
        )
        platform = payload.conversation.platform or payload.source
        if platform:
            state_lines.append(f"Kênh: {platform}")
        if chat_type:
            state_lines.append(f"Loại chat: {chat_type}")
        if chat_scope:
            state_lines.append(f"Chat scope: {chat_scope}")
        if group_chat is not None:
            state_lines.append(f"Group chat: {group_chat}")
        if conversation_state.get("active_topic_title"):
            state_lines.append(f"Chủ đề hiện tại: {conversation_state['active_topic_title']}")
        if conversation_state.get("conversation_mode"):
            state_lines.append(f"Trạng thái hội thoại: {conversation_state['conversation_mode']}")
        if conversation_state.get("last_user_message_mode"):
            state_lines.append(f"Người dùng đang: {conversation_state['last_user_message_mode']}")
        if conversation_state.get("continuity_score") is not None:
            state_lines.append(f"Điểm liên tục: {conversation_state['continuity_score']}")
        if conversation_state.get("open_loops"):
            state_lines.append(f"Open loops: {conversation_state.get('open_loops', [])[:3]}")
        if conversation_state.get("key_entities"):
            state_lines.append(f"Entities: {conversation_state.get('key_entities', [])[:5]}")
        if image_case_context.get("internal_note"):
            state_lines.append(f"Image case note: {image_case_context['internal_note']}")

        persona_block = "\n".join(persona_lines)
        state_block = "\n".join(state_lines)

        if self._llm:
            system_prompt = (
                "Bạn là một trợ lý AI hữu ích. Trả lời ngắn gọn, chính xác bằng tiếng Việt."
            )
            if persona_block:
                system_prompt = f"{system_prompt}\n{persona_block}"
            if state_block:
                system_prompt = f"{system_prompt}\n{state_block}"
            merged_context = memory.to_dict()
            if support_context:
                merged_context["support_context"] = support_context
            response: LLMResponse = await self._llm.complete(
                system_prompt=system_prompt,
                user_message=f"Người dùng {user_name} hỏi: {message}",
                context=merged_context,
            )
            return response.content, response.confidence

        if image_case_context.get("clarification_question"):
            return image_case_context["clarification_question"], 0.45

        return (
            f'Xin chào {user_name}, về câu hỏi của bạn "{message[:100]}...", tôi có thể giúp bạn. Bạn cần thêm thông tin gì không?',
            0.4,
        )

    def _stable_rollout_bucket(self, key: str, salt: str) -> int:
        """Compute deterministic bucket [0, 99] for rollout decisions."""
        raw = f"{salt}:{key}".encode("utf-8")
        digest = sha256(raw).hexdigest()
        return int(digest[:8], 16) % 100

    def _is_in_rollout(self, identifier: str | None, percent: int, salt: str, scope: str) -> bool:
        """Return True if identifier is included in rollout percent."""
        if not identifier:
            metrics.record_reasoning_loop_rollout(scope=scope, outcome="no_id")
            return False

        normalized_percent = max(0, min(100, int(percent)))
        if normalized_percent == 100:
            metrics.record_reasoning_loop_rollout(scope=scope, outcome="enabled")
            return True
        if normalized_percent == 0:
            metrics.record_reasoning_loop_rollout(scope=scope, outcome="disabled")
            return False

        bucket = self._stable_rollout_bucket(identifier, salt)
        enabled = bucket < normalized_percent
        metrics.record_reasoning_loop_rollout(
            scope=scope,
            outcome="enabled" if enabled else "disabled",
        )
        return enabled

    def _should_run_reasoning_loop(self, payload: InputPayload, settings) -> tuple[bool, dict]:
        """Evaluate reasoning loop gate using feature flag + % rollout by team/user."""
        if not settings.enable_reasoning_loop:
            return False, {
                "enabled": False,
                "reason": "feature_flag_off",
                "team_percent": int(getattr(settings, "reasoning_loop_rollout_team_percent", 100)),
                "user_percent": int(getattr(settings, "reasoning_loop_rollout_user_percent", 100)),
            }

        team_percent = int(getattr(settings, "reasoning_loop_rollout_team_percent", 100))
        user_percent = int(getattr(settings, "reasoning_loop_rollout_user_percent", 100))
        salt = str(getattr(settings, "reasoning_loop_rollout_salt", "reasoning-loop-v1"))

        team_id = payload.user.team
        user_id = payload.user.id

        team_enabled = self._is_in_rollout(team_id, team_percent, f"{salt}:team", "team")
        user_enabled = self._is_in_rollout(user_id, user_percent, f"{salt}:user", "user")
        enabled = team_enabled or user_enabled

        return enabled, {
            "enabled": enabled,
            "team_enabled": team_enabled,
            "user_enabled": user_enabled,
            "team_percent": max(0, min(100, team_percent)),
            "user_percent": max(0, min(100, user_percent)),
            "team_id_present": bool(team_id),
            "user_id_present": bool(user_id),
            "salt": salt,
        }
