"""Collection classes for managing multiple tools."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Dict, Iterator, List

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError

from app.exceptions import ToolError
from app.logger import logger
from app.tool.base import (
    BaseTool,
    ToolFailure,
    ToolMetadata,
    ToolResult,
    normalize_tool_result,
)


_SUPPORTED_SCHEMA_KEYWORDS = {
    "type",
    "properties",
    "required",
    "enum",
    "additionalProperties",
    "items",
    "anyOf",
    "minItems",
    "minimum",
    "maximum",
    "description",
    "default",
    "title",
}
_MAX_READ_ATTEMPTS = 3
_RETRY_BASE_DELAY_SECONDS = 0.1
_tool_event_hook: ContextVar[
    Callable[[str, str, dict[str, Any]], None] | None
] = ContextVar("tool_event_hook", default=None)


@contextmanager
def tool_event_scope(
    hook: Callable[[str, str, dict[str, Any]], None] | None,
) -> Iterator[None]:
    token = _tool_event_hook.set(hook)
    try:
        yield
    finally:
        _tool_event_hook.reset(token)


def _emit_tool_event(event: str, status: str, **details: Any) -> None:
    hook = _tool_event_hook.get()
    if hook is not None:
        hook(event, status, details)


class ToolValidationFailure(ToolFailure):
    """Arguments or schema failed before the underlying tool was called."""


class MissingParameterFailure(ToolValidationFailure):
    missing_fields: List[str]


def _unsupported_schema_keyword(schema: Any) -> str | None:
    if not isinstance(schema, dict):
        return None
    for keyword in schema:
        if keyword not in _SUPPORTED_SCHEMA_KEYWORDS:
            return keyword
    for child in schema.get("properties", {}).values():
        unsupported = _unsupported_schema_keyword(child)
        if unsupported:
            return unsupported
    for keyword in ("items", "additionalProperties"):
        child = schema.get(keyword)
        children = child if isinstance(child, list) else [child]
        for item in children:
            unsupported = _unsupported_schema_keyword(item)
            if unsupported:
                return unsupported
    for child in schema.get("anyOf", []):
        unsupported = _unsupported_schema_keyword(child)
        if unsupported:
            return unsupported
    return None


class ToolCollection:
    """A collection of defined tools."""

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, *tools: BaseTool):
        self.tools = tools
        self.tool_map = {}
        for tool in tools:
            if tool.name in self.tool_map:
                raise ValueError(f"Duplicate tool name: {tool.name}")
            self.tool_map[tool.name] = tool

    def __iter__(self):
        return iter(self.tools)

    def to_params(self) -> List[Dict[str, Any]]:
        return [tool.to_param() for tool in self.tools]

    def to_metadata(self) -> List[ToolMetadata]:
        return [tool.metadata for tool in self.tools]

    async def execute(self, *, name: str, tool_input: Any = None) -> ToolResult:
        tool = self.tool_map.get(name)
        if not tool:
            _emit_tool_event("validation", "failure", reason="unknown_tool")
            return ToolValidationFailure(error=f"Tool {name} is invalid")
        if (
            not isinstance(tool.parameters, dict)
            or tool.parameters.get("type") != "object"
        ):
            _emit_tool_event("validation", "failure", reason="schema_rejected")
            return ToolValidationFailure(
                error=(
                    f"Tool '{name}' validation failed: unsupported parameter schema "
                    "(expected object)"
                )
            )
        if not isinstance(tool_input, dict):
            _emit_tool_event("validation", "failure", reason="invalid_arguments")
            return ToolValidationFailure(
                error=(
                    f"Tool '{name}' validation failed: arguments must be "
                    "a JSON object"
                )
            )
        try:
            Draft7Validator.check_schema(tool.parameters)
        except SchemaError:
            _emit_tool_event("validation", "failure", reason="schema_rejected")
            return ToolValidationFailure(
                error=f"Tool '{name}' has an invalid parameter schema"
            )
        unsupported = _unsupported_schema_keyword(tool.parameters)
        if unsupported:
            _emit_tool_event("validation", "failure", reason="schema_rejected")
            return ToolValidationFailure(
                error=f"Tool '{name}' has unsupported schema keyword: {unsupported}"
            )
        errors = list(Draft7Validator(tool.parameters).iter_errors(tool_input))
        missing_fields = list(
            dict.fromkeys(
                ".".join([*(str(part) for part in error.absolute_path), field])
                for error in errors
                if error.validator == "required"
                for field in error.validator_value
                if field not in error.instance
            )
        )
        if missing_fields:
            _emit_tool_event(
                "validation",
                "pending",
                reason="required_missing",
                missing_count=len(missing_fields),
            )
            return MissingParameterFailure(
                error=f"Tool '{name}' validation failed: required parameters missing",
                missing_fields=missing_fields,
            )
        if errors:
            _emit_tool_event("validation", "failure", reason="schema_rejected")
            error = errors[0]
            path = "$" + "".join(f"[{part!r}]" for part in error.absolute_path)
            return ToolValidationFailure(
                error=f"Tool '{name}' validation failed at {path}: {error.validator}"
            )
        _emit_tool_event("validation", "success")
        attempts: list[dict[str, Any]] = []
        current_attempt = 0
        loop = asyncio.get_running_loop()
        deadline = loop.time() + tool.timeout_seconds
        try:
            async with asyncio.timeout_at(deadline):
                for number in range(
                    1, _MAX_READ_ATTEMPTS + 1 if tool.retry_safe_read else 2
                ):
                    if number > 1:
                        previous = attempts[-1]
                        _emit_tool_event(
                            "retry",
                            "started",
                            attempt=number,
                            classification=previous["classification"],
                            **(
                                {"http_status": previous["http_status"]}
                                if previous["http_status"]
                                else {}
                            ),
                        )
                    _emit_tool_event("tool_execution", "started", attempt=number)
                    current_attempt = number
                    result = normalize_tool_result(await tool(**tool_input))
                    _emit_tool_event(
                        "tool_execution",
                        "cancelled"
                        if result.error_kind == "cancelled"
                        else result.status,
                        attempt=number,
                        **(
                            {"error_kind": result.error_kind}
                            if result.error_kind
                            else {}
                        ),
                    )
                    current_attempt = 0
                    if not tool.retry_safe_read:
                        return result
                    classification = (
                        result.retry_classification
                        if result.status == "failure"
                        else result.status
                    ) or "permanent"
                    attempts.append(
                        {
                            "number": number,
                            "classification": classification,
                            "http_status": result.http_status,
                            "status": result.status,
                        }
                    )
                    logger.info(
                        "tool_retry_attempt name={} number={} classification={} http_status={} status={}",
                        name,
                        number,
                        classification,
                        result.http_status,
                        result.status,
                    )
                    result = result.model_copy(update={"attempts": attempts.copy()})
                    if (
                        result.status != "failure"
                        or result.retry_classification != "transient"
                        or number == _MAX_READ_ATTEMPTS
                    ):
                        return result
                    delay = max(
                        _RETRY_BASE_DELAY_SECONDS * 2 ** (number - 1),
                        result.retry_after_seconds or 0,
                    )
                    if delay >= deadline - loop.time():
                        return result
                    await asyncio.sleep(delay)
        except TimeoutError:
            _emit_tool_event(
                "tool_execution",
                "unknown",
                error_kind="timeout",
                **({"attempt": current_attempt} if current_attempt else {}),
            )
            return ToolResult(
                error=(
                    f"Tool '{name}' timed out after {tool.timeout_seconds:g} seconds; "
                    "outcome unknown because the operation may still complete"
                ),
                status="unknown",
                error_kind="timeout",
                attempts=attempts,
            )
        except ToolError as e:
            _emit_tool_event(
                "tool_execution",
                "failure",
                **({"attempt": current_attempt} if current_attempt else {}),
            )
            if tool.retry_safe_read:
                attempts.append(
                    {
                        "number": number,
                        "classification": "permanent",
                        "http_status": None,
                        "status": "failure",
                    }
                )
            return normalize_tool_result(
                ToolFailure(error=e.message, attempts=attempts)
            )

    async def execute_all(self) -> List[ToolResult]:
        """Execute all tools in the collection sequentially."""
        results = []
        for tool in self.tools:
            try:
                result = await tool()
                results.append(result)
            except ToolError as e:
                results.append(ToolFailure(error=e.message))
        return results

    def get_tool(self, name: str) -> BaseTool:
        return self.tool_map.get(name)

    def add_tool(self, tool: BaseTool):
        """Add a single tool to the collection."""
        if tool.name in self.tool_map:
            raise ValueError(f"Duplicate tool name: {tool.name}")

        self.tools += (tool,)
        self.tool_map[tool.name] = tool
        return self

    def add_tools(self, *tools: BaseTool):
        """Add multiple tools to the collection."""
        for tool in tools:
            self.add_tool(tool)
        return self
