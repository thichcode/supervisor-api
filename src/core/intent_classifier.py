from src.core import InputPayload, IntentClassification, IntentType, MemoryContext
import re


class IntentClassifier:
    PATTERNS = {
        IntentType.FAQ: [
            r"what is",
            r"how to",
            r"how do",
            r"what does",
            r"where is",
            r"where do",
            r"là gì",
            r"như thế nào",
            r"ở đâu",
        ],
        IntentType.POLICY: [
            r"policy",
            r"guideline",
            r"rule",
            r"sop",
            r"quy định",
            r"chính sách",
            r"hướng dẫn",
            r"quy trình",
        ],
        IntentType.SUPPORT_CASE: [
            r"case",
            r"ticket",
            r"issue",
            r"problem",
            r"support",
            r"ticket",
            r"sự cố",
            r"vấn đề",
            r"hỗ trợ",
        ],
        IntentType.ANALYSIS: [
            r"analyze",
            r"report",
            r"summary",
            r"trend",
            r"data",
            r"phân tích",
            r"báo cáo",
            r"tổng hợp",
        ],
        IntentType.EXECUTIVE_REQUEST: [
            r"ceo",
            r"cto",
            r"cfo",
            r"director",
            r"vp",
            r"sếp",
            r"giám đốc",
            r"urgent",
            r"asap",
            r"important",
        ],
    }

    def classify(self, payload: InputPayload, memory: MemoryContext) -> IntentClassification:
        text = payload.message.text.lower()

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

        if not any(scores.values()):
            scores[IntentType.FAQ] = 0.5

        max_intent = max(scores, key=scores.get)
        max_score = scores[max_intent]

        if max_score == 0:
            confidence = 0.5
        else:
            confidence = min(0.95, 0.6 + (max_score * 0.1))

        return IntentClassification(intent=max_intent, confidence=confidence)
