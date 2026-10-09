import asyncio
import math

import pytest
from pydantic import Field

from app.taskpilot.tool_embedding_index import EmbeddingIndexError, InMemoryToolIndex
from app.tool.base import BaseTool, ToolResult
from app.tool.mcp import MCPClientTool
from app.tool.tool_collection import ToolCollection


class ProbeTool(BaseTool):
    name: str = "issues"
    description: str = "Search issues"
    parameters: dict = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )

    async def execute(self, **kwargs):
        return ToolResult(output="unused")


class FixedVectors:
    model_id = "fixed-vectors"
    revision = "fixture-1"
    vector_version = "fixed-v1"

    def __init__(self):
        self.calls = []
        self.fail_on_call = None
        self.invalid_vectors = False

    async def embed(self, texts):
        self.calls.append(tuple(texts))
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("embedding unavailable")
        if self.invalid_vectors:
            return [[math.nan, 0.0] for _ in texts]
        return [[1.0, 0.0] if "issue" in text.lower() else [0.0, 1.0] for text in texts]


@pytest.mark.asyncio
async def test_fixed_vectors_rank_all_current_tools_without_changing_tool_schemas():
    backend = FixedVectors()
    issues = ProbeTool()
    files = ProbeTool(name="files", description="Read files")
    tools = ToolCollection(issues, files)
    original_params = tools.to_params()
    index = InMemoryToolIndex(backend)

    hits = await index.search("issue", tools)

    assert [(hit.name, hit.identity) for hit in hits] == [
        ("issues", "local:issues"),
        ("files", "local:files"),
    ]
    assert hits[0].score == pytest.approx(1.0)
    assert hits[1].score == pytest.approx(0.0)
    assert index.version.model_id == "fixed-vectors"
    assert index.version.model_revision == "fixture-1"
    assert index.version.vector_version
    assert tools.to_params() == original_params
    assert [hit.name for hit in await index.search("file", tools)] == [
        "files",
        "issues",
    ]
    assert [len(call) for call in backend.calls] == [2, 1, 1]


@pytest.mark.asyncio
async def test_empty_tool_pool_has_empty_query_result_without_embedding_call():
    backend = FixedVectors()
    index = InMemoryToolIndex(backend)

    assert await index.search("issue", ToolCollection()) == []
    assert backend.calls == []
    assert index.version is not None


@pytest.mark.asyncio
async def test_add_and_remove_rebuilds_from_current_collection():
    backend = FixedVectors()
    index = InMemoryToolIndex(backend)
    issues = ProbeTool()
    files = ProbeTool(name="files", description="Read files")
    tools = ToolCollection(issues)

    assert [hit.name for hit in await index.search("issue", tools)] == ["issues"]
    first_version = index.version
    tools.add_tool(files)
    assert {hit.name for hit in await index.search("issue", tools)} == {
        "issues",
        "files",
    }
    assert index.version.content_fingerprint != first_version.content_fingerprint

    remaining = ToolCollection(files)
    assert [hit.name for hit in await index.search("file", remaining)] == ["files"]
    assert index.version.content_fingerprint != first_version.content_fingerprint
    assert [len(call) for call in backend.calls] == [1, 1, 2, 1, 1, 1]


@pytest.mark.asyncio
async def test_schema_and_metadata_mutation_rebuilds_live_snapshot():
    backend = FixedVectors()
    index = InMemoryToolIndex(backend)
    tool = ProbeTool()
    tools = ToolCollection(tool)

    await index.search("issue", tools)
    versions = [index.version.content_fingerprint]
    tool.parameters["properties"]["number"] = {"type": "integer"}
    await index.search("issue", tools)
    versions.append(index.version.content_fingerprint)
    tool.description = "Inspect issue details"
    await index.search("issue", tools)
    versions.append(index.version.content_fingerprint)
    tool.capabilities = ("issue_investigation",)
    tool.examples = ("Explain issue 42",)
    await index.search("issue", tools)
    versions.append(index.version.content_fingerprint)

    assert len(set(versions)) == 4
    assert len(backend.calls) == 8


@pytest.mark.asyncio
async def test_mcp_metadata_and_model_revision_are_part_of_version():
    backend = FixedVectors()
    index = InMemoryToolIndex(backend)
    remote = MCPClientTool(
        name="mcp_fixture_issues",
        description="Search issues",
        parameters={"type": "object", "properties": {}},
        server_id="fixture",
        original_name="issues.search",
    )
    tools = ToolCollection(remote)

    hits = await index.search("issue", tools)
    first_version = index.version
    assert hits[0].identity == "mcp:fixture:issues.search"
    backend.revision = "fixture-2"
    await index.search("issue", tools)
    assert index.version.model_revision == "fixture-2"
    assert index.version != first_version
    backend.vector_version = "fixed-v2"
    await index.search("issue", tools)
    assert index.version.vector_version.endswith("fixed-v2")
    assert len(backend.calls) == 6


@pytest.mark.asyncio
async def test_rebuild_failure_never_returns_stale_results_and_can_retry():
    backend = FixedVectors()
    index = InMemoryToolIndex(backend)
    tool = ProbeTool()
    tools = ToolCollection(tool)
    await index.search("issue", tools)

    tool.description = "Updated issue search"
    backend.fail_on_call = 3
    with pytest.raises(EmbeddingIndexError, match="embedding unavailable"):
        await index.search("issue", tools)
    assert index.version is None

    backend.fail_on_call = None
    assert [hit.name for hit in await index.search("issue", tools)] == ["issues"]
    assert index.version is not None


@pytest.mark.asyncio
async def test_non_finite_vectors_and_query_failure_are_explicit():
    backend = FixedVectors()
    index = InMemoryToolIndex(backend)
    tools = ToolCollection(ProbeTool())
    backend.invalid_vectors = True
    with pytest.raises(EmbeddingIndexError, match="invalid embedding vector"):
        await index.search("issue", tools)
    assert index.version is None

    backend.invalid_vectors = False
    backend.fail_on_call = 3
    with pytest.raises(EmbeddingIndexError, match="embedding unavailable"):
        await index.search("issue", tools)
    assert index.version is not None


@pytest.mark.asyncio
async def test_query_uses_one_snapshot_when_pool_rebuilds_during_embedding():
    class DelayedQueryVectors(FixedVectors):
        def __init__(self):
            super().__init__()
            self.query_started = asyncio.Event()
            self.release_query = asyncio.Event()

        async def embed(self, texts):
            if list(texts) == ["issue"]:
                self.query_started.set()
                await self.release_query.wait()
            return await super().embed(texts)

    backend = DelayedQueryVectors()
    index = InMemoryToolIndex(backend)
    issues = ProbeTool()
    files = ProbeTool(name="files", description="Read files")
    pending = asyncio.create_task(index.search("issue", ToolCollection(issues, files)))
    await backend.query_started.wait()
    await index.refresh(ToolCollection(files))
    backend.release_query.set()

    assert [hit.name for hit in await pending] == ["issues", "files"]
