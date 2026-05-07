"""
Shared support detection utilities.
Consolidates duplicate support detection logic from supervisor.py and simple_agent.py.
"""

from typing import Optional

SUPPORT_KEYWORDS = [
    "support", "case", "ticket", "issue", "problem", "bug", "error", "crash",
    "not working", "broken", "help", "hỗ trợ", "vấn đề", "sự cố", "lỗi", "hỏng",
    "không được", "bị lỗi", "treo", "đơ", "cần giúp", "giúp tôi", "sửa", "fix",
    "login", "đăng nhập", "credential", "auth", "authentication", "publickey",
]

GIT_KEYWORDS = ["git", "github", "gitlab", "bitbucket", "ssh", "https", "credential", "publickey", "auth", "đăng nhập", "login"]
CREDENTIAL_KEYWORDS = ["mật khẩu", "password", "sso", "ldap", "vpn", "email", "outlook"]

GENERIC_REPLY_PHRASES = [
    "bạn cần tôi hỗ trợ gì",
    "vui lòng cho tôi biết yêu cầu của bạn",
    "bạn cần hỗ trợ gì",
    "mình có thể giúp gì",
    "tôi là trợ lý",
    "cho mình biết vấn đề",
    "bạn xác nhận",
    "nếu cần mình hỗ trợ",
]


def looks_like_support_request(message: str, conversation_state: Optional[dict] = None) -> bool:
    """Check if a message looks like a support request.

    Args:
        message: The user's message text
        conversation_state: Optional conversation state dict for message_mode check

    Returns:
        True if the message appears to be a support request
    """
    text = (message or "").lower()
    state = conversation_state or {}
    message_mode = (state.get("last_user_message_mode") or "").lower()
    if message_mode == "problem":
        return True
    return any(keyword in text for keyword in SUPPORT_KEYWORDS)


def build_support_clarification(message: str) -> str:
    """Build a clarification question for support requests.

    Uses keyword matching to provide targeted clarification questions
    for different categories of support requests.

    Args:
        message: The user's message text

    Returns:
        A clarification question string
    """
    text = (message or "").lower()
    if any(keyword in text for keyword in GIT_KEYWORDS):
        return (
            "Mình cần 3 thông tin để chẩn đoán nhanh: bạn đang dùng GitHub/GitLab/Bitbucket, "
            "đang login bằng HTTPS hay SSH, và nguyên lỗi hiển thị là gì?"
        )

    if any(keyword in text for keyword in CREDENTIAL_KEYWORDS):
        return (
            "Bạn cho mình biết hệ thống nào đang lỗi, bạn đang làm ở bước nào, và nguyên thông báo lỗi/mã lỗi là gì?"
        )

    return (
        "Bạn cho mình biết hệ thống/dịch vụ nào đang lỗi, bạn đang kẹt ở bước nào, và có mã lỗi hoặc ảnh chụp màn hình không?"
    )


def looks_generic_support_reply(answer: str) -> bool:
    """Check if an answer is a generic/polite opening rather than a substantive response.

    Args:
        answer: The generated answer text

    Returns:
        True if the answer looks like a generic support opening
    """
    text = (answer or "").lower()
    return any(phrase in text for phrase in GENERIC_REPLY_PHRASES)