import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Union
from urllib.parse import quote

from pydantic import BaseModel, Field

from app.utils.logger import logger


# class BaseTool(ABC, BaseModel):
#     name: str
#     description: str
#     parameters: Optional[dict] = None

#     class Config:
#         arbitrary_types_allowed = True

#     async def __call__(self, **kwargs) -> Any:
#         """Execute the tool with given parameters."""
#         return await self.execute(**kwargs)

#     @abstractmethod
#     async def execute(self, **kwargs) -> Any:
#         """Execute the tool with given parameters."""

#     def to_param(self) -> Dict:
#         """Convert tool to function call format."""
#         return {
#             "type": "function",
#             "function": {
#                 "name": self.name,
#                 "description": self.description,
#                 "parameters": self.parameters,
#             },
#         }


class ToolResult(BaseModel):
    """Represents the result of a tool execution."""

    output: Any = Field(default=None)
    error: Optional[str] = Field(default=None)
    base64_image: Optional[str] = Field(default=None)
    system: Optional[str] = Field(default=None)
    status: Optional[Literal["success", "failure", "unknown"]] = Field(default=None)

    class Config:
        arbitrary_types_allowed = True

    def __bool__(self):
        return any(
            getattr(self, field) is not None
            for field in ("output", "error", "base64_image", "system")
        )

    def __add__(self, other: "ToolResult"):
        def combine_fields(
            field: Optional[str], other_field: Optional[str], concatenate: bool = True
        ):
            if field and other_field:
                if concatenate:
                    return field + other_field
                raise ValueError("Cannot combine tool results")
            return field or other_field

        return ToolResult(
            output=combine_fields(self.output, other.output),
            error=combine_fields(self.error, other.error),
            base64_image=combine_fields(self.base64_image, other.base64_image, False),
            system=combine_fields(self.system, other.system),
        )

    def __str__(self):
        if self.error is not None:
            return f"Error: {self.error}"
        return str(self.output) if self.output is not None else ""

    def replace(self, **kwargs):
        """Returns a new ToolResult with the given fields replaced."""
        # return self.copy(update=kwargs)
        return type(self)(**{**self.dict(), **kwargs})


def normalize_tool_result(value: Any) -> ToolResult:
    """Preserve explicit outcome signals before a result becomes observation text."""
    if not isinstance(value, ToolResult):
        return ToolResult(output=value, status="unknown")
    if isinstance(value, ToolFailure) or value.error is not None:
        status = "failure"
    elif value.status is not None:
        status = value.status
    elif value.output is not None or value.base64_image is not None:
        status = "success"
    else:
        status = "unknown"
    return value.model_copy(update={"status": status})


@dataclass(frozen=True)
class ToolMetadata:
    """Live view of a tool's existing definition, not a second schema."""

    tool: "BaseTool"

    @property
    def name(self) -> str:
        return self.tool.name

    @property
    def description(self) -> str:
        return self.tool.description

    @property
    def schema(self) -> Optional[dict]:
        return self.tool.parameters

    @property
    def capabilities(self) -> tuple[str, ...]:
        return self.tool.capabilities

    @property
    def examples(self) -> tuple[str, ...]:
        return self.tool.examples

    @property
    def source(self) -> Literal["local", "mcp"]:
        return self.tool.metadata_origin()[0]

    @property
    def server_id(self) -> Optional[str]:
        return self.tool.metadata_origin()[1]

    @property
    def original_name(self) -> str:
        return self.tool.metadata_origin()[2]

    @property
    def identity(self) -> str:
        source, server_id, original_name = self.tool.metadata_origin()
        if source == "mcp":
            return (
                f"mcp:{quote(server_id or '', safe='')}:{quote(original_name, safe='')}"
            )
        return f"local:{quote(original_name, safe='')}"

    @property
    def risk(self) -> dict[str, str | bool]:
        """Informational placeholder; dispatch does not use it as a gate."""
        return {
            "status": "pending_phase_5",
            "write_allowed": False,
            "enforced": False,
        }


class BaseTool(ABC, BaseModel):
    """Consolidated base class for all tools combining BaseModel and Tool functionality.

    Provides:
    - Pydantic model validation
    - Schema registration
    - Standardized result handling
    - Abstract execution interface

    Attributes:
        name (str): Tool name
        description (str): Tool description
        parameters (dict): Tool parameters schema
        _schemas (Dict[str, List[ToolSchema]]): Registered method schemas
    """

    name: str
    description: str
    parameters: Optional[dict] = None
    capabilities: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    # _schemas: Dict[str, List[ToolSchema]] = {}

    class Config:
        arbitrary_types_allowed = True
        underscore_attrs_are_private = False

    # def __init__(self, **data):
    #     """Initialize tool with model validation and schema registration."""
    #     super().__init__(**data)
    #     logger.debug(f"Initializing tool class: {self.__class__.__name__}")
    #     self._register_schemas()

    # def _register_schemas(self):
    #     """Register schemas from all decorated methods."""
    #     for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
    #         if hasattr(method, 'tool_schemas'):
    #             self._schemas[name] = method.tool_schemas
    #             logger.debug(f"Registered schemas for method '{name}' in {self.__class__.__name__}")

    async def __call__(self, **kwargs) -> Any:
        """Execute the tool with given parameters."""
        return await self.execute(**kwargs)

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """Execute the tool with given parameters."""

    def metadata_origin(
        self,
    ) -> tuple[Literal["local", "mcp"], Optional[str], str]:
        return "local", None, self.name

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(self)

    def to_param(self) -> Dict:
        """Convert tool to function call format.

        Returns:
            Dictionary with tool metadata in OpenAI function calling format
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    # def get_schemas(self) -> Dict[str, List[ToolSchema]]:
    #     """Get all registered tool schemas.

    #     Returns:
    #         Dict mapping method names to their schema definitions
    #     """
    #     return self._schemas

    def success_response(self, data: Union[Dict[str, Any], str]) -> ToolResult:
        """Create a successful tool result.

        Args:
            data: Result data (dictionary or string)

        Returns:
            ToolResult with success=True and formatted output
        """
        if isinstance(data, str):
            text = data
        else:
            text = json.dumps(data, indent=2)
        logger.debug(f"Created success response for {self.__class__.__name__}")
        return ToolResult(output=text)

    def fail_response(self, msg: str) -> ToolResult:
        """Create a failed tool result.

        Args:
            msg: Error message describing the failure

        Returns:
            ToolResult with success=False and error message
        """
        logger.debug(f"Tool {self.__class__.__name__} returned failed result: {msg}")
        return ToolResult(error=msg)


class CLIResult(ToolResult):
    """A ToolResult that can be rendered as a CLI output."""


class ToolFailure(ToolResult):
    """A ToolResult that represents a failure."""

    status: Literal["failure"] = "failure"
