"""Small, task-scoped execution trace with allowlisted, redacted event fields."""

import hashlib
import json
from typing import Any
from uuid import uuid4

from app.logger import logger


_SAFE_DETAIL_KEYS = {
    "task_sha256",
    "task_chars",
    "candidate_count",
    "selected_count",
    "k_business",
    "k_total",
    "attempt",
    "http_status",
    "missing_count",
    "reason",
    "classification",
    "error_kind",
}
_SAFE_TEXT_VALUES = {
    "invalid_json",
    "invalid_arguments",
    "unknown_tool",
    "duplicate_call",
    "context_conflict",
    "budget_exhausted",
    "required_missing",
    "schema_rejected",
    "index_error",
    "no_match",
    "missing_task",
    "no_business_tool",
    "transient",
    "permanent",
    "success",
    "failure",
    "unknown",
    "timeout",
    "cancelled",
}


class ExecutionTrace:
    """Retains one run's safe event sequence and emits it through Loguru."""

    def __init__(self) -> None:
        self.run_id = uuid4().hex
        self.events: list[dict[str, Any]] = []

    @staticmethod
    def call_ref(call_id: str) -> str:
        return "sha256:" + hashlib.sha256(call_id.encode("utf-8")).hexdigest()

    def record(
        self,
        event: str,
        status: str,
        *,
        step: int,
        call_id: str | None = None,
        tool: str | None = None,
        **details: Any,
    ) -> None:
        if unknown := set(details) - _SAFE_DETAIL_KEYS:
            raise ValueError(f"Unsupported trace fields: {sorted(unknown)}")
        for value in details.values():
            if (
                isinstance(value, str)
                and value not in _SAFE_TEXT_VALUES
                and not (
                    len(value) == 64
                    and all(char in "0123456789abcdef" for char in value)
                )
            ):
                raise ValueError("Unsupported trace field value")
        item = {
            "seq": len(self.events) + 1,
            "run_id": self.run_id,
            "step": step,
            "event": event,
            "status": status,
        }
        if call_id is not None:
            item["call_ref"] = self.call_ref(call_id)
        if tool is not None:
            item["tool"] = tool
        item.update(details)
        self.events.append(item)
        logger.info("execution_trace_event " + json.dumps(item, sort_keys=True))
