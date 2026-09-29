"""Collection classes for managing multiple tools."""
from typing import Any, Dict, List

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError

from app.exceptions import ToolError
from app.logger import logger
from app.tool.base import BaseTool, ToolFailure, ToolResult


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
        self.tool_map = {tool.name: tool for tool in tools}

    def __iter__(self):
        return iter(self.tools)

    def to_params(self) -> List[Dict[str, Any]]:
        return [tool.to_param() for tool in self.tools]

    async def execute(
        self, *, name: str, tool_input: Any = None
    ) -> ToolResult:
        tool = self.tool_map.get(name)
        if not tool:
            return ToolFailure(error=f"Tool {name} is invalid")
        if (
            not isinstance(tool.parameters, dict)
            or tool.parameters.get("type") != "object"
        ):
            return ToolFailure(
                error=(
                    f"Tool '{name}' validation failed: unsupported parameter schema "
                    "(expected object)"
                )
            )
        if not isinstance(tool_input, dict):
            return ToolFailure(
                error=(
                    f"Tool '{name}' validation failed: arguments must be "
                    "a JSON object"
                )
            )
        try:
            Draft7Validator.check_schema(tool.parameters)
        except SchemaError:
            return ToolFailure(error=f"Tool '{name}' has an invalid parameter schema")
        unsupported = _unsupported_schema_keyword(tool.parameters)
        if unsupported:
            return ToolFailure(
                error=f"Tool '{name}' has unsupported schema keyword: {unsupported}"
            )
        error = next(Draft7Validator(tool.parameters).iter_errors(tool_input), None)
        if error:
            path = "$" + "".join(f"[{part!r}]" for part in error.absolute_path)
            return ToolFailure(
                error=f"Tool '{name}' validation failed at {path}: {error.validator}"
            )
        try:
            result = await tool(**tool_input)
            return result
        except ToolError as e:
            return ToolFailure(error=e.message)

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
        """Add a single tool to the collection.

        If a tool with the same name already exists, it will be skipped and a warning will be logged.
        """
        if tool.name in self.tool_map:
            logger.warning(f"Tool {tool.name} already exists in collection, skipping")
            return self

        self.tools += (tool,)
        self.tool_map[tool.name] = tool
        return self

    def add_tools(self, *tools: BaseTool):
        """Add multiple tools to the collection.

        If any tool has a name conflict with an existing tool, it will be skipped and a warning will be logged.
        """
        for tool in tools:
            self.add_tool(tool)
        return self
