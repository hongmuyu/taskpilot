import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

with patch(
    "tomllib.load",
    return_value={
        "llm": {"model": "test", "base_url": "http://127.0.0.1:9/v1", "api_key": "test"},
        "daytona": {"daytona_api_key": "unused-test"},
    },
):
    from app.taskpilot.github_tools import (
        GitHubClient,
        GitHubCodeSearch,
        GitHubIssueDetail,
        GitHubIssueSearch,
        GitHubReadFile,
        GitHubRepositoryInfo,
        RepositoryContext,
    )


def api_client(handler):
    return GitHubClient(http_client=httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    ))


def test_repository_context_parses_owner_repo_and_rejects_invalid_identifier():
    context = RepositoryContext.parse("octo-org/sample-repo", ref="release/v2")

    assert (context.owner, context.repo, context.ref) == ("octo-org", "sample-repo", "release/v2")
    with pytest.raises(ValueError, match="owner/repo"):
        RepositoryContext.parse("sample-repo")
    assert RepositoryContext.parse("octo-org/.github").repo == ".github"


@pytest.mark.asyncio
async def test_repository_metadata_reads_configured_repository():
    def respond(request):
        assert request.url.path == "/repos/octo-org/sample-repo"
        return httpx.Response(200, json={
            "full_name": "octo-org/sample-repo",
            "description": "A sample repository",
            "language": "Python",
            "private": False,
        })

    tool = GitHubRepositoryInfo(
        context=RepositoryContext.parse("octo-org/sample-repo"),
        client=api_client(respond),
    )
    result = await tool.execute()

    assert "A sample repository" in result.output
    assert '"language": "Python"' in result.output
    assert "private" not in result.output


@pytest.mark.asyncio
async def test_issue_search_maps_query_to_repository_scoped_github_search():
    captured = {}

    def respond(request):
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"items": [{"number": 12, "title": "MCP timeout", "html_url": "https://github.com/octo-org/sample-repo/issues/12"}]})

    tool = GitHubIssueSearch(
        context=RepositoryContext.parse("octo-org/sample-repo"),
        client=api_client(respond),
    )
    result = await tool.execute(query="MCP timeout repo:elsewhere/other", per_page=5)

    assert "repo:octo-org/sample-repo is:issue MCP timeout" == captured["params"]["q"]
    assert captured["params"]["per_page"] == "5"
    assert "MCP timeout" in result.output


@pytest.mark.asyncio
async def test_file_read_decodes_github_contents_api_response():
    def respond(request):
        assert request.url.path == "/repos/octo-org/sample-repo/contents/src/client.py"
        assert request.url.params["ref"] == "main"
        return httpx.Response(200, json={
            "path": "src/client.py",
            "html_url": "https://github.com/octo-org/sample-repo/blob/main/src/client.py",
            "encoding": "base64",
            "content": base64.b64encode(b"timeout = 30\n").decode(),
        })

    tool = GitHubReadFile(
        context=RepositoryContext.parse("octo-org/sample-repo", ref="main"),
        client=api_client(respond),
    )
    result = await tool.execute(path="src/client.py")

    assert "timeout = 30" in result.output
    assert "https://github.com/octo-org/sample-repo/blob/main/src/client.py" in result.output


@pytest.mark.asyncio
async def test_issue_detail_reads_issue_and_discussion_comments():
    def respond(request):
        if request.url.path.endswith("/comments"):
            return httpx.Response(200, json=[{
                "user": {"login": "octocat"},
                "created_at": "2026-01-01T00:00:00Z",
                "html_url": "https://github.com/octo-org/sample-repo/issues/12#issuecomment-1",
                "body": "I can reproduce this.",
            }])
        assert request.url.path.endswith("/issues/12")
        return httpx.Response(200, json={
            "number": 12,
            "title": "MCP timeout",
            "state": "open",
            "html_url": "https://github.com/octo-org/sample-repo/issues/12",
            "body": "The request times out.",
            "labels": [{"name": "bug"}],
            "user": {"login": "octocat"},
        })

    tool = GitHubIssueDetail(
        context=RepositoryContext.parse("octo-org/sample-repo"),
        client=api_client(respond),
    )
    result = await tool.execute(issue_number=12)

    assert "The request times out" in result.output
    assert "I can reproduce this" in result.output
    assert "octocat" in result.output


