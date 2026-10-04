"""Build a current-step query and retrieve scored tools without filtering them."""

from dataclasses import dataclass
from typing import Sequence

from app.schema import Message, Role
from app.taskpilot.tool_embedding_index import (
    IndexVersion,
    InMemoryToolIndex,
    ToolMatch,
)
from app.tool.tool_collection import ToolCollection


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    matches: tuple[ToolMatch, ...]
    index_version: IndexVersion
    observation_tool_call_id: str | None


class SemanticToolRetriever:
    """Use explicit task input and current-task observations for retrieval."""

    def __init__(self, index: InMemoryToolIndex):
        self.index = index

    async def retrieve(
        self,
        original_task: str,
        messages: Sequence[Message],
        tools: ToolCollection,
    ) -> RetrievalResult:
        task = original_task.strip()
        if not task:
            raise ValueError("original task must not be empty")

        anchor = next(
            (
                position
                for position in range(len(messages) - 1, -1, -1)
                if messages[position].role == Role.USER
                and (messages[position].content or "").strip() == task
            ),
            None,
        )
        observation = None
        if anchor is not None:
            observation = next(
                (
                    message
                    for message in reversed(messages[anchor + 1 :])
                    if message.role == Role.TOOL
                    and message.tool_call_id
                    and message.content
                    and message.content.strip()
                ),
                None,
            )

        query = f"Original user task: {task}"
        if observation is not None:
            query += f"\nLatest tool observation: {observation.content.strip()}"
        version, matches = await self.index.search_with_version(query, tools)
        return RetrievalResult(
            query=query,
            matches=tuple(matches),
            index_version=version,
            observation_tool_call_id=(
                observation.tool_call_id if observation is not None else None
            ),
        )
