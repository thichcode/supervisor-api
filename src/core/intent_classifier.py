from src.core import InputPayload, IntentClassification, IntentType
from src.memory import MemoryContext
import re


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

    def classify(self, payload: InputPayload, memory: MemoryContext) -> IntentClassification:
        text = payload.message.text.lower()

        if payload.case and payload.case.case_id:
            return IntentClassification(intent=IntentType.SUPPORT_CASE, confidence=0.85)

        scores = {}
        for intent, patterns in self.PATTERNS.items():
            score = 0
            for pattern in patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    score += 1
            scores[intent] = score

        # VIP boost
        if payload.user.vip_flag:
            scores[IntentType.EXECUTIVE_REQUEST] += 2

        # Case presence boost
        if payload.case and payload.case.case_id:
            scores[IntentType.SUPPORT_CASE] += 1

        # Memory context boost
        if memory.case_memory:
            scores[IntentType.SUPPORT_CASE] += 1

        # Role-based context boost
        user_role = memory.user_profile.get("role", "").lower() if memory.user_profile else ""
        user_team = memory.user_profile.get("team", "").lower() if memory.user_profile else ""
        
        # Project manager - boost analysis
        if "manager" in user_role or "project" in user_team:
            scores[IntentType.ANALYSIS] = scores.get(IntentType.ANALYSIS, 0) + 1
        
        # HR - boost policy
        if "hr" in user_team or "nhân" in user_role:
            scores[IntentType.POLICY] = scores.get(IntentType.POLICY, 0) + 1
        
        # IT support - boost support case
        if "it" in user_team or "support" in user_role:
            scores[IntentType.SUPPORT_CASE] = scores.get(IntentType.SUPPORT_CASE, 0) + 1

        # Policy keywords boost
        if scores.get(IntentType.POLICY, 0) > 0:
            scores[IntentType.POLICY] += 0.5

        # Support case keywords boost
        if scores.get(IntentType.SUPPORT_CASE, 0) > 0:
            scores[IntentType.SUPPORT_CASE] += 0.5

        # Default to FAQ if no match
        if not any(scores.values()):
            scores[IntentType.FAQ] = 0.5

        max_intent = max(scores, key=scores.get)
        max_score = scores[max_intent]

        if max_score == 0:
            confidence = 0.5
        else:
            confidence = min(0.95, 0.6 + (max_score * 0.1))

        return IntentClassification(intent=max_intent, confidence=confidence)
