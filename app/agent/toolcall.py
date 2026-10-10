import asyncio
import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, List, Optional, Union
from uuid import uuid4

from pydantic import Field, PrivateAttr

from app.agent.react import ReActAgent
from app.exceptions import TokenLimitExceeded
from app.logger import logger
from app.prompt.toolcall import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.schema import TOOL_CHOICE_TYPE, AgentState, Message, ToolCall, ToolChoice
from app.taskpilot.execution_trace import ExecutionTrace
from app.taskpilot.semantic_tool_retrieval import SemanticToolRetriever
from app.taskpilot.tool_embedding_index import (
    EmbeddingIndexError,
    InMemoryToolIndex,
    LocalTransformerEmbeddingBackend,
)
from app.tool import CreateChatCompletion, Terminate, ToolCollection
from app.tool.ask_human import AskHuman
from app.tool.base import ToolFailure, ToolResult, normalize_tool_result
from app.tool.tool_collection import (
    MissingParameterFailure,
    ToolValidationFailure,
    tool_event_scope,
)


TOOL_CALL_REQUIRED = "Tool calls required but none provided"
MAX_CLARIFICATION_ATTEMPTS = 3


def _reject_non_json_constant(value: str):
    raise ValueError(f"Invalid JSON constant: {value}")


def _reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate clarification field: {key}")
        result[key] = value
    return result


def _has_argument(arguments: dict[str, Any], field: str) -> bool:
    if field in arguments:
        return True
    value = arguments
    for part in field.split("."):
        if not isinstance(value, dict) or part not in value:
            return False
        value = value[part]
    return True


