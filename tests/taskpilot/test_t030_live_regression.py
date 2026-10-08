from app.schema import Function, Message, ToolCall
from eval.t030_live_regression import termination_status


def test_completion_requires_matching_terminate_observation():
    call = ToolCall(
        id="call-1",
        function=Function(name="terminate", arguments='{"status":"success"}'),
    )
    selected = Message.from_tool_calls([call])
    unrelated = Message.tool_message(
        "The interaction has been completed with status: success",
        "terminate",
        "call-2",
    )
    assert termination_status([selected]) is None
    assert termination_status([selected, unrelated]) is None

    completed = Message.tool_message(
        "Observed output of cmd terminate: The interaction has been completed with status: success",
        "terminate",
        "call-1",
    )
    assert termination_status([selected, completed]) == "success"


def test_completion_keeps_failure_status():
    call = ToolCall(
        id="call-1",
        function=Function(name="terminate", arguments='{"status":"failure"}'),
    )
    selected = Message.from_tool_calls([call])
    failed = Message.tool_message(
        "Observed output of cmd terminate: The interaction has been completed with status: failure",
        "terminate",
        "call-1",
    )
    assert termination_status([selected, failed]) == "failure"
