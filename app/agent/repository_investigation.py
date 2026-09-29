from typing import Optional

from pydantic import Field

from app.agent.toolcall import ToolCallAgent
from app.prompt.toolcall import NEXT_STEP_PROMPT
from app.taskpilot.github_tools import (
    GitHubClient,
    GitHubCodeSearch,
    GitHubIssueDetail,
    GitHubIssueSearch,
    GitHubReadFile,
    GitHubRepositoryInfo,
    RepositoryContext,
)
from app.tool import Terminate, ToolCollection


class RepositoryInvestigationAgent(ToolCallAgent):
    """Single-agent, read-only GitHub repository investigation entry point."""

    name: str = "TaskPilotRepositoryInvestigation"
    description: str = "Investigates one explicitly selected GitHub repository using read-only tools."
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
        github = client or GitHubClient.from_environment()
        tools = ToolCollection(
            GitHubRepositoryInfo(context=context, client=github),
            GitHubIssueSearch(context=context, client=github),
            GitHubIssueDetail(context=context, client=github),
            GitHubCodeSearch(context=context, client=github),
            GitHubReadFile(context=context, client=github),
            Terminate(),
        )
        ref = f" at ref {context.ref}" if context.ref else " at its default branch"
        return cls(
            repository_context=context,
            github_client=github,
            llm=llm,
            max_steps=max_steps,
            available_tools=tools,
            system_prompt=(
                "You are TaskPilot, a read-only repository investigation agent. "
                f"The user selected GitHub repository {context.owner}/{context.repo}{ref}. "
                "Use the available tools dynamically based on the request and each observation. "
                "Do not assume a fixed tool sequence. Base conclusions on retrieved repository evidence, "
                "include issue numbers, file paths, and source URLs when available, and state uncertainty. "
                "Never request or perform writes, issue changes, shell commands, or destructive actions. "
                "When done, answer the user and call terminate."
            ),
        )

    async def cleanup(self) -> None:
        await self.github_client.aclose()
