import logging
import json
from typing import Dict, Any

# Create logger for InsightSQL diagnostics
logger = logging.getLogger("insightsql.diagnostics")
logger.setLevel(logging.INFO)

if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] [DIAGNOSTICS] %(message)s")
    ch.setFormatter(formatter)
    logger.addHandler(ch)

def _sanitize_item(val: Any) -> Any:
    if isinstance(val, str):
        val_lower = val.lower()
        if any(term in val_lower for term in ["gsk_", "sk-", "bearer ", "password", "secret", "private_key"]):
            return "[REDACTED]"
        return val
    elif isinstance(val, dict):
        sanitized_dict = {}
        for k, v in val.items():
            if any(term in str(k).lower() for term in ["key", "secret", "password", "token", "credential", "auth"]):
                sanitized_dict[k] = "[REDACTED]"
            else:
                sanitized_dict[k] = _sanitize_item(v)
        return sanitized_dict
    elif isinstance(val, list):
        return [_sanitize_item(x) for x in val]
    return val

def log_diagnostic_event(event: Dict[str, Any]) -> None:
    """
    Safely logs request diagnostics without leaking credentials or entire datasets.
    """
    sanitized = _sanitize_item(event)
    try:
        log_line = json.dumps(sanitized, default=str)
        logger.info(log_line)
    except Exception as e:
        logger.warning(f"Failed to serialize diagnostic event: {e}")
