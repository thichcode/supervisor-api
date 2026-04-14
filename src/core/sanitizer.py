import re
from typing import Optional
import structlog

logger = structlog.get_logger()

EMAIL_PATTERN = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
PHONE_PATTERN = re.compile(r'\+?[\d\s\-\(\)]{10,}')
CREDIT_CARD_PATTERN = re.compile(r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b')
SSN_PATTERN = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')
API_KEY_PATTERN = re.compile(r'[a-zA-Z0-9]{32,}')
PASSWORD_PATTERN = re.compile(r'(password|passwd|pwd)[\s:=]+\S+', re.IGNORECASE)


class InputSanitizer:
    MASK_CHAR = "*"

    @classmethod
    def mask_email(cls, text: str) -> str:
        def replace_email(match):
            email = match.group()
            parts = email.split('@')
            if len(parts[0]) > 2:
                masked = parts[0][0] + cls.MASK_CHAR * (len(parts[0]) - 2) + parts[0][-1]
            else:
                masked = cls.MASK_CHAR * len(parts[0])
            return f"{masked}@{parts[1]}"
        return EMAIL_PATTERN.sub(replace_email, text)

    @classmethod
    def mask_phone(cls, text: str) -> str:
        def replace_phone(match):
            phone = match.group()
            return cls.MASK_CHAR * len(phone)
        return PHONE_PATTERN.sub(replace_phone, text)

    @classmethod
    def mask_credit_card(cls, text: str) -> str:
        return CREDIT_CARD_PATTERN.sub(lambda m: cls.MASK_CHAR * 16, text)

    @classmethod
    def mask_ssn(cls, text: str) -> str:
        return SSN_PATTERN.sub(lambda m: cls.MASK_CHAR * 11, text)

    @classmethod
    def mask_api_key(cls, text: str) -> str:
        def replace_key(match):
            key = match.group()
            return key[:4] + cls.MASK_CHAR * (len(key) - 8) + key[-4:]
        return API_KEY_PATTERN.sub(replace_key, text)

    @classmethod
    def mask_passwords(cls, text: str) -> str:
        def replace_pwd(match):
            return match.group(1) + ": [REDACTED]"
        return PASSWORD_PATTERN.sub(replace_pwd, text)

    @classmethod
    def sanitize(cls, text: str, mask_sensitive: bool = True) -> str:
        if not mask_sensitive:
            return text.strip()

        sanitized = text.strip()
        sanitized = cls.mask_passwords(sanitized)
        
        if mask_sensitive:
            sanitized = cls.mask_credit_card(sanitized)
            sanitized = cls.mask_ssn(sanitized)
            sanitized = cls.mask_api_key(sanitized)

        sanitized = cls.mask_email(sanitized)
        sanitized = cls.mask_phone(sanitized)

        sanitized = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', sanitized)

        if len(sanitized) > 10000:
            sanitized = sanitized[:10000]
            logger.warning("Input truncated due to length", original_length=len(text))

        return sanitized

    @classmethod
    def validate_input(cls, text: str) -> tuple[bool, Optional[str]]:
        if not text:
            return False, "Input cannot be empty"
        
        if len(text) > 10000:
            return False, "Input exceeds maximum length"

        if text.count('\x00') > 0:
            return False, "Input contains null bytes"

        dangerous_patterns = [
            r'<script[^>]*>.*?</script>',
            r'javascript:',
            r'on\w+\s*=',
        ]
        for pattern in dangerous_patterns:
            if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
                return False, "Input contains potentially dangerous content"

        return True, None


sanitizer = InputSanitizer()
