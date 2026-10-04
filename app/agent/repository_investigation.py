from typing import Optional

from pydantic import Field

from app.agent.toolcall import ToolCallAgent
from app.prompt.toolcall import NEXT_STEP_PROMPT
from app.schema import ToolCall
from app.taskpilot.github_tools import (
    GitHubClient,
    GitHubCodeSearch,
    GitHubIssueDetail,
    GitHubIssueSearch,
    GitHubReadFile,
    GitHubRepositoryInfo,
    GitHubReadOnlyTool,
    RepositoryContext,
)
from app.tool import Terminate, ToolCollection


class RepositoryInvestigationAgent(ToolCallAgent):
    """Single-agent, read-only GitHub repository investigation entry point."""

    name: str = "TaskPilotRepositoryInvestigation"
    description: str = (
        "Investigates one explicitly selected GitHub repository using read-only tools."
    )
    system_prompt: Optional[str] = None
    next_step_prompt: str = NEXT_STEP_PROMPT
    repository_context: RepositoryContext
    github_client: GitHubClient = Field(exclude=True)
    max_steps: int = 12
    special_tool_names: list[str] = Field(default_factory=lambda: [Terminate().name])

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def create(
        cls,
        context: RepositoryContext,
        *,
        client: Optional[GitHubClient] = None,
        llm=None,
        max_steps: int = 12,
    ) -> "RepositoryInvestigationAgent":
        if context.source != "user_input":
            raise ValueError(
                "Repository context requires an explicit user_input source"
            )
        github = client or GitHubClient.from_environment()
        tools = ToolCollection(
            GitHubRepositoryInfo(context=context, client=github),
            GitHubIssueSearch(context=context, client=github),
            GitHubIssueDetail(context=context, client=github),
            GitHubCodeSearch(context=context, client=github),
            GitHubReadFile(context=context, client=github),
            Terminate(),
        )
        return cls(
            repository_context=context,
            github_client=github,
            llm=llm,
            max_steps=max_steps,
            available_tools=tools,
            system_prompt=cls._prompt_for_context(context),
        )

    @staticmethod
    def _prompt_for_context(context: RepositoryContext) -> str:
        ref = f" at ref {context.ref}" if context.ref else " at its default branch"
        return (
            "You are TaskPilot, a read-only repository investigation agent. "
            f"The user selected GitHub repository {context.owner}/{context.repo}{ref}. "
            "Use the available tools dynamically based on the request and each observation. "
            "Do not assume a fixed tool sequence. Base conclusions on retrieved repository evidence, "
            "include issue numbers, file paths, and source URLs when available, and state uncertainty. "
            "Never request or perform writes, issue changes, shell commands, or destructive actions. "
            "When done, answer the user and call terminate."
        )

    def _trusted_context_arguments(self, tool_name: str) -> dict[str, str]:
        if self.repository_context.source != "user_input":
            return {}
        tool = self.available_tools.get_tool(tool_name)
        properties = tool.parameters.get("properties", {})
        context = self.repository_context
        values = {
            "repository": f"{context.owner}/{context.repo}",
            "owner": context.owner,
            "repo": context.repo,
        }
        if context.ref is not None:
            values["ref"] = context.ref
        return {field: value for field, value in values.items() if field in properties}

    async def execute_tool(self, command: ToolCall) -> str:
        if self.repository_context.source != "user_input":
            return "Error: Repository context source is unverified; tool not executed."
        return await super().execute_tool(command)

    def switch_repository_context(self, context: RepositoryContext) -> None:
        if context.source != "user_input":
            raise ValueError(
                "Repository context requires an explicit user_input source"
            )
        if any(pending.in_flight for pending in self.pending_tool_calls.values()):
            raise RuntimeError("Cannot switch repository during a tool dispatch")
        self.closed_pending_call_ids.update(self.pending_tool_calls)
        self.pending_tool_calls.clear()
        self.tool_call_sources.clear()
        self.tool_calls.clear()
        self.memory.clear()
        self.repository_context = context
        self.system_prompt = self._prompt_for_context(context)
        for tool in self.available_tools:
            if isinstance(tool, GitHubReadOnlyTool):
                tool.context = context

    async def cleanup(self) -> None:
        await self.github_client.aclose()
