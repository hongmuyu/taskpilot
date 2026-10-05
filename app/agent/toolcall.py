import asyncio
import copy
import json
from dataclasses import dataclass
from typing import Any, List, Optional, Union

from pydantic import Field

from app.agent.react import ReActAgent
from app.exceptions import TokenLimitExceeded
from app.logger import logger
from app.prompt.toolcall import NEXT_STEP_PROMPT, SYSTEM_PROMPT
from app.schema import TOOL_CHOICE_TYPE, AgentState, Message, ToolCall, ToolChoice
from app.taskpilot.semantic_tool_retrieval import SemanticToolRetriever
from app.taskpilot.tool_embedding_index import (
    EmbeddingIndexError,
    InMemoryToolIndex,
    LocalTransformerEmbeddingBackend,
)
from app.tool import CreateChatCompletion, Terminate, ToolCollection
from app.tool.ask_human import AskHuman
from app.tool.tool_collection import MissingParameterFailure, ToolValidationFailure


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
    pending_tool_calls: dict[str, PendingToolCall] = Field(
        default_factory=dict, exclude=True, repr=False
    )
    closed_pending_call_ids: set[str] = Field(
        default_factory=set, exclude=True, repr=False
    )
    tool_call_sources: dict[str, dict[str, str]] = Field(
        default_factory=dict, exclude=True, repr=False
    )
    _current_base64_image: Optional[str] = None

    max_steps: int = 30
    max_observe: Optional[Union[int, bool]] = None

    async def think(self) -> bool:
        """Process current state and decide next actions using tools"""
        self.routing_clarification = None
        if self.next_step_prompt:
            user_msg = Message.user_message(self.next_step_prompt)
            self.messages += [user_msg]

        tool_schemas = self.available_tools.to_params()
        if self.routing_top_k is not None:
            business_tools = [
                tool
                for tool in self.available_tools
                if not self._is_special_tool(tool.name)
            ]
            if not business_tools or not (self.routing_original_task or "").strip():
                self.routing_clarification = (
                    "No suitable tool candidate is available for this step. "
                    "Please clarify the request and retry."
                )
            else:
                if self.routing_retriever is None:
                    self.routing_retriever = SemanticToolRetriever(
                        InMemoryToolIndex(LocalTransformerEmbeddingBackend())
                    )
                try:
                    retrieval = await self.routing_retriever.retrieve(
                        self.routing_original_task, self.messages, self.available_tools
                    )
                except EmbeddingIndexError:
                    self.routing_clarification = "Tool retrieval is unavailable. Please retry or clarify the request."
                else:
                    selected = [
                        match.name
                        for match in retrieval.matches
                        if match.score > 0 and not self._is_special_tool(match.name)
                    ][: self.routing_top_k]
                    tool_schemas = [
                        self.available_tools.get_tool(name).to_param()
                        for name in selected
                    ]
                    if not tool_schemas:
                        self.routing_clarification = (
                            "No suitable tool candidate matched this step. "
                            "Please clarify the request and retry."
                        )
            if self.routing_clarification:
                self.tool_calls = []
                self.memory.add_message(
                    Message.assistant_message(self.routing_clarification)
                )
                self.state = AgentState.FINISHED
                return True

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
            raise
        except Exception as e:
            # Check if this is a RetryError containing TokenLimitExceeded
            if hasattr(e, "__cause__") and isinstance(e.__cause__, TokenLimitExceeded):
                token_limit_error = e.__cause__
                logger.error(
                    f"🚨 Token limit error (from RetryError): {token_limit_error}"
                )
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

        # Log response info
        logger.info(f"✨ {self.name}'s thoughts: {content}")
        logger.info(
            f"🛠️ {self.name} selected {len(tool_calls) if tool_calls else 0} tools to use"
        )
        if tool_calls:
            logger.info(
                f"🧰 Tools being prepared: {[call.function.name for call in tool_calls]}"
            )
            logger.info(f"🔧 Tool arguments: {tool_calls[0].function.arguments}")

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
            logger.error(f"🚨 Oops! The {self.name}'s thinking process hit a snag: {e}")
            self.memory.add_message(
                Message.assistant_message(
                    f"Error encountered while processing: {str(e)}"
                )
            )
            return False

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

            logger.info(
                f"🎯 Tool '{command.function.name}' completed its mission! Result: {result}"
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

    async def execute_tool(self, command: ToolCall) -> str:
        """Execute a single tool call with robust error handling"""
        if not command or not command.function or not command.function.name:
            return "Error: Invalid command format"

        name = command.function.name
        if name not in self.available_tools.tool_map:
            return f"Error: Unknown tool '{name}'"

        try:
            args = json.loads(
                command.function.arguments, parse_constant=_reject_non_json_constant
            )
        except ValueError:
            logger.warning(f"Invalid JSON arguments for tool '{name}'")
            return f"Error: Error parsing arguments for {name}: Invalid JSON format"

        if not isinstance(args, dict):
            return f"Error: Arguments for {name} must be a JSON object"

        if command.id in self.closed_pending_call_ids:
            return f"Error: Tool call ID '{command.id}' is already resolved"
        pending = self.pending_tool_calls.get(command.id)
        if pending:
            if (
                pending.tool_name != name
                or pending.original_arguments_json != command.function.arguments
            ):
                return f"Error: Tool call ID '{command.id}' already has a different pending call"
            return f"Error: Clarification pending for '{name}'; original tool not executed."

        context_arguments = self._trusted_context_arguments(name)
        conflicts = [
            field
            for field, value in context_arguments.items()
            if field in args and args[field] != value
        ]
        if conflicts:
            return (
                f"Error: Tool '{name}' arguments conflict with confirmed repository "
                f"context: {', '.join(conflicts)}. Explicitly switch context or correct "
                "the call; original tool not executed."
            )
        sources = {field: "tool_call" for field in args}
        for field, value in context_arguments.items():
            if field not in args:
                args[field] = value
                sources[field] = "repository_context:user_input"
        self.tool_call_sources[command.id] = sources

        try:
            logger.info(f"🔧 Activating tool: '{name}'...")
            result = await self.available_tools.execute(name=name, tool_input=args)
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
                return (
                    f"Error: Tool '{name}' validation failed: required parameters are "
                    "available in trusted context; original tool not executed."
                )

            return await self._observe_tool_result(name, result)
        except Exception as e:
            error_msg = f"⚠️ Tool '{name}' encountered a problem: {str(e)}"
            logger.exception(error_msg)
            return f"Error: {error_msg}"

    def _trusted_required_fields(self) -> set[str]:
        return set()

    def _trusted_context_arguments(self, tool_name: str) -> dict[str, Any]:
        return {}

    async def _observe_tool_result(self, name: str, result: Any) -> str:
        await self._handle_special_tool(name=name, result=result)
        if hasattr(result, "base64_image") and result.base64_image:
            self._current_base64_image = result.base64_image
        return (
            f"Observed output of cmd `{name}` executed:\n{str(result)}"
            if result
            else f"Cmd `{name}` completed with no output"
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
        if tool_call_id in self.closed_pending_call_ids:
            return f"Error: Tool call ID '{tool_call_id}' is already resolved"
        pending = self.pending_tool_calls.get(tool_call_id)
        if pending is None:
            return f"Error: No pending tool call for '{tool_call_id}'"
        if pending.in_flight:
            return f"Error: Tool call ID '{tool_call_id}' is already resuming"

        status = self._merge_clarification_reply(tool_call_id, reply)
        if status == "merged":
            self.tool_call_sources[tool_call_id] = pending.sources.copy()
        if status != "merged":
            observation = (
                f"Error: Tool '{pending.tool_name}' validation failed: required "
                f"parameters missing. Clarification {status}; original tool not executed."
            )
        else:
            pending.in_flight = True
            self._current_base64_image = None
            try:
                result = await self.available_tools.execute(
                    name=pending.tool_name,
                    tool_input=copy.deepcopy(pending.arguments),
                )
            except Exception as e:
                self.pending_tool_calls.pop(tool_call_id, None)
                self.closed_pending_call_ids.add(tool_call_id)
                logger.exception(
                    f"Tool '{pending.tool_name}' failed during resume: {e}"
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
                else:
                    self.pending_tool_calls.pop(tool_call_id, None)
                    self.closed_pending_call_ids.add(tool_call_id)
                    observation = await self._observe_tool_result(
                        pending.tool_name, result
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
        for tool_name, tool_instance in self.available_tools.tool_map.items():
            if hasattr(tool_instance, "cleanup") and asyncio.iscoroutinefunction(
                tool_instance.cleanup
            ):
                try:
                    logger.debug(f"🧼 Cleaning up tool: {tool_name}")
                    await tool_instance.cleanup()
                except Exception as e:
                    logger.error(
                        f"🚨 Error cleaning up tool '{tool_name}': {e}", exc_info=True
                    )
        logger.info(f"✨ Cleanup complete for agent '{self.name}'.")

    async def run(self, request: Optional[str] = None) -> str:
        """Run the agent with cleanup when done."""
        self.pending_tool_calls.clear()
        self.closed_pending_call_ids.clear()
        self.tool_call_sources.clear()
        self.routing_original_task = request
        self.routing_clarification = None
        try:
            return await super().run(request)
        finally:
            self.routing_original_task = None
            self.routing_clarification = None
            self.pending_tool_calls.clear()
            self.closed_pending_call_ids.clear()
            self.tool_call_sources.clear()
            await self.cleanup()
