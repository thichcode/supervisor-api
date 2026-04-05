import logging
import sys
import json
import structlog
from datetime import datetime
from typing import Any
from pythonjsonlogger import jsonlogger

from src.config import get_settings

settings = get_settings()


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record: dict, record: logging.Record, message_dict: dict):
        super().add_fields(log_record, record, message_dict)
        log_record['timestamp'] = datetime.utcnow().isoformat()
        log_record['level'] = record.levelname
        log_record['logger'] = record.name
        log_record['service'] = 'supervisor'


def setup_logging():
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    
    formatter = CustomJsonFormatter(
        '%(timestamp)s %(level)s %(name)s %(message)s'
    )
    handler.setFormatter(formatter)
    
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    for logger_name in ['uvicorn', 'uvicorn.error', 'uvicorn.access']:
        uv_logger = logging.getLogger(logger_name)
        uv_logger.handlers.clear()
        uv_logger.addHandler(handler)
        uv_logger.setLevel(log_level)

    return structlog.get_logger()


class RequestLogger:
    def __init__(self, request_id: str):
        self.request_id = request_id
        self.logger = structlog.get_logger()

    def log_request_received(self, data: dict):
        self.logger.info(
            "Request received",
            request_id=self.request_id,
            user_id=data.get('user', {}).get('id'),
            thread_id=data.get('conversation', {}).get('thread_id'),
        )

    def log_decision(self, decision: str, intent: str, risk: str, agents: list):
        self.logger.info(
            "Decision made",
            request_id=self.request_id,
            decision=decision,
            intent=intent,
            risk_level=risk,
            agents_used=agents,
        )

    def log_response_sent(self, status: str, confidence: float, duration_ms: int):
        self.logger.info(
            "Response sent",
            request_id=self.request_id,
            status=status,
            confidence=confidence,
            duration_ms=duration_ms,
        )

    def log_error(self, error: str, error_type: str):
        self.logger.error(
            "Error occurred",
            request_id=self.request_id,
            error=error,
            error_type=error_type,
        )


setup_logging()
