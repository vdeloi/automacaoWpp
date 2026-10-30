# app/core/logger.py
import logging
import sys
from contextvars import ContextVar

# ContextVars garantem isolamento de estado entre corrotinas concorrentes
ctx_tenant_id: ContextVar[str] = ContextVar("tenant_id", default="-")
ctx_phone: ContextVar[str] = ContextVar("phone_number", default="-")


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.tenant_id = ctx_tenant_id.get()
        record.phone = ctx_phone.get()
        return True


def setup_logger(name: str = "app") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [Tenant:%(tenant_id)s|User:%(phone)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        handler.addFilter(ContextFilter())
        logger.addHandler(handler)
    return logger


logger = setup_logger()