from src.core import InputPayload, RiskEvaluation, RiskLevel, MemoryContext
from src.config import get_settings
import re

settings = get_settings()


class RiskEvaluator:
    def __init__(self):
        self.executive_keywords = settings.executive_keywords
        self.commitment_keywords = settings.commitment_keywords
        self.financial_keywords = settings.financial_keywords
        self.legal_keywords = settings.legal_keywords

    def evaluate(self, payload: InputPayload, memory: MemoryContext) -> RiskEvaluation:
        text = payload.message.text.lower()
        flags = []

        for keyword in self.executive_keywords:
            if re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE):
                flags.append("executive")
                break

        for keyword in self.commitment_keywords:
            if re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE):
                flags.append("commitment")
                break

        for keyword in self.financial_keywords:
            if re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE):
                flags.append("financial")
                break

        for keyword in self.legal_keywords:
            if re.search(rf"\b{re.escape(keyword)}\b", text, re.IGNORECASE):
                flags.append("legal")
                break

        if payload.user.vip_flag:
            flags.append("vip")

        if payload.case and payload.case.priority in ["high", "urgent"]:
            flags.append("high_priority_case")

        risk_level = RiskLevel.LOW
        if len(flags) >= 3 or "executive" in flags:
            risk_level = RiskLevel.HIGH
        elif len(flags) >= 1 or "vip" in flags:
            risk_level = RiskLevel.MEDIUM

        return RiskEvaluation(risk_level=risk_level, flags=list(set(flags)))
