"""Small, task-scoped execution trace with allowlisted, redacted event fields."""

import hashlib
import json
from typing import Any
from uuid import uuid4

from jsonschema import Draft7Validator

from app.logger import logger


TRACE_SCHEMA_VERSION = "1.0"
_REASONS = {
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
}

_EVENT_STATUSES = {
    "task": {"started"},
    "step": {"started", "success", "failure", "unknown", "cancelled"},
    "routing": {"success", "failure", "unknown"},
    "llm": {"started", "success", "failure"},
    "llm_selection": {"success"},
    "validation": {"success", "failure", "pending"},
    "clarification": {"requested", "merged", "pending", "cancelled", "unknown"},
    "tool_execution": {"started", "success", "failure", "unknown", "cancelled"},
    "retry": {"started"},
    "observation": {"success", "failure", "unknown", "cancelled", "pending"},
    "finish": {"success", "failure", "unknown", "cancelled"},
}
_CALL_EVENTS = {
    "llm_selection",
    "validation",
    "clarification",
    "tool_execution",
    "retry",
    "observation",
}

TRACE_EVENT_SCHEMA: dict[str, Any] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "task_id",
        "run_id",
        "event_id",
        "seq",
        "step",
        "event",
        "status",
    ],
    "properties": {
        "schema_version": {"const": TRACE_SCHEMA_VERSION},
        "task_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
        "run_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
        "event_id": {"type": "string", "pattern": "^[0-9a-f]{32}:[1-9][0-9]*$"},
        "seq": {"type": "integer", "minimum": 1},
        "step": {"type": "integer", "minimum": 0},
        "step_id": {"type": "string", "pattern": "^[0-9a-f]{32}:step:[1-9][0-9]*$"},
        "event": {"enum": list(_EVENT_STATUSES)},
        "status": {"type": "string"},
        "tool_call_id": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "attempt": {"type": "integer", "minimum": 1},
        "attempt_id": {
            "type": "string",
            "pattern": "^sha256:[0-9a-f]{64}:attempt:[1-9][0-9]*$",
        },
        "tool": {"type": "string", "maxLength": 128},
        "task_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "task_chars": {"type": "integer", "minimum": 0},
        "candidate_count": {"type": "integer", "minimum": 0},
        "selected_count": {"type": "integer", "minimum": 0},
        "k_business": {"type": "integer", "minimum": 0},
        "k_total": {"type": "integer", "minimum": 0},
        "http_status": {"type": "integer", "minimum": 100, "maximum": 599},
        "missing_count": {"type": "integer", "minimum": 0},
        "reason": {"enum": sorted(_REASONS)},
        "classification": {
            "enum": ["transient", "permanent", "success", "failure", "unknown"]
        },
        "error_kind": {"enum": ["timeout", "cancelled"]},
        "finish_source": {"enum": ["agent_declaration", "runtime_exit"]},
        "business_outcome": {"const": "not_verified"},
    },
}
_VALIDATOR = Draft7Validator(TRACE_EVENT_SCHEMA)


def validate_trace_event(item: dict[str, Any]) -> None:
    """Validate one event, including derived IDs and event-specific fields."""
    if not isinstance(item, dict) or next(_VALIDATOR.iter_errors(item), None):
        raise ValueError("Invalid trace event")
    event, step = item["event"], item["step"]
    if (
        item["status"] not in _EVENT_STATUSES[event]
        or item["event_id"] != f"{item['run_id']}:{item['seq']}"
    ):
        raise ValueError("Invalid trace event")
    if event == "task":
        if step != 0 or "step_id" in item:
            raise ValueError("Invalid trace event")
    elif event == "finish":
        if (
            "step_id" in item
            or not {"finish_source", "business_outcome"} <= item.keys()
        ):
            raise ValueError("Invalid trace event")
    elif step < 1 or item.get("step_id") != f"{item['run_id']}:step:{step}":
        raise ValueError("Invalid trace event")
    if event != "finish" and ("finish_source" in item or "business_outcome" in item):
        raise ValueError("Invalid trace event")
    if (event in _CALL_EVENTS) != ("tool_call_id" in item):
        raise ValueError("Invalid trace event")
    if "attempt" in item:
        if event not in {"tool_execution", "retry"} or item.get("attempt_id") != (
            f"{item.get('tool_call_id')}:attempt:{item['attempt']}"
        ):
            raise ValueError("Invalid trace event")
    elif "attempt_id" in item or event == "retry":
        raise ValueError("Invalid trace event")
    if event == "retry" and (
        item["attempt"] < 2 or item.get("classification") != "transient"
    ):
        raise ValueError("Invalid trace event")


