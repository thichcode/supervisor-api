from src.core import InputPayload, MemoryContext
from src.memory import MemoryContext as MemoryContextModel
from src.llm import LLMClient
from typing import Optional


class ContextAgent:
    def build(self, payload: InputPayload, memory: MemoryContext) -> dict:
        context = {
            "current_message": payload.message.text,
            "conversation_history": memory.recent_messages,
            "conversation_summary": memory.conversation_summary or "No prior context",
            "user_info": {
                "name": payload.user.display_name,
                "id": payload.user.id,
                "role": memory.user_profile.get("role") if memory.user_profile else None,
                "vip": payload.user.vip_flag,
            },
            "case_info": None,
            "resolved_points": [],
            "unresolved_points": memory.to_dict().get("conversation_summary", ""),
        }

        if payload.case and memory.case_memory:
            context["case_info"] = {
                "case_id": payload.case.case_id,
                "status": memory.case_memory.get("status"),
                "owner": memory.case_memory.get("owner"),
                "priority": payload.case.priority,
                "summary": memory.case_memory.get("summary"),
                "open_items": memory.case_memory.get("open_items", []),
            }

        if len(memory.recent_messages) > 3:
            context["conversation_history"] = memory.recent_messages[-5:]

        return context


class PolicyAgent:
    async def extract(
        self,
        payload: InputPayload,
        memory: MemoryContext,
        llm: Optional[LLMClient] = None,
    ) -> dict:
        text_lower = payload.message.text.lower()
        policy_info = {
            "relevant_policies": [],
            "guidelines_found": False,
            "sop_steps": [],
        }

        policy_keywords = ["policy", "quy định", "chính sách", "guideline", "rule", "sop"]
        if any(kw in text_lower for kw in policy_keywords):
            policy_info["guidelines_found"] = True

            if llm:
                system_prompt = """You are a policy expert. Extract relevant company policies and SOPs from the user's question.
Return JSON format:
{"policies": ["policy1", "policy2"], "sop_steps": ["step1", "step2"]}"""

                result, _ = await llm.complete(system_prompt, payload.message.text)
                import json
                import re
                match = re.search(r'\{[^{}]*\}', result, re.DOTALL)
                if match:
                    try:
                        parsed = json.loads(match.group())
                        policy_info["relevant_policies"] = parsed.get("policies", [])
                        policy_info["sop_steps"] = parsed.get("sop_steps", [])
                    except json.JSONDecodeError:
                        pass

            if not policy_info["relevant_policies"]:
                policy_info["relevant_policies"].append("General company policies apply")

        support_keywords = ["support", "case", "hỗ trợ", "vấn đề", "ticket", "issue"]
        if any(kw in text_lower for kw in support_keywords):
            if memory.case_memory:
                policy_info["relevant_policies"].append("Case handling procedures apply")
            if not policy_info["sop_steps"]:
                policy_info["sop_steps"] = [
                    "Acknowledge the case",
                    "Review case history",
                    "Provide resolution or escalate",
                ]

        if any(kw in text_lower for kw in ["escalate", "chuyển", "forward"]):
            policy_info["relevant_policies"].append("Escalation policy applies")
            policy_info["sop_steps"].append("Escalate to appropriate team")

        return policy_info


class KnowledgeAgent:
    async def retrieve(
        self,
        payload: InputPayload,
        memory: MemoryContext,
        llm: Optional[LLMClient] = None,
    ) -> dict:
        knowledge = {
            "facts": [],
            "patterns": [],
            "confidence": 0.5,
        }

        if memory.episodic_memory:
            knowledge["patterns"] = [
                item["content"] for item in memory.episodic_memory[:3]
            ]
            knowledge["confidence"] = 0.7

        text_lower = payload.message.text.lower()
        question_types = {
            "who": ["who", "ai là", "người nào"],
            "when": ["when", "khi nào", "thời gian", "lúc nào"],
            "where": ["where", "ở đâu", "địa điểm"],
            "what": ["what", "là gì", "cái gì", "gì"],
            "why": ["why", "tại sao", "vì sao"],
            "how": ["how", "như thế nào", "làm sao"],
        }

        for qtype, keywords in question_types.items():
            if any(kw in text_lower for kw in keywords):
                knowledge["facts"].append(f"{qtype.capitalize()} question detected")

        if llm and (knowledge["patterns"] or memory.conversation_summary):
            system_prompt = """You are a knowledge retrieval assistant. Based on the context provided, 
extract relevant facts and patterns that would help answer the user's question.
Return JSON format:
{"relevant_facts": ["fact1", "fact2"], "confidence": 0.0-1.0}"""

            context_str = f"Patterns: {knowledge['patterns']}\nSummary: {memory.conversation_summary}"
            result, conf = await llm.complete(system_prompt, f"Question: {payload.message.text}\nContext: {context_str}")

            import json
            import re
            match = re.search(r'\{[^{}]*\}', result, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                    knowledge["facts"] = parsed.get("relevant_facts", knowledge["facts"])
                    knowledge["confidence"] = parsed.get("confidence", conf)
                except json.JSONDecodeError:
                    pass

        return knowledge
