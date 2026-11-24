import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Any

import structlog

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "app.log")

SENSITIVE_KEYS = {
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "password",
    "client_secret",
    "encryption_key",
    "authorization",
}


def censor_sensitive_data(logger: Any, method_name: str, event_dict: dict) -> dict:
    for key, value in list(event_dict.items()):
        if any(sens in key.lower() for sens in SENSITIVE_KEYS):
            event_dict[key] = "[REDACTED]"
        elif isinstance(value, dict):
            event_dict[key] = censor_sensitive_data(logger, method_name, value)
    return event_dict


def setup_logging(log_level: str = "INFO") -> None:
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    root_logger.handlers = []

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    root_logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(numeric_level)
    root_logger.addHandler(file_handler)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            censor_sensitive_data,
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "app") -> structlog.BoundLogger:
    return structlog.get_logger(name)
