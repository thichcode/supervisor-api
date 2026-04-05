from src.core import InputPayload
from src.llm import LLMClient
from typing import Optional


class DraftAgent:
    async def generate(
        self,
        payload: InputPayload,
        context: dict,
        policy: dict,
        knowledge: dict,
        llm: Optional[LLMClient] = None,
    ) -> str:
        user_name = payload.user.display_name

        if llm:
            answer, _ = await llm.generate_response(
                message=payload.message.text,
                context={
                    "summary": context.get("conversation_summary", ""),
                    "recent_messages": context.get("conversation_history", []),
                    "user_role": context.get("user_info", {}).get("role", "employee"),
                },
                policy=policy,
                knowledge=knowledge,
            )
            return answer

        parts = []

        if policy.get("guidelines_found"):
            parts.append("Based on our guidelines:\n")
            for policy_item in policy.get("relevant_policies", []):
                parts.append(f"- {policy_item}")
            if policy.get("sop_steps"):
                parts.append("\nRecommended steps:")
                for step in policy["sop_steps"]:
                    parts.append(f"  {step}")
            parts.append("")

        if knowledge.get("facts"):
            parts.append("Here is the information you requested:")
            for fact in knowledge.get("facts", []):
                parts.append(f"- {fact}")
            parts.append("")

        if knowledge.get("patterns"):
            parts.append("Based on similar cases:")
            for pattern in knowledge.get("patterns", []):
                parts.append(f"- {pattern}")
            parts.append("")

        if context.get("case_info"):
            case = context["case_info"]
            if case.get("open_items"):
                parts.append("Current open items:")
                for item in case.get("open_items", []):
                    parts.append(f"- {item}")
                parts.append("")

        if not parts:
            parts.append(f"Hi {user_name}, I've reviewed your request and I'm here to help.")

        return "\n".join(parts)


class QAAgent:
    def __init__(self):
        self.confidence_threshold = 0.7

    async def validate(
        self,
        draft: str,
        payload: InputPayload,
        context: dict,
        llm: Optional[LLMClient] = None,
    ) -> dict:
        issues = []
        confidence = 0.85

        if len(draft) < 20:
            issues.append("Response too short")
            confidence -= 0.2

        if not draft.strip():
            issues.append("Empty response")
            confidence -= 0.5

        text_lower = payload.message.text.lower()

        support_keywords = ["giúp", "help", "hỗ trợ", "support", "case", "vấn đề"]
        if any(kw in text_lower for kw in support_keywords):
            if "case" not in draft.lower() and "support" not in draft.lower() and "help" not in draft.lower():
                issues.append("Support request not explicitly addressed")
                confidence -= 0.1

        if context.get("case_info") and context["case_info"].get("status") == "urgent":
            if "urgent" not in draft.lower() and "asap" not in draft.lower():
                issues.append("Urgent case not flagged appropriately")
                confidence -= 0.1

        if "?" in payload.message.text and "?" not in draft:
            issues.append("Question asked but not answered directly")
            confidence -= 0.15

        if llm and issues:
            validation_prompt = f"""Review this draft response for the user's question:
User Question: {payload.message.text}
Draft: {draft}

Check for:
1. Does it answer the question?
2. Is it appropriately detailed?
3. Are there any hallucinations or incorrect info?

Return JSON:
{{"additional_issues": [], "confidence_adjustment": 0.0}}"""

            result, _ = await llm.complete("You are a quality assurance agent.", validation_prompt)

            import json
            import re
            match = re.search(r'\{[^{}]*\}', result, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                    issues.extend(parsed.get("additional_issues", []))
                    confidence += parsed.get("confidence_adjustment", 0)
                except json.JSONDecodeError:
                    pass

        if confidence < self.confidence_threshold:
            issues.append("Confidence below threshold")

        needs_review = len(issues) > 1 or confidence < self.confidence_threshold

        return {
            "draft": draft,
            "confidence": max(0.0, min(1.0, confidence)),
            "issues": issues,
            "needs_review": needs_review,
        }

    def refine(self, validation: dict, payload: InputPayload) -> str:
        draft = validation["draft"]
        issues = validation["issues"]
        user_name = payload.user.display_name

        if not draft.strip():
            return f"Hi {user_name}, thank you for reaching out. I'm reviewing your request and will provide a detailed response shortly."

        if validation.get("needs_review"):
            draft += "\n\n*Note: This response may need further review.*"
        elif len(issues) > 2:
            draft += "\n\n*Note: This response may need further review.*"

        return draft