def validate_trace_events(events: list[dict[str, Any]]) -> None:
    """Validate one run's ordering and unique step/attempt starts."""
    if not events:
        return
    validate_trace_event(events[0])
    task_id, run_id = events[0].get("task_id"), events[0].get("run_id")
    step_starts: set[str] = set()
    attempt_starts: set[str] = set()
    attempt_results: dict[str, str] = {}
    retry_starts: set[str] = set()
    selected_calls: set[str] = set()
    call_tools: dict[str, str] = {}
    for seq, item in enumerate(events, 1):
        validate_trace_event(item)
        if (
            item["task_id"] != task_id
            or item["run_id"] != run_id
            or item["seq"] != seq
            or (seq == 1) != (item["event"] == "task")
            or (seq < len(events) and item["event"] == "finish")
        ):
            raise ValueError("Duplicate trace event or inconsistent run")
        if item["event"] == "step" and item["status"] == "started":
            if item["step_id"] in step_starts:
                raise ValueError("Duplicate trace event")
            step_starts.add(item["step_id"])
        elif "step_id" in item and item["step_id"] not in step_starts:
            raise ValueError("Invalid trace chain")
        call_id = item.get("tool_call_id")
        if call_id and "tool" in item:
            if call_id in call_tools and call_tools[call_id] != item["tool"]:
                raise ValueError("Invalid trace chain")
            call_tools[call_id] = item["tool"]
        if item["event"] == "llm_selection":
            if call_id in selected_calls:
                raise ValueError("Duplicate trace event")
            selected_calls.add(call_id)
        if item["event"] == "tool_execution" and "attempt_id" in item:
            if item["status"] == "started":
                if item["attempt_id"] in attempt_starts:
                    raise ValueError("Duplicate trace event")
                if item["attempt"] > 1 and item["attempt_id"] not in retry_starts:
                    raise ValueError("Invalid trace chain")
                attempt_starts.add(item["attempt_id"])
            elif item["attempt_id"] not in attempt_starts:
                raise ValueError("Invalid trace chain")
            else:
                attempt_results[item["attempt_id"]] = item["status"]
        if item["event"] == "retry":
            if item["attempt_id"] in retry_starts:
                raise ValueError("Duplicate trace event")
            previous_id = f"{call_id}:attempt:{item['attempt'] - 1}"
            if attempt_results.get(previous_id) != "failure":
                raise ValueError("Invalid trace chain")
            retry_starts.add(item["attempt_id"])


def parse_trace_event_json(raw: str) -> dict[str, Any]:
    """Read an event without silently overwriting duplicate JSON fields."""

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        item: dict[str, Any] = {}
        for key, value in pairs:
            if key in item:
                raise ValueError("Duplicate trace field")
            item[key] = value
        return item

    item = json.loads(raw, object_pairs_hook=unique_pairs)
    validate_trace_event(item)
    return item


class ExecutionTrace:
    """One task has one run today; IDs remain distinct for later run lifecycles.

    task_id names the task; run_id names this Agent.run invocation. step_id is
    run_id plus the Agent step number. tool_call_id is a run-scoped digest of
    the original provider ID, and attempt_id adds the attempt number to it.
    Raw provider IDs remain in tool messages, never in emitted trace events.
    """

    def __init__(self) -> None:
        self.task_id = uuid4().hex
        self.run_id = uuid4().hex
        self.events: list[dict[str, Any]] = []

    def call_ref(self, call_id: str) -> str:
        """Run-scoped redacted form of the original provider tool_call_id."""
        if not call_id:
            raise ValueError("Invalid trace event")
        return (
            "sha256:"
            + hashlib.sha256(f"{self.run_id}:{call_id}".encode("utf-8")).hexdigest()
        )

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
        if (not self.events and event != "task") or (
            self.events and self.events[-1]["event"] == "finish"
        ):
            raise ValueError("Invalid trace event")
        if set(details) & {
            "schema_version",
            "task_id",
            "run_id",
            "event_id",
            "seq",
            "step",
            "step_id",
            "event",
            "status",
            "tool_call_id",
            "attempt_id",
            "tool",
        }:
            raise ValueError("Invalid trace event")
        seq = len(self.events) + 1
        item = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "event_id": f"{self.run_id}:{seq}",
            "seq": seq,
            "step": step,
            "event": event,
            "status": status,
        }
        if step > 0 and event not in {"task", "finish"}:
            item["step_id"] = f"{self.run_id}:step:{step}"
        if call_id is not None:
            item["tool_call_id"] = self.call_ref(call_id)
        if "attempt" in details and call_id is not None:
            item["attempt_id"] = f"{item['tool_call_id']}:attempt:{details['attempt']}"
        if tool is not None:
            item["tool"] = tool
        item.update(details)
        validate_trace_event(item)
        self.events.append(item)
        logger.info("execution_trace_event " + json.dumps(item, sort_keys=True))