@pytest.mark.asyncio
async def test_issue_detail_rejects_non_positive_issue_number_before_request():
    client = api_client(lambda request: pytest.fail("invalid issue number made an HTTP request"))
    tool = GitHubIssueDetail(
        context=RepositoryContext.parse("octo-org/sample-repo"),
        client=client,
    )

    result = await tool.execute(issue_number=-1)

    assert "positive integer" in result.error


@pytest.mark.asyncio
async def test_code_search_maps_query_and_repository_qualifier():
    def respond(request):
        assert request.url.path == "/search/code"
        assert request.url.params["q"] == "timeout repo:octo-org/sample-repo"
        return httpx.Response(200, json={"total_count": 1, "items": [{
            "path": "src/client.py",
            "name": "client.py",
            "html_url": "https://github.com/octo-org/sample-repo/blob/main/src/client.py",
            "repository": {"full_name": "octo-org/sample-repo"},
        }]})

    tool = GitHubCodeSearch(
        context=RepositoryContext.parse("octo-org/sample-repo"),
        client=api_client(respond),
    )
    result = await tool.execute(query="timeout repo:someone/elsewhere")

    assert "src/client.py" in result.output
    assert "https://github.com/octo-org/sample-repo/blob/main/src/client.py" in result.output
    assert "repository" not in result.output


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_factory,arguments", [
    (lambda client: GitHubRepositoryInfo(context=RepositoryContext.parse("o/r"), client=client), {}),
    (lambda client: GitHubIssueSearch(context=RepositoryContext.parse("o/r"), client=client), {"query": "bug"}),
    (lambda client: GitHubIssueDetail(context=RepositoryContext.parse("o/r"), client=client), {"issue_number": 4}),
    (lambda client: GitHubCodeSearch(context=RepositoryContext.parse("o/r"), client=client), {"query": "timeout"}),
    (lambda client: GitHubReadFile(context=RepositoryContext.parse("o/r"), client=client), {"path": "src/main.py"}),
])
async def test_github_api_error_is_returned_as_tool_failure(tool_factory, arguments):
    tool = tool_factory(api_client(lambda request: httpx.Response(404, json={"message": "Not Found"})))

    result = await tool.execute(**arguments)

    assert result.error
    assert "404" in result.error


@pytest.mark.asyncio
async def test_investigation_agent_exposes_only_read_only_github_tools_and_observes_results():
    with patch(
        "tomllib.load",
        return_value={
            "llm": {"model": "test", "base_url": "http://127.0.0.1:9/v1", "api_key": "test"},
            "daytona": {"daytona_api_key": "unused-test"},
        },
    ):
        from app.agent.repository_investigation import RepositoryInvestigationAgent
        from app.schema import Function, ToolCall

    context = RepositoryContext.parse("octo-org/sample-repo")
    mock_api = api_client(lambda request: httpx.Response(200, json={"items": [{"number": 12, "title": "MCP timeout"}]}))
    llm = object.__new__(__import__("app.llm", fromlist=["LLM"]).LLM)
    search = ToolCall(id="search-1", function=Function(name="github_issue_search", arguments=json.dumps({"query": "MCP"})))
    stop = ToolCall(id="stop-1", function=Function(name="terminate", arguments='{"status":"success"}'))
    requests = []

    async def ask_tool(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(content=None, tool_calls=[search] if len(requests) == 1 else [stop])

    llm.ask_tool = AsyncMock(side_effect=ask_tool)
    agent = RepositoryInvestigationAgent.create(context, client=mock_api, llm=llm, max_steps=3)

    await agent.run("Find recent MCP issues")

    exposed = {tool["function"]["name"] for tool in requests[0]["tools"]}
    second_turn = [message for message in requests[1]["messages"] if message.tool_call_id == "search-1"]
    assert exposed == {
        "github_repository_info", "github_issue_search", "github_issue_detail",
        "github_code_search", "github_read_file", "terminate",
    }
    assert len(second_turn) == 1 and "MCP timeout" in second_turn[0].content