def _schema_sha256(schema: Any) -> Optional[str]:
    try:
        encoded = json.dumps(
            schema,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class PendingToolCall:
    tool_call_id: str
    tool_name: str
    original_arguments_json: str
    original_arguments: dict[str, Any]
    arguments: dict[str, Any]
    sources: dict[str, str]
    missing_fields: list[str]
    in_flight: bool = False


class ToolCallAgent(ReActAgent):
    """Base agent class for handling tool/function calls with enhanced abstraction"""

    name: str = "toolcall"
    description: str = "an agent that can execute tool calls."

    system_prompt: str = SYSTEM_PROMPT
    next_step_prompt: str = NEXT_STEP_PROMPT

    available_tools: ToolCollection = ToolCollection(
        CreateChatCompletion(), Terminate()
    )
    tool_choices: TOOL_CHOICE_TYPE = ToolChoice.AUTO  # type: ignore
    special_tool_names: List[str] = Field(default_factory=lambda: [Terminate().name])

    tool_calls: List[ToolCall] = Field(default_factory=list)
    routing_top_k: Optional[int] = Field(default=None, gt=0)
    routing_retriever: Optional[SemanticToolRetriever] = Field(
        default=None, exclude=True, repr=False
    )
    routing_original_task: Optional[str] = Field(default=None, exclude=True, repr=False)
    routing_clarification: Optional[str] = Field(default=None, exclude=True, repr=False)
    routing_run_id: Optional[str] = Field(default=None, exclude=True, repr=False)
    pending_tool_calls: dict[str, PendingToolCall] = Field(
        default_factory=dict, exclude=True, repr=False
    )
    closed_pending_call_ids: set[str] = Field(
        default_factory=set, exclude=True, repr=False
    )
    tool_call_sources: dict[str, dict[str, str]] = Field(
        default_factory=dict, exclude=True, repr=False
    )
    execution_trace: Optional[ExecutionTrace] = Field(
        default=None, exclude=True, repr=False
    )
    trace_seen_call_ids: set[str] = Field(default_factory=set, exclude=True, repr=False)
    _current_base64_image: Optional[str] = None

    max_steps: int = 30
    max_observe: Optional[Union[int, bool]] = None
    run_timeout_seconds: float = Field(default=300.0, gt=0)
    cleanup_timeout_seconds: float = Field(default=10.0, gt=0)
    _run_deadline: Optional[float] = PrivateAttr(default=None)
    _trace_final_status: Optional[str] = PrivateAttr(default=None)
    _trace_finish_source: str = PrivateAttr(default="runtime_exit")
    _trace_step_status: str = PrivateAttr(default="success")

    def _trace_event(
        self,
        event: str,
        status: str,
        *,
        call_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        **details: Any,
    ) -> None:
        if self.execution_trace is None:
            return
        self.execution_trace.record(
            event,
            status,
            step=0 if event == "task" else self.current_step,
            call_id=call_id,
            tool=self._routing_tool_label(tool_name) if tool_name else None,
            **details,
        )
        if event == "observation" and status in {"failure", "unknown", "cancelled"}:
            self._trace_step_status = status

    def _log_routing_event(self, event: dict[str, Any]) -> None:
        logger.info(
            "tool_routing_event "
            + json.dumps(
                event, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        )
        selection = event["selection_status"]
        self._trace_event(
            "routing",
            "failure"
            if selection in {"llm_error", "no_response"}
            else "success"
            if selection == "selected"
            else "unknown",
            candidate_count=len(event["candidates"]),
            selected_count=len(event["selected_tools"]),
            k_business=event["k_business"],
            k_total=event["k_total"],
            **(
                {"reason": event["candidate_status"]}
                if event["candidate_status"] != "ready"
                else {}
            ),
        )

    def _routing_tool_label(self, name: str) -> str:
        tool = self.available_tools.get_tool(name)
        identity = tool.metadata.identity if tool else name
        if tool is None or tool.metadata.source == "mcp":
            source = "mcp" if tool else "unknown"
            return f"{source}:sha256:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"
        return name

    async def think(self) -> bool:
        """Process current state and decide next actions using tools"""
        if self._remaining_run_seconds() == 0:
            self._trace_step_status = "unknown"
            self.routing_clarification = (
                "Status: unknown\nError: Agent execution budget exhausted; "
                "no new tool calls started."
            )
            self.state = AgentState.FINISHED
            return True
        self.routing_clarification = None
        routing_event = None
        if self.next_step_prompt:
            user_msg = Message.user_message(self.next_step_prompt)
            self.messages += [user_msg]

        tool_schemas = (
            self.available_tools.to_params() if self.routing_top_k is None else []
        )
        if self.routing_top_k is not None:
            if self.routing_run_id is None:
                self.routing_run_id = uuid4().hex
            retrieval = None
            selected = []
            candidate_status = "ready"
            # K limits business schemas; required controls are appended separately.
            controls = [
                tool
                for tool in self.available_tools
                if type(tool) is Terminate
                or (self.pending_tool_calls and type(tool) is AskHuman)
            ]
            business_tools = [
                tool
                for tool in self.available_tools
                if not isinstance(tool, (Terminate, AskHuman))
                and not self._is_special_tool(tool.name)
            ]
            if not (self.routing_original_task or "").strip():
                candidate_status = "missing_task"
                self.routing_clarification = (
                    "No suitable tool candidate is available for this step. "
                    "Please clarify the request and retry."
                )
            elif business_tools:
                if self.routing_retriever is None:
                    self.routing_retriever = SemanticToolRetriever(
                        InMemoryToolIndex(LocalTransformerEmbeddingBackend())
                    )
                try:
                    retrieval = await self.routing_retriever.retrieve(
                        self.routing_original_task,
                        self.messages,
                        ToolCollection(*business_tools),
                    )
                except EmbeddingIndexError:
                    candidate_status = "index_error"
                    self.routing_clarification = "Tool retrieval is unavailable. Please retry or clarify the request."
                else:
                    selected = [
                        match.name for match in retrieval.matches if match.score > 0
                    ][: self.routing_top_k]
                    tool_schemas = [
                        self.available_tools.get_tool(name).to_param()
                        for name in selected
                    ]
                    if not tool_schemas:
                        candidate_status = "no_match"
                        if not self.pending_tool_calls or not controls:
                            self.routing_clarification = (
                                "No suitable tool candidate matched this step. "
                                "Please clarify the request and retry."
                            )
            if not business_tools and not controls:
                candidate_status = "no_business_tool"
                self.routing_clarification = (
                    "No suitable tool candidate is available for this step. "
                    "Please clarify the request and retry."
                )
            elif not business_tools:
                candidate_status = "no_business_tool"
            if not self.routing_clarification:
                tool_schemas += [tool.to_param() for tool in controls]
            query = retrieval.query if retrieval else (self.routing_original_task or "")
            version = retrieval.index_version if retrieval else None
            exposed_names = {schema["function"]["name"] for schema in tool_schemas}
            routing_event = {
                "event": "tool_routing",
                "version": 1,
                "run_id": self.routing_run_id,
                "step": self.current_step,
                "query": {
                    "sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
                    "chars": len(query),
                    "summary": (
                        "task_and_observation"
                        if retrieval and retrieval.observation_tool_call_id
                        else "task_only"
                        if query
                        else "missing_task"
                    ),
                    "observation_tool_call_id": (
                        retrieval.observation_tool_call_id if retrieval else None
                    ),
                },
                "candidates": [
                    {
                        "name": self._routing_tool_label(match.name),
                        "identity": (
                            self._routing_tool_label(match.name)
                            if self.available_tools.get_tool(match.name).metadata.source
                            == "mcp"
                            else match.identity
                        ),
                        "source": self.available_tools.get_tool(
                            match.name
                        ).metadata.source,
                        "score": match.score,
                        "exposed": match.name in exposed_names,
                        "schema_sha256": _schema_sha256(
                            self.available_tools.get_tool(match.name).parameters
                        ),
                    }
                    for match in (retrieval.matches if retrieval else [])
                ],
                "exposed_tools": [
                    {
                        "name": self._routing_tool_label(name),
                        "identity": (
                            self._routing_tool_label(name)
                            if self.available_tools.get_tool(name).metadata.source
                            == "mcp"
                            else self.available_tools.get_tool(name).metadata.identity
                        ),
                        "source": self.available_tools.get_tool(name).metadata.source,
                        "schema_sha256": _schema_sha256(
                            self.available_tools.get_tool(name).parameters
                        ),
                    }
                    for name in (schema["function"]["name"] for schema in tool_schemas)
                ],
                "selected_tools": [],
                "k_business": len(selected),
                "k_total": len(tool_schemas),
                "index_version": (
                    {
                        "model_id": version.model_id,
                        "model_revision": version.model_revision,
                        "vector_version": version.vector_version,
                        "content_fingerprint": version.content_fingerprint,
                    }
                    if version
                    else None
                ),
                "metadata_version": version.content_fingerprint if version else None,
                "candidate_status": candidate_status,
                "selection_status": "not_called",
            }
            if self.routing_clarification:
                self._trace_step_status = "unknown"
                self._log_routing_event(routing_event)
                self.tool_calls = []
                self.memory.add_message(
                    Message.assistant_message(self.routing_clarification)
                )
                self.state = AgentState.FINISHED
                return True

        self._trace_event("llm", "started")
        try:
            # Get response with tool options
            response = await self.llm.ask_tool(
                messages=self.messages,
                system_msgs=(
                    [Message.system_message(self.system_prompt)]
                    if self.system_prompt
                    else None
                ),
                tools=tool_schemas,
                tool_choice=self.tool_choices,
            )
        except ValueError:
            self._trace_event("llm", "failure")
            if routing_event is not None:
                routing_event["selection_status"] = "llm_error"
                self._log_routing_event(routing_event)
            raise
        except Exception as e:
            self._trace_event("llm", "failure")
            if routing_event is not None:
                routing_event["selection_status"] = "llm_error"
                self._log_routing_event(routing_event)
            # Check if this is a RetryError containing TokenLimitExceeded
            if hasattr(e, "__cause__") and isinstance(e.__cause__, TokenLimitExceeded):
                token_limit_error = e.__cause__
                logger.error("Token limit error from model request")
                self.memory.add_message(
                    Message.assistant_message(
                        f"Maximum token limit reached, cannot continue execution: {str(token_limit_error)}"
                    )
                )
                self.state = AgentState.FINISHED
                return False
            raise

        self.tool_calls = tool_calls = (
            response.tool_calls if response and response.tool_calls else []
        )
        content = response.content if response and response.content else ""
        if self.execution_trace is not None:
            call_ids = [selected_call.id for selected_call in tool_calls]
            if any(
                not call_id or call_id in self.trace_seen_call_ids
                for call_id in call_ids
            ) or len(call_ids) != len(set(call_ids)):
                self._trace_event("llm", "failure", reason="duplicate_call")
                raise ValueError("Missing or duplicate tool call ID")
            self.trace_seen_call_ids.update(call_ids)
        self._trace_event(
            "llm",
            "success" if response is not None else "failure",
            selected_count=len(tool_calls),
        )
        for selected_call in tool_calls:
            self._trace_event(
                "llm_selection",
                "success",
                call_id=selected_call.id,
                tool_name=selected_call.function.name,
            )

        # Log response info
        if routing_event is not None:
            routing_event["selected_tools"] = [
                {
                    "name": self._routing_tool_label(call.function.name),
                    "tool_call_id": call.id,
                }
                for call in tool_calls
            ]
            if response is None:
                routing_event["selection_status"] = "no_response"
            elif tool_calls:
                routing_event["selection_status"] = "selected"
            else:
                routing_event["selection_status"] = "not_selected"
            self._log_routing_event(routing_event)
        else:
            logger.info(f"✨ {self.name} received a model response")
            logger.info(
                f"🛠️ {self.name} selected {len(tool_calls) if tool_calls else 0} tools to use"
            )
        if tool_calls and routing_event is None:
            logger.info(
                f"🧰 Tools being prepared: {[self._routing_tool_label(call.function.name) for call in tool_calls]}"
            )

        try:
            if response is None:
                raise RuntimeError("No response received from the LLM")

            # Handle different tool_choices modes
            if self.tool_choices == ToolChoice.NONE:
                if tool_calls:
                    logger.warning(
                        f"🤔 Hmm, {self.name} tried to use tools when they weren't available!"
                    )
                if content:
                    self.memory.add_message(Message.assistant_message(content))
                    return True
                return False

            # Create and add assistant message
            assistant_msg = (
                Message.from_tool_calls(content=content, tool_calls=self.tool_calls)
                if self.tool_calls
                else Message.assistant_message(content)
            )
            self.memory.add_message(assistant_msg)

            if self.tool_choices == ToolChoice.REQUIRED and not self.tool_calls:
                return True  # Will be handled in act()

            # For 'auto' mode, continue with content if no commands but content exists
            if self.tool_choices == ToolChoice.AUTO and not self.tool_calls:
                return bool(content)

            return bool(self.tool_calls)
        except Exception as e:
            logger.error(f"🚨 {self.name}'s response processing failed")
            self.memory.add_message(
                Message.assistant_message(
                    f"Error encountered while processing: {str(e)}"
                )
            )
            return False

    async def step(self) -> str:
        self._trace_step_status = "success"
        self._trace_event("step", "started")
        try:
            result = await super().step()
        except asyncio.CancelledError:
            self._trace_event("step", "cancelled")
            raise
        except Exception:
            self._trace_event("step", "failure")
            raise
        self._trace_event("step", self._trace_step_status)
        return result

    async def act(self) -> str:
        """Execute tool calls and handle their results"""
        if self.routing_clarification:
            return self.routing_clarification
        if not self.tool_calls:
            if self.tool_choices == ToolChoice.REQUIRED:
                raise ValueError(TOOL_CALL_REQUIRED)

            # Return last message content if no tool calls
            return self.messages[-1].content or "No content or commands to execute"

        results = []
        image_messages = []
        for command in self.tool_calls:
            # Reset base64_image for each tool call
            self._current_base64_image = None

            result = await self.execute_tool(command)

            if self.max_observe:
                result = result[: self.max_observe]

            if self.routing_top_k is None:
                logger.info(
                    f"🎯 Tool '{self._routing_tool_label(command.function.name)}' completed"
                )
            else:
                logger.info(
                    f"🎯 Tool '{self._routing_tool_label(command.function.name)}' completed"
                )

            # Add tool response to memory
            tool_msg = Message.tool_message(
                content=result,
                tool_call_id=command.id,
                name=command.function.name,
            )
            self.memory.add_message(tool_msg)
            if self._current_base64_image:
                image_messages.append(
                    Message.user_message(
                        content=f"Image returned by {command.function.name}:",
                        base64_image=self._current_base64_image,
                    )
                )
            results.append(result)

        self.memory.add_messages(image_messages)
        return "\n\n".join(results)

    def _reject_tool_call(
        self, command: ToolCall, name: str, message: str, reason: str
    ) -> str:
        self._trace_event(
            "validation", "failure", call_id=command.id, tool_name=name, reason=reason
        )
        self._trace_event("observation", "failure", call_id=command.id, tool_name=name)
        return message

    async def execute_tool(self, command: ToolCall) -> str:
        """Execute a single tool call with robust error handling"""
        if not command or not command.function or not command.function.name:
            return "Error: Invalid command format"

        name = command.function.name
        if name not in self.available_tools.tool_map:
            self._trace_event(
                "validation",
                "failure",
                call_id=command.id,
                tool_name=name,
                reason="unknown_tool",
            )
            return await self._observe_tool_result(
                name, ToolFailure(error=f"Unknown tool '{name}'"), call_id=command.id
            )

        try:
            args = json.loads(
                command.function.arguments, parse_constant=_reject_non_json_constant
            )
        except ValueError:
            logger.warning(f"Invalid JSON arguments for tool '{name}'")
            return self._reject_tool_call(
                command,
                name,
                f"Error: Error parsing arguments for {name}: Invalid JSON format",
                "invalid_json",
            )

        if not isinstance(args, dict):
            return self._reject_tool_call(
                command,
                name,
                f"Error: Arguments for {name} must be a JSON object",
                "invalid_arguments",
            )

        if command.id in self.closed_pending_call_ids:
            return self._reject_tool_call(
                command,
                name,
                f"Error: Tool call ID '{command.id}' is already resolved",
                "duplicate_call",
            )
        pending = self.pending_tool_calls.get(command.id)
        if pending:
            if (
                pending.tool_name != name
                or pending.original_arguments_json != command.function.arguments
            ):
                return self._reject_tool_call(
                    command,
                    name,
                    f"Error: Tool call ID '{command.id}' already has a different pending call",
                    "duplicate_call",
                )
            self._trace_event(
                "clarification", "pending", call_id=command.id, tool_name=name
            )
            return f"Error: Clarification pending for '{name}'; original tool not executed."

        context_arguments = self._trusted_context_arguments(name)
        conflicts = [
            field
            for field, value in context_arguments.items()
            if field in args and args[field] != value
        ]
        if conflicts:
            return self._reject_tool_call(
                command,
                name,
                (
                    f"Error: Tool '{name}' arguments conflict with confirmed repository "
                    f"context: {', '.join(conflicts)}. Explicitly switch context or correct "
                    "the call; original tool not executed."
                ),
                "context_conflict",
            )
        sources = {field: "tool_call" for field in args}
        for field, value in context_arguments.items():
            if field not in args:
                args[field] = value
                sources[field] = "repository_context:user_input"
        self.tool_call_sources[command.id] = sources

        try:
            logged_name = self._routing_tool_label(name) if self.routing_top_k else name
            logger.info(f"🔧 Activating tool: '{logged_name}'...")
            result = await self._execute_with_budget(name, args, call_id=command.id)
            if isinstance(result, MissingParameterFailure):
                original = json.loads(command.function.arguments)
                pending = PendingToolCall(
                    tool_call_id=command.id,
                    tool_name=name,
                    original_arguments_json=command.function.arguments,
                    original_arguments=original,
                    arguments=copy.deepcopy(args),
                    sources=sources.copy(),
                    missing_fields=result.missing_fields.copy(),
                )
                self.pending_tool_calls[command.id] = pending
                missing = [
                    field
                    for field in pending.missing_fields
                    if field not in self._trusted_required_fields()
                ]
                if missing:
                    for attempt in range(MAX_CLARIFICATION_ATTEMPTS):
                        self._trace_event(
                            "clarification",
                            "requested",
                            call_id=command.id,
                            tool_name=name,
                            missing_count=len(missing),
                        )
                        question = (
                            f"Tool '{name}' needs required parameters: "
                            f"{', '.join(missing)}. "
                            "Reply with a JSON object naming each field, or cancel."
                            if attempt == 0
                            else (
                                f"Clarify parameters for tool '{name}' and call "
                                f"'{command.id}': {', '.join(missing)}. "
                                "Use a JSON object with unique field names, or cancel."
                            )
                        )
                        try:
                            reply = await AskHuman().execute(inquire=question)
                        except EOFError:
                            reply = "cancel"
                        observation = await self.submit_clarification_reply(
                            command.id, reply, record_tool_message=False
                        )
                        remaining = self.pending_tool_calls.get(command.id)
                        if not remaining or (
                            remaining.missing_fields
                            and all(
                                field in self._trusted_required_fields()
                                for field in remaining.missing_fields
                            )
                        ):
                            break
                    return observation
                self._trace_event(
                    "observation",
                    "pending",
                    call_id=command.id,
                    tool_name=name,
                    reason="required_missing",
                )
                return (
                    f"Error: Tool '{name}' validation failed: required parameters are "
                    "available in trusted context; original tool not executed."
                )

            return await self._observe_tool_result(name, result, call_id=command.id)
        except asyncio.CancelledError:
            observation = await self._observe_tool_result(
                name,
                ToolResult(
                    error=(
                        "Tool call cancelled locally; outcome unknown because "
                        "the operation may still complete"
                    ),
                    status="unknown",
                    error_kind="cancelled",
                ),
                call_id=command.id,
            )
            self.memory.add_message(
                Message.tool_message(
                    content=observation, tool_call_id=command.id, name=name
                )
            )
            raise
        except Exception as e:
            error_msg = f"⚠️ Tool '{name}' encountered a problem: {str(e)}"
            logger.error("Tool '{}' encountered an exception", logged_name)
            return await self._observe_tool_result(
                name, ToolFailure(error=error_msg), call_id=command.id
            )

    def _trusted_required_fields(self) -> set[str]:
        return set()

    def _remaining_run_seconds(self) -> Optional[float]:
        if self._run_deadline is None:
            return None
        return max(0.0, self._run_deadline - asyncio.get_running_loop().time())

    async def _execute_with_budget(
        self, name: str, arguments: dict, *, call_id: Optional[str] = None
    ) -> ToolResult:
        remaining = self._remaining_run_seconds()
        if remaining == 0:
            self._trace_event(
                "tool_execution",
                "unknown",
                call_id=call_id,
                tool_name=name,
                reason="budget_exhausted",
            )
            return ToolResult(
                error="Agent execution budget exhausted; tool not started",
                status="unknown",
                error_kind="timeout",
            )

        active_attempt: Optional[int] = None

        def on_tool_event(event: str, status: str, details: dict[str, Any]) -> None:
            nonlocal active_attempt
            if event == "tool_execution":
                if status == "started":
                    active_attempt = details.get("attempt")
                else:
                    active_attempt = None
            self._trace_event(event, status, call_id=call_id, tool_name=name, **details)

        try:
            with tool_event_scope(on_tool_event if self.execution_trace else None):
                async with asyncio.timeout(remaining):
                    result = await self.available_tools.execute(
                        name=name, tool_input=arguments
                    )
            if (
                name == Terminate().name
                and not isinstance(result, ToolValidationFailure)
                and result.status != "failure"
                and result.error_kind is None
                and arguments.get("status") in {"success", "failure"}
            ):
                self._trace_final_status = arguments["status"]
                self._trace_finish_source = "agent_declaration"
            return result
        except TimeoutError:
            self._trace_event(
                "tool_execution",
                "unknown",
                call_id=call_id,
                tool_name=name,
                error_kind="timeout",
                **({"attempt": active_attempt} if active_attempt else {}),
            )
            return ToolResult(
                error=(
                    "Agent execution budget exhausted during tool execution; "
                    "outcome unknown because the operation may still complete"
                ),
                status="unknown",
                error_kind="timeout",
            )
        except asyncio.CancelledError:
            self._trace_event(
                "tool_execution",
                "cancelled",
                call_id=call_id,
                tool_name=name,
                **({"attempt": active_attempt} if active_attempt else {}),
            )
            raise

    def _trusted_context_arguments(self, tool_name: str) -> dict[str, Any]:
        return {}

    async def _observe_tool_result(
        self, name: str, result: Any, *, call_id: Optional[str] = None
    ) -> str:
        result = normalize_tool_result(result)
        self._trace_event(
            "observation",
            "cancelled" if result.error_kind == "cancelled" else result.status,
            call_id=call_id,
            tool_name=name,
            **({"error_kind": result.error_kind} if result.error_kind else {}),
        )
        if result.status != "failure" and result.error_kind is None:
            await self._handle_special_tool(name=name, result=result)
        if hasattr(result, "base64_image") and result.base64_image:
            self._current_base64_image = result.base64_image
        status_header = f"Status: {result.status}\n"
        if result.error_kind:
            status_header += f"Error kind: {result.error_kind}\n"
        if result.attempts:
            summary = ", ".join(
                f"{item['number']}:{item['classification']}"
                + (f"({item['http_status']})" if item["http_status"] else "")
                for item in result.attempts
            )
            status_header += f"Attempts: {summary}\n"
        return (
            f"{status_header}Observed output of cmd `{name}` executed:\n{str(result)}"
            if result
            else f"{status_header}Cmd `{name}` completed with no output"
        )

    def _record_clarification_observation(
        self, tool_call_id: str, name: str, observation: str
    ) -> None:
        for message in reversed(self.memory.messages):
            if message.tool_call_id == tool_call_id and message.name == name:
                message.content = observation
                message.base64_image = self._current_base64_image
                return
        self.memory.add_message(
            Message.tool_message(
                content=observation,
                tool_call_id=tool_call_id,
                name=name,
                base64_image=self._current_base64_image,
            )
        )

    async def submit_clarification_reply(
        self, tool_call_id: str, reply: str, *, record_tool_message: bool = True
    ) -> str:
        if self._remaining_run_seconds() == 0:
            self._trace_event(
                "clarification",
                "unknown",
                call_id=tool_call_id,
                reason="budget_exhausted",
            )
            return (
                "Status: unknown\nError: Agent execution budget exhausted; "
                "pending tool not resumed."
            )
        if tool_call_id in self.closed_pending_call_ids:
            return f"Error: Tool call ID '{tool_call_id}' is already resolved"
        pending = self.pending_tool_calls.get(tool_call_id)
        if pending is None:
            return f"Error: No pending tool call for '{tool_call_id}'"
        if pending.in_flight:
            return f"Error: Tool call ID '{tool_call_id}' is already resuming"

        status = self._merge_clarification_reply(tool_call_id, reply)
        self._trace_event(
            "clarification",
            "merged"
            if status == "merged"
            else "cancelled"
            if status == "cancelled"
            else "pending",
            call_id=tool_call_id,
            tool_name=pending.tool_name,
        )
        if status == "merged":
            self.tool_call_sources[tool_call_id] = pending.sources.copy()
        if status != "merged":
            self._trace_event(
                "observation",
                "cancelled" if status == "cancelled" else "failure",
                call_id=tool_call_id,
                tool_name=pending.tool_name,
            )
            observation = (
                f"Error: Tool '{pending.tool_name}' validation failed: required "
                f"parameters missing. Clarification {status}; original tool not executed."
            )
        else:
            pending.in_flight = True
            self._current_base64_image = None
            try:
                result = await self._execute_with_budget(
                    pending.tool_name,
                    copy.deepcopy(pending.arguments),
                    call_id=tool_call_id,
                )
            except Exception as e:
                self.pending_tool_calls.pop(tool_call_id, None)
                self.closed_pending_call_ids.add(tool_call_id)
                logger.error(
                    "Tool '{}' failed during resume",
                    self._routing_tool_label(pending.tool_name),
                )
                self._trace_event(
                    "observation",
                    "failure",
                    call_id=tool_call_id,
                    tool_name=pending.tool_name,
                )
                observation = (
                    f"Error: Tool '{pending.tool_name}' failed during resume: {e}"
                )
            else:
                if isinstance(result, ToolValidationFailure):
                    pending.in_flight = False
                    if isinstance(result, MissingParameterFailure):
                        pending.missing_fields = result.missing_fields.copy()
                    observation = (
                        f"Error: Clarification pending for '{pending.tool_name}': "
                        f"{result}; original tool not executed."
                    )
                    self._trace_event(
                        "observation",
                        "pending"
                        if isinstance(result, MissingParameterFailure)
                        else "failure",
                        call_id=tool_call_id,
                        tool_name=pending.tool_name,
                    )
                else:
                    self.pending_tool_calls.pop(tool_call_id, None)
                    self.closed_pending_call_ids.add(tool_call_id)
                    observation = await self._observe_tool_result(
                        pending.tool_name, result, call_id=tool_call_id
                    )

        if record_tool_message:
            self._record_clarification_observation(
                tool_call_id, pending.tool_name, observation
            )
        return observation

    def _merge_clarification_reply(self, tool_call_id: str, reply: str) -> str:
        pending = self.pending_tool_calls.get(tool_call_id)
        if pending is None:
            return "mismatch"
        if pending.in_flight:
            return "busy"
        answerable_fields = [
            field
            for field in pending.missing_fields
            if field not in self._trusted_required_fields()
        ]

        text = reply.strip()
        if text.lower() == "cancel":
            del self.pending_tool_calls[tool_call_id]
            self.closed_pending_call_ids.add(tool_call_id)
            return "cancelled"
        if not text:
            return "needs_clarification"

        try:
            values = json.loads(
                text,
                object_pairs_hook=_reject_duplicate_fields,
                parse_constant=_reject_non_json_constant,
            )
        except json.JSONDecodeError:
            if len(answerable_fields) != 1 or text.startswith(("{", "[")):
                return "needs_clarification"
            values = {answerable_fields[0]: text}
        except ValueError:
            return "needs_clarification"
        else:
            if not isinstance(values, dict):
                if len(answerable_fields) != 1 or not isinstance(
                    values, (str, int, float, bool)
                ):
                    return "needs_clarification"
                values = {answerable_fields[0]: values}

        tool = self.available_tools.get_tool(pending.tool_name)
        root_fields = set(tool.parameters.get("properties", {})) | set(
            pending.arguments
        )
        if not values or any(
            (field not in root_fields and field not in pending.missing_fields)
            or value is None
            or value == ""
            for field, value in values.items()
        ):
            return "needs_clarification"

        updated = copy.deepcopy(pending.arguments)
        for field, value in values.items():
            if field in root_fields:
                updated[field] = value
                continue
            target = updated
            for part in field.split(".")[:-1]:
                target = target.get(part)
                if not isinstance(target, dict):
                    return "needs_clarification"
            target[field.split(".")[-1]] = value

        pending.arguments = updated
        pending.sources.update({field: "user_clarification" for field in values})
        pending.missing_fields = [
            field
            for field in pending.missing_fields
            if not _has_argument(updated, field)
        ]
        return "merged"

    async def _handle_special_tool(self, name: str, result: Any, **kwargs):
        """Handle special tool execution and state changes"""
        if not self._is_special_tool(name):
            return

        if self._should_finish_execution(name=name, result=result, **kwargs):
            # Set agent state to finished
            logger.info(f"🏁 Special tool '{name}' has completed the task!")
            self.state = AgentState.FINISHED

    @staticmethod
    def _should_finish_execution(**kwargs) -> bool:
        """Determine if tool execution should finish the agent"""
        return True

    def _is_special_tool(self, name: str) -> bool:
        """Check if tool name is in special tools list"""
        return name.lower() in [n.lower() for n in self.special_tool_names]

    async def cleanup(self):
        """Clean up resources used by the agent's tools."""
        logger.info(f"🧹 Cleaning up resources for agent '{self.name}'...")
        try:
            async with asyncio.timeout(self.cleanup_timeout_seconds):
                for tool_name, tool_instance in self.available_tools.tool_map.items():
                    if hasattr(
                        tool_instance, "cleanup"
                    ) and asyncio.iscoroutinefunction(tool_instance.cleanup):
                        try:
                            logger.debug(f"🧼 Cleaning up tool: {tool_name}")
                            await tool_instance.cleanup()
                        except Exception:
                            logger.error(
                                f"🚨 Error cleaning up tool '{self._routing_tool_label(tool_name)}'"
                            )
        except TimeoutError:
            logger.warning(f"Cleanup timed out for agent '{self.name}'")
            return
        logger.info(f"✨ Cleanup complete for agent '{self.name}'.")

    async def run(self, request: Optional[str] = None) -> str:
        """Run the agent with cleanup when done."""
        self.execution_trace = ExecutionTrace()
        self._trace_final_status = None
        self._trace_finish_source = "runtime_exit"
        self.trace_seen_call_ids.clear()
        self._trace_event(
            "task",
            "started",
            task_sha256=hashlib.sha256((request or "").encode("utf-8")).hexdigest(),
            task_chars=len(request or ""),
        )
        self._run_deadline = (
            asyncio.get_running_loop().time() + self.run_timeout_seconds
        )
        self.pending_tool_calls.clear()
        self.closed_pending_call_ids.clear()
        self.tool_call_sources.clear()
        self.routing_original_task = request
        self.routing_clarification = None
        self.routing_run_id = (
            self.execution_trace.run_id if self.routing_top_k is not None else None
        )
        try:
            async with asyncio.timeout(
                self.run_timeout_seconds + self.cleanup_timeout_seconds
            ) as guard:
                return await super().run(request)
        except asyncio.CancelledError:
            self._trace_final_status = "cancelled"
            self._trace_finish_source = "runtime_exit"
            raise
        except TimeoutError:
            if not guard.expired():
                self._trace_final_status = "failure"
                self._trace_finish_source = "runtime_exit"
                raise
            self._trace_final_status = "unknown"
            self._trace_finish_source = "runtime_exit"
            return (
                "Status: unknown\nError: Agent execution deadline exceeded; "
                "unfinished operation outcome unknown."
            )
        except Exception:
            self._trace_final_status = "failure"
            self._trace_finish_source = "runtime_exit"
            raise
        finally:
            self._run_deadline = None
            self.routing_original_task = None
            self.routing_clarification = None
            self.routing_run_id = None
            self.pending_tool_calls.clear()
            self.closed_pending_call_ids.clear()
            self.tool_call_sources.clear()
            self.trace_seen_call_ids.clear()
            try:
                async with asyncio.timeout(self.cleanup_timeout_seconds):
                    await self.cleanup()
            except TimeoutError:
                logger.warning(f"Cleanup timed out for agent '{self.name}'")
            finally:
                self._trace_event(
                    "finish",
                    self._trace_final_status or "unknown",
                    finish_source=self._trace_finish_source,
                    business_outcome="not_verified",
                )
