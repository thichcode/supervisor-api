from src.core import InputPayload, IntentClassification, IntentType
from src.memory import MemoryContext
import re
import structlog

logger = structlog.get_logger()


class IntentClassifier:
    PATTERNS = {
        IntentType.FAQ: [
            # English
            r"what is",
            r"how to",
            r"how do",
            r"what does",
            r"where is",
            r"where do",
            r"who is",
            r"who are",
            r"when does",
            r"can i",
            r"is it possible",
            r"tìm hiểu",
            r"giải thích",
            r"cho biết",
            r"thông tin",
            # Vietnamese common
            r"là gì",
            r"như thế nào",
            r"ở đâu",
            r"khi nào",
            r"ai là",
            r"cái gì",
            r"làm sao",
            r"có thể không",
            r"được không",
            r"nói cho tôi biết",
            r"cho hỏi",
            r"xin hỏi",
            r"muốn hỏi",
        ],
        IntentType.POLICY: [
            # English
            r"policy",
            r"guideline",
            r"rule",
            r"sop",
            r"procedure",
            r"regulation",
            r"compliance",
            r"requirement",
            # Vietnamese
            r"quy định",
            r"chính sách",
            r"hướng dẫn",
            r"quy trình",
            r"tiêu chuẩn",
            r"nội quy",
            r"thể lệ",
            r"điều lệ",
            r"quy luật",
            r"nguyên tắc",
            r"yêu cầu",
            r"quyền lợi",
            r"phúc lợi",
            r"đánh giá",
            r"thưởng",
            r"phạt",
            r"nghỉ phép",
            r"đi muộn",
            r"work from home",
            r"wfh",
            r"remote",
        ],
        IntentType.SUPPORT_CASE: [
            # English
            r"case",
            r"ticket",
            r"issue",
            r"problem",
            r"support",
            r"bug",
            r"error",
            r"crash",
            r"not working",
            r"broken",
            r"fail",
            r"stuck",
            r"cannot",
            r"can't",
            r"unable to",
            r"help me",
            r"urgent",
            r"asap",
            # Vietnamese
            r"sự cố",
            r"vấn đề",
            r"hỗ trợ",
            r"lỗi",
            r"hỏng",
            r"không được",
            r"không hoạt động",
            r"bị lỗi",
            r"treo",
            r"đơ",
            r"chậm",
            r"lag",
            r"giật",
            r"không vào được",
            r"đăng nhập không được",
            r"quên mật khẩu",
            r"reset password",
            r"cần hỗ trợ",
            r"gấp",
            r"khẩn cấp",
        ],
        IntentType.ANALYSIS: [
            # English
            r"analyze",
            r"analysis",
            r"report",
            r"summary",
            r"trend",
            r"data",
            r"statistics",
            r"metrics",
            r"insight",
            r"overview",
            r"dashboard",
            r"performance",
            r"benchmark",
            # Vietnamese
            r"phân tích",
            r"báo cáo",
            r"tổng hợp",
            r"thống kê",
            r"số liệu",
            r"biểu đồ",
            r"đồ thị",
            r"xem xét",
            r"đánh giá",
            r"hiệu suất",
            r"tiến độ",
            r"progress",
            r"tình hình",
            r"status",
            r"update",
        ],
        IntentType.EXECUTIVE_REQUEST: [
            # English
            r"ceo",
            r"cto",
            r"cfo",
            r"coo",
            r"director",
            r"vp",
            r"head of",
            r"head",
            r"manager",
            r"leader",
            r"board",
            r"executive",
            r"urgent",
            r"asap",
            r"important",
            r"critical",
            r"priority",
            r"confidential",
            r"budget",
            r"revenue",
            r"profit",
            r"meeting",
            r"presentation",
            # Vietnamese
            r"sếp",
            r"giám đốc",
            r"trưởng phòng",
            r"quản lý",
            r"lãnh đạo",
            r"ban lãnh đạo",
            r"board",
            r"cấp cao",
            r"quan trọng",
            r"gấp",
            r"khẩn",
            r"họp",
            r"báo cáo gấp",
            r"báo cáo ngay",
            r"doanh thu",
            r"lợi nhuận",
            r"ngân sách",
            r"chi phí",
            r"đầu tư",
            r"mua sắm",
            r"tuyển dụng",
            r"sao thải",
            r"thay đổi chiến lược",
        ],
    }

    # Context keywords - bổ sung context từ memory/user profile
    CONTEXT_BOOST = {
        "project_manager": [r"project", r"deadline", r"milestone", r"sprint", r"scrum"],
        "hr": [r"nhân sự", r"tuyển", r"lương", r"thưởng", r"phép", r"đánh giá"],
        "finance": [r"tài chính", r"thanh toán", r"hóa đơn", r"chi phí", r"ngân sách"],
        "it": [r"server", r"network", r"wifi", r"email", r"vpn", r"tool", r"software"],
        "sales": [r"khách hàng", r"hợp đồng", r"deal", r"quote", r"báo giá"],
    }

    def __init__(self, llm=None, preferred_model: str | None = None):
        self.llm = llm
        self.preferred_model = preferred_model

    def _has_any(self, text: str, patterns: list[str]) -> bool:
        return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)

    def _build_context_text(self, payload: InputPayload, memory: MemoryContext) -> str:
        parts = []
        user_profile = memory.user_profile or {}
        conversation_state = memory.conversation_state or {}

        if payload.user.role:
            parts.append(f"user_role={payload.user.role}")
        if payload.user.team:
            parts.append(f"user_team={payload.user.team}")
        if user_profile.get("communication_style"):
            parts.append(f"communication_style={user_profile.get('communication_style')}")
        if conversation_state.get("last_user_message_mode"):
            parts.append(f"last_user_message_mode={conversation_state.get('last_user_message_mode')}")
        if conversation_state.get("conversation_mode"):
            parts.append(f"conversation_mode={conversation_state.get('conversation_mode')}")
        if payload.case and payload.case.case_id:
            parts.append(f"case_id={payload.case.case_id}")
        if memory.case_memory:
            parts.append(f"case_status={memory.case_memory.get('status', '')}")
            parts.append(f"case_summary={memory.case_memory.get('summary', '')}")
        if memory.conversation_summary:
            parts.append(f"conversation_summary={memory.conversation_summary}")
        if memory.recent_messages:
            parts.append(f"recent_messages={memory.recent_messages[-3:]}")

        return "\n".join(str(part) for part in parts if part)

    def _emit_result(self, intent: IntentType, confidence: float, source: str) -> IntentClassification:
        result = IntentClassification(intent=intent, confidence=confidence, source=source)
        logger.info("intent_classified", intent=intent.value, confidence=confidence, intent_source=source)
        return result

    def _normalize_intent(self, raw_intent) -> IntentType | None:
        if not raw_intent:
            return None
        try:
            return IntentType(str(raw_intent))
        except Exception:
            normalized = str(raw_intent).strip().lower()
            for intent in IntentType:
                if intent.value == normalized or intent.name.lower() == normalized:
                    return intent
        return None

    def _rule_guardrail(self, text: str, payload: InputPayload, memory: MemoryContext) -> tuple[IntentType | None, float, str]:
        if payload.case and payload.case.case_id:
            return IntentType.SUPPORT_CASE, 0.9, "case_present"

        policy_high_precision = [
            r"theo policy",
            r"theo chính sách",
            r"theo quy định",
            r"theo quy trình",
            r"được phép",
            r"có được phép",
            r"tuân theo policy",
            r"phù hợp policy",
        ]
        support_high_precision = [
            r"không vào được",
            r"không hoạt động",
            r"đăng nhập không được",
            r"login",
            r"đăng nhập",
            r"lỗi",
            r"error",
            r"issue",
            r"bug",
            r"sự cố",
        ]
        faq_high_precision = [
            r"là gì",
            r"how to",
            r"how do",
            r"làm sao",
            r"cách nào",
            r"giải thích",
            r"cho biết",
        ]

        if self._has_any(text, policy_high_precision):
            return IntentType.POLICY, 0.84, "policy_cue"
        if self._has_any(text, support_high_precision):
            return IntentType.SUPPORT_CASE, 0.82, "support_cue"
        if self._has_any(text, faq_high_precision):
            return IntentType.FAQ, 0.78, "faq_cue"
        return None, 0.0, ""

    def _fallback_classify(self, text: str, payload: InputPayload, memory: MemoryContext) -> IntentClassification:
        scores = {}
        for intent, patterns in self.PATTERNS.items():
            score = 0
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    score += 1
            scores[intent] = score

        if payload.user.vip_flag:
            scores[IntentType.EXECUTIVE_REQUEST] += 2
        if payload.case and payload.case.case_id:
            scores[IntentType.SUPPORT_CASE] += 1
        if memory.case_memory:
            scores[IntentType.SUPPORT_CASE] += 1

        user_role = memory.user_profile.get("role", "").lower() if memory.user_profile else ""
        user_team = memory.user_profile.get("team", "").lower() if memory.user_profile else ""

        if "manager" in user_role or "project" in user_team:
            scores[IntentType.ANALYSIS] = scores.get(IntentType.ANALYSIS, 0) + 1
        if "hr" in user_team or "nhân" in user_role:
            scores[IntentType.POLICY] = scores.get(IntentType.POLICY, 0) + 1
        if "it" in user_team or "support" in user_role:
            scores[IntentType.SUPPORT_CASE] = scores.get(IntentType.SUPPORT_CASE, 0) + 1

        if scores.get(IntentType.POLICY, 0) > 0:
            scores[IntentType.POLICY] += 0.5
        if scores.get(IntentType.SUPPORT_CASE, 0) > 0:
            scores[IntentType.SUPPORT_CASE] += 0.5

        no_match = not any(scores.values())
        if no_match:
            scores[IntentType.FAQ] = 0.4

        max_intent = max(scores, key=scores.get)
        max_score = scores[max_intent]

        if no_match:
            confidence = 0.4
        elif max_score == 0:
            confidence = 0.4
        else:
            confidence = min(0.95, 0.6 + (max_score * 0.1))

        return IntentClassification(intent=max_intent, confidence=confidence, source="fallback")

    async def classify(self, payload: InputPayload, memory: MemoryContext) -> IntentClassification:
        text = (payload.message.text or "").lower() if payload.message else ""

        if payload.case and payload.case.case_id:
            return self._emit_result(IntentType.SUPPORT_CASE, 0.85, "guardrail")

        model_client = self.llm
        if model_client is None:
            try:
                from src.llm import get_llm_client

                model_client = await get_llm_client()
            except Exception:
                model_client = None

        guardrail_intent, guardrail_confidence, guardrail_reason = self._rule_guardrail(text, payload, memory)

        if model_client:
            try:
                available_intents = [intent.value for intent in IntentType]
                model_result = await model_client.classify_intent(
                    message=payload.message.text,
                    context=self._build_context_text(payload, memory),
                    available_intents=available_intents,
                    model=self.preferred_model,
                )
                model_intent = self._normalize_intent(model_result.get("intent"))
                model_confidence = float(model_result.get("confidence", 0.4) or 0.4)

                if guardrail_intent and (
                    model_intent is None
                    or model_confidence < 0.55
                    or (guardrail_reason == "policy_cue" and model_intent != IntentType.POLICY)
                    or (guardrail_reason == "support_cue" and model_intent == IntentType.FAQ)
                ):
                    return self._emit_result(guardrail_intent, max(model_confidence, guardrail_confidence), "guardrail")

                if model_intent:
                    return self._emit_result(model_intent, max(0.0, min(1.0, model_confidence)), "model")
            except Exception:
                pass

        if guardrail_intent:
            return self._emit_result(guardrail_intent, guardrail_confidence, "guardrail")

        return self._fallback_classify(text, payload, memory)
