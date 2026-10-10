import copy
import json

import pytest
from jsonschema import Draft7Validator

from app.taskpilot.execution_trace import (
    TRACE_EVENT_SCHEMA,
    TRACE_SCHEMA_VERSION,
    ExecutionTrace,
    parse_trace_event_json,
    validate_trace_event,
    validate_trace_events,
)


def started_trace():
    trace = ExecutionTrace()
    trace.record("task", "started", step=0, task_sha256="0" * 64, task_chars=0)
    return trace


def test_versioned_schema_rejects_missing_extra_and_duplicate_fields():
    Draft7Validator.check_schema(TRACE_EVENT_SCHEMA)
    event = started_trace().events[0]
    assert event["schema_version"] == TRACE_SCHEMA_VERSION
    validate_trace_event(event)

    missing = {key: value for key, value in event.items() if key != "task_id"}
    with pytest.raises(ValueError, match="Invalid trace event"):
        validate_trace_event(missing)

    extra = {**event, "raw_arguments": "should never be logged"}
    with pytest.raises(ValueError, match="Invalid trace event"):
        validate_trace_event(extra)

    serialized = json.dumps(event)
    duplicated = serialized.replace(
        '"schema_version":', '"schema_version": "old", "schema_version":', 1
    )
    with pytest.raises(ValueError, match="Duplicate trace field"):
        parse_trace_event_json(duplicated)


def test_ids_are_stable_within_a_run_and_scoped_across_runs():
    first = started_trace()
    first.record("step", "started", step=1)
    first.record(
        "llm_selection", "success", step=1, call_id="provider-call", tool="probe"
    )
    first.record("validation", "success", step=1, call_id="provider-call", tool="probe")
    first.record(
        "tool_execution",
        "started",
        step=1,
        call_id="provider-call",
        tool="probe",
        attempt=1,
    )
    first.record(
        "tool_execution",
        "failure",
        step=1,
        call_id="provider-call",
        tool="probe",
        attempt=1,
    )
    first.record(
        "retry",
        "started",
        step=1,
        call_id="provider-call",
        tool="probe",
        attempt=2,
        classification="transient",
    )
    first.record(
        "tool_execution",
        "started",
        step=1,
        call_id="provider-call",
        tool="probe",
        attempt=2,
    )
    first.record(
        "tool_execution",
        "success",
        step=1,
        call_id="provider-call",
        tool="probe",
        attempt=2,
    )
    first.record(
        "observation", "success", step=1, call_id="provider-call", tool="probe"
    )
    first.record("step", "success", step=1)
    first.record(
        "finish",
        "success",
        step=1,
        finish_source="agent_declaration",
        business_outcome="not_verified",
    )

    events = first.events
    validate_trace_events(events)
    assert first.task_id != first.run_id
    assert len({item["event_id"] for item in events}) == len(events)
    assert {
        item["step_id"] for item in events if item["event"] not in {"task", "finish"}
    } == {f"{first.run_id}:step:1"}
    call_events = [item for item in events if item.get("tool_call_id")]
    assert {item["tool_call_id"] for item in call_events} == {
        first.call_ref("provider-call")
    }
    assert {item["attempt_id"] for item in call_events if item.get("attempt") == 1} == {
        f"{first.call_ref('provider-call')}:attempt:1"
    }
    assert {item["attempt_id"] for item in call_events if item.get("attempt") == 2} == {
        f"{first.call_ref('provider-call')}:attempt:2"
    }
    assert all("provider-call" not in json.dumps(item) for item in events)

    second = started_trace()
    assert first.task_id != second.task_id
    assert first.run_id != second.run_id
    assert first.call_ref("provider-call") != second.call_ref("provider-call")


def test_schema_rejects_inconsistent_and_duplicate_ids():
    trace = started_trace()
    trace.record("step", "started", step=1)
    trace.record("tool_execution", "started", step=1, call_id="call-1", attempt=1)
    for field, value in (
        ("step_id", "wrong"),
        ("attempt_id", "wrong"),
        ("event_id", "wrong"),
    ):
        damaged = {**trace.events[-1], field: value}
        with pytest.raises(ValueError, match="Invalid trace event"):
            validate_trace_event(damaged)

    duplicated = copy.deepcopy(trace.events)
    duplicated.append(copy.deepcopy(duplicated[-1]))
    with pytest.raises(ValueError, match="Duplicate trace event"):
        validate_trace_events(duplicated)

    with pytest.raises(ValueError, match="Invalid trace event"):
        trace.record("retry", "started", step=1, call_id="call-1", attempt=1)
    with pytest.raises(ValueError, match="Invalid trace event"):
        trace.record("tool_execution", "started", step=1, attempt=2)
    with pytest.raises(ValueError, match="Invalid trace event"):
        trace.record("fallback", "started", step=1)


def test_finish_declaration_does_not_claim_business_success():
    trace = started_trace()
    trace.record("step", "started", step=1)
    trace.record(
        "tool_execution",
        "started",
        step=1,
        call_id="failed-call",
        tool="probe",
        attempt=1,
    )
    trace.record(
        "tool_execution",
        "failure",
        step=1,
        call_id="failed-call",
        tool="probe",
        attempt=1,
    )
    trace.record("step", "failure", step=1)
    trace.record(
        "finish",
        "success",
        step=1,
        finish_source="agent_declaration",
        business_outcome="not_verified",
    )
    finish = trace.events[-1]
    assert finish["status"] == "success"
    assert finish["finish_source"] == "agent_declaration"
    assert finish["business_outcome"] == "not_verified"
    assert any(
        item["event"] == "tool_execution" and item["status"] == "failure"
        for item in trace.events
    )
    validate_trace_events(trace.events)


def test_full_trace_rejects_missing_step_or_attempt_start_and_reused_call_identity():
    no_step = started_trace()
    no_step.record("validation", "success", step=1, call_id="call-a", tool="probe")
    with pytest.raises(ValueError, match="Invalid trace chain"):
        validate_trace_events(no_step.events)

    no_attempt = started_trace()
    no_attempt.record("step", "started", step=1)
    no_attempt.record(
        "tool_execution", "failure", step=1, call_id="call-a", tool="probe", attempt=1
    )
    with pytest.raises(ValueError, match="Invalid trace chain"):
        validate_trace_events(no_attempt.events)

    no_retry = started_trace()
    no_retry.record("step", "started", step=1)
    no_retry.record(
        "tool_execution", "started", step=1, call_id="call-a", tool="probe", attempt=2
    )
    with pytest.raises(ValueError, match="Invalid trace chain"):
        validate_trace_events(no_retry.events)

    reused = started_trace()
    reused.record("step", "started", step=1)
    reused.record("llm_selection", "success", step=1, call_id="call-a", tool="probe")
    reused.record("llm_selection", "success", step=1, call_id="call-a", tool="probe")
    with pytest.raises(ValueError, match="Duplicate trace event"):
        validate_trace_events(reused.events)

    mismatched_tool = started_trace()
    mismatched_tool.record("step", "started", step=1)
    mismatched_tool.record(
        "llm_selection", "success", step=1, call_id="call-a", tool="probe"
    )
    mismatched_tool.record(
        "validation", "success", step=1, call_id="call-a", tool="other"
    )
    with pytest.raises(ValueError, match="Invalid trace chain"):
        validate_trace_events(mismatched_tool.events)
