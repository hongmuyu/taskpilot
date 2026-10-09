import asyncio

import pytest

from app.prompt.toolcall import NEXT_STEP_PROMPT
from app.schema import Message
from app.taskpilot.semantic_tool_retrieval import SemanticToolRetriever
from app.taskpilot.tool_embedding_index import InMemoryToolIndex
from app.tool.base import BaseTool, ToolResult
from app.tool.tool_collection import ToolCollection


class ProbeTool(BaseTool):
    name: str = "issue_tool"
    description: str = "Search repository issues"
    parameters: dict = {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return ToolResult(output="unused")


class KeywordVectors:
    model_id = "fixed-vectors"
    revision = "fixture-1"
    vector_version = "keywords-v1"

    def __init__(self):
        self.calls = []

    async def embed(self, texts):
        self.calls.append(tuple(texts))
        vectors = []
        for text in texts:
            lowered = text.lower()
            if "read source files" in lowered or any(
                word in lowered for word in ("source file", "code path")
            ):
                vectors.append([0.0, 1.0, 0.0])
            elif "search repository issues" in lowered or any(
                word in lowered for word in ("issue", "bug report", "ticket")
            ):
                vectors.append([1.0, 0.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return vectors


def tool_pool():
    return ToolCollection(
        ProbeTool(),
        ProbeTool(name="file_tool", description="Read source files"),
    )


def messages(task, observation, *, call_id="call-1"):
    return [
        Message.user_message(task),
        Message.user_message(NEXT_STEP_PROMPT),
        Message.tool_message(observation, name="probe", tool_call_id=call_id),
        Message.user_message(NEXT_STEP_PROMPT),
    ]


@pytest.mark.asyncio
async def test_same_task_with_different_observations_changes_ranking():
    backend = KeywordVectors()
    index = InMemoryToolIndex(backend)
    retriever = SemanticToolRetriever(index)
    tools = tool_pool()
    exposed_schemas = tools.to_params()
    task = "Investigate the repository"

    issue_result = await retriever.retrieve(
        task, messages(task, "Issue #42 reports a crash"), tools
    )
    file_result = await retriever.retrieve(
        task,
        messages(task, "Read the source file at app/router.py", call_id="call-2"),
        tools,
    )

    assert issue_result.matches[0].name == "issue_tool"
    assert issue_result.matches[0].identity == "local:issue_tool"
    assert issue_result.matches[0].score == pytest.approx(1.0)
    assert file_result.matches[0].name == "file_tool"
    assert file_result.matches[0].score == pytest.approx(1.0)
    assert task in issue_result.query and "Issue #42" in issue_result.query
    assert task in file_result.query and "app/router.py" in file_result.query
    assert NEXT_STEP_PROMPT not in issue_result.query
    assert issue_result.observation_tool_call_id == "call-1"
    assert file_result.observation_tool_call_id == "call-2"
    assert issue_result.index_version == index.version
    assert tools.to_params() == exposed_schemas


@pytest.mark.asyncio
async def test_synonymous_issue_observations_retrieve_same_tool():
    retriever = SemanticToolRetriever(InMemoryToolIndex(KeywordVectors()))
    tools = tool_pool()
    task = "Review the repository"

    bug_report = await retriever.retrieve(
        task, messages(task, "Find the bug report about timeouts"), tools
    )
    ticket = await retriever.retrieve(
        task, messages(task, "Locate the ticket about timeouts"), tools
    )

    assert bug_report.matches[0].name == "issue_tool"
    assert ticket.matches[0].name == "issue_tool"
    assert bug_report.matches[0].score == ticket.matches[0].score


@pytest.mark.asyncio
async def test_no_clear_match_keeps_all_scored_candidates():
    retriever = SemanticToolRetriever(InMemoryToolIndex(KeywordVectors()))
    tools = tool_pool()
    task = "What is tomorrow's weather?"

    result = await retriever.retrieve(task, [Message.user_message(task)], tools)

    assert len(result.matches) == 2
    assert all(hit.score == pytest.approx(0.0) for hit in result.matches)
    assert result.observation_tool_call_id is None
    assert result.query == f"Original user task: {task}"
    assert result.index_version.model_id == "fixed-vectors"


@pytest.mark.asyncio
async def test_only_latest_observation_from_current_task_is_used():
    retriever = SemanticToolRetriever(InMemoryToolIndex(KeywordVectors()))
    task = "Investigate the repository"
    history = [
        Message.user_message("Old task"),
        Message.tool_message("Read source file", name="probe", tool_call_id="old"),
        Message.user_message(task),
        Message.tool_message("Issue #42", name="probe", tool_call_id="current"),
        Message.tool_message("  ", name="probe", tool_call_id="empty"),
        Message.assistant_message("I think code search is next"),
        Message.user_message(NEXT_STEP_PROMPT),
    ]

    result = await retriever.retrieve(task, history, tool_pool())

    assert result.matches[0].name == "issue_tool"
    assert result.observation_tool_call_id == "current"
    assert "Issue #42" in result.query
    assert "Read source file" not in result.query
    assert "code search is next" not in result.query


@pytest.mark.asyncio
async def test_unanchored_history_cannot_reuse_previous_task_observation():
    retriever = SemanticToolRetriever(InMemoryToolIndex(KeywordVectors()))
    task = "Find an issue"
    old_history = [
        Message.user_message("Old task"),
        Message.tool_message("Read source file", name="probe", tool_call_id="old"),
    ]

    result = await retriever.retrieve(task, old_history, tool_pool())

    assert result.query == f"Original user task: {task}"
    assert result.observation_tool_call_id is None


@pytest.mark.asyncio
async def test_blank_original_task_is_rejected():
    retriever = SemanticToolRetriever(InMemoryToolIndex(KeywordVectors()))

    with pytest.raises(ValueError, match="original task"):
        await retriever.retrieve("  ", [], tool_pool())


@pytest.mark.asyncio
async def test_result_version_matches_scored_snapshot_during_concurrent_rebuild():
    class DelayedQueryVectors(KeywordVectors):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def embed(self, texts):
            if len(texts) == 1 and texts[0].startswith("Original user task:"):
                self.started.set()
                await self.release.wait()
            return await super().embed(texts)

    backend = DelayedQueryVectors()
    index = InMemoryToolIndex(backend)
    retriever = SemanticToolRetriever(index)
    tools = tool_pool()
    await index.refresh(tools)
    scored_version = index.version
    task = "Investigate the repository"

    pending = asyncio.create_task(
        retriever.retrieve(task, messages(task, "Issue #42"), tools)
    )
    await backend.started.wait()
    await index.refresh(ToolCollection(ProbeTool(name="other", description="Other")))
    backend.release.set()
    result = await pending

    assert len(result.matches) == 2
    assert result.index_version == scored_version
    assert index.version != scored_version
