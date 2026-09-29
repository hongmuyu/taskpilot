import base64
import json
import os
import re
import time
from typing import Any, Optional
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from app.logger import logger
from app.tool.base import BaseTool


class RepositoryContext(BaseModel):
    owner: str
    repo: str
    ref: Optional[str] = None

    @classmethod
    def parse(cls, identifier: str, ref: Optional[str] = None) -> "RepositoryContext":
        parts = identifier.strip().split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("Repository must use owner/repo format")
        owner, repo = parts
        if (
            len(owner) > 39
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", owner)
            or len(repo) > 100
            or repo in {".", ".."}
            or not re.fullmatch(r"[A-Za-z0-9._-]+", repo)
        ):
            raise ValueError("Repository must use valid GitHub owner/repo names")
        return cls(owner=owner, repo=repo, ref=ref)

    @property
    def api_path(self) -> str:
        return f"/repos/{self.owner}/{self.repo}"


class GitHubClient:
    def __init__(
        self,
        token: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "TaskPilot-ReadOnly-Repository-Investigation",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http_client = http_client or httpx.AsyncClient(
            base_url="https://api.github.com", headers=headers, timeout=20.0
        )
        if http_client:
            self.http_client.headers.update(headers)

    @classmethod
    def from_environment(cls) -> "GitHubClient":
        return cls(token=os.getenv("GITHUB_TOKEN"))

    async def aclose(self) -> None:
        await self.http_client.aclose()

    async def get(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        path: str,
        params: Optional[dict[str, Any]] = None,
    ) -> Any:
        started = time.perf_counter()
        status = "error"
        observation_success = False
        http_status: Any = "network_error"
        try:
            response = await self.http_client.get(path, params=params)
            http_status = response.status_code
            response.raise_for_status()
            data = response.json()
            status = "success"
            observation_success = True
            return data
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                detail = exc.response.json().get("message", "")
            except (ValueError, AttributeError):
                detail = exc.response.reason_phrase
            return _ApiError(
                f"GitHub API returned {exc.response.status_code} for {tool_name}: {detail}"
            )
        except (httpx.HTTPError, ValueError) as exc:
            return _ApiError(f"GitHub request failed for {tool_name}: {exc}")
        finally:
            logger.info(
                "github_tool name={} arguments={} status={} http_status={} latency_ms={} observation_success={}",
                tool_name,
                json.dumps(arguments, sort_keys=True),
                status,
                http_status,
                round((time.perf_counter() - started) * 1000, 1),
                observation_success,
            )


class _ApiError(str):
    pass


class GitHubReadOnlyTool(BaseTool):
    context: RepositoryContext
    client: GitHubClient = Field(exclude=True)

    class Config:
        arbitrary_types_allowed = True

    async def get(self, path: str, arguments: dict, params: Optional[dict] = None):
        result = await self.client.get(
            tool_name=self.name,
            arguments=arguments,
            path=path,
            params=params,
        )
        if isinstance(result, _ApiError):
            return self.fail_response(str(result))
        return self.success_response(result)


class GitHubRepositoryInfo(GitHubReadOnlyTool):
    name: str = "github_repository_info"
    description: str = "Read metadata and description for the configured GitHub repository."
    parameters: dict = {"type": "object", "properties": {}, "additionalProperties": False}

    async def execute(self, **kwargs):
        result = await self.get(self.context.api_path, {})
        if result.error:
            return result
        data = json.loads(result.output)
        fields = (
            "name", "full_name", "description", "default_branch", "language",
            "stargazers_count", "open_issues_count", "html_url", "updated_at", "topics",
        )
        return self.success_response({key: data.get(key) for key in fields})


class GitHubIssueSearch(GitHubReadOnlyTool):
    name: str = "github_issue_search"
    description: str = "Search issues in the configured repository using keywords or GitHub issue search qualifiers."
    parameters: dict = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Issue keywords or search qualifiers."},
            "per_page": {"type": "integer", "description": "Maximum results, from 1 to 30."},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    async def execute(self, query: str, per_page: int = 10):
        query = _without_repository_qualifier(query)
        if not query:
            return self.fail_response("Issue search query must not be empty")
        params = {
            "q": f"repo:{self.context.owner}/{self.context.repo} is:issue {query}",
            "sort": "updated",
            "order": "desc",
            "per_page": min(max(per_page, 1), 30),
        }
        result = await self.get(
            "/search/issues",
            {"query": query, "per_page": params["per_page"]},
            params,
        )
        if result.error:
            return result
        data = json.loads(result.output)
        issues = [
            {
                "number": item.get("number"),
                "title": item.get("title"),
                "state": item.get("state"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "html_url": item.get("html_url"),
                "labels": [label.get("name") for label in item.get("labels", [])],
                "body": _trim(item.get("body"), 1000),
            }
            for item in data.get("items", [])
        ]
        return self.success_response({"total_count": data.get("total_count", 0), "items": issues})


class GitHubIssueDetail(GitHubReadOnlyTool):
    name: str = "github_issue_detail"
    description: str = "Read an issue and up to ten comments from the configured repository."
    parameters: dict = {
        "type": "object",
        "properties": {"issue_number": {"type": "integer", "description": "Issue number."}},
        "required": ["issue_number"],
        "additionalProperties": False,
    }

    async def execute(self, issue_number: int):
        if isinstance(issue_number, bool) or not isinstance(issue_number, int) or issue_number < 1:
            return self.fail_response("Issue number must be a positive integer")
        issue = await self.get(f"{self.context.api_path}/issues/{issue_number}", {"issue_number": issue_number})
        if issue.error:
            return issue
        comments = await self.get(
            f"{self.context.api_path}/issues/{issue_number}/comments",
            {"issue_number": issue_number, "comments_per_page": 10},
            {"per_page": 10},
        )
        if comments.error:
            return comments
        try:
            issue_data = json.loads(issue.output)
            comment_data = json.loads(comments.output)
        except (TypeError, json.JSONDecodeError) as exc:
            return self.fail_response(f"Unexpected GitHub issue response: {exc}")
        issue_fields = (
            "number", "title", "state", "created_at", "updated_at", "html_url",
            "labels", "body",
        )
        issue_data = {key: issue_data.get(key) for key in issue_fields}
        issue_data["labels"] = [label.get("name") for label in issue_data.get("labels", [])]
        issue_data["body"] = _trim(issue_data.get("body"), 4000)
        comments = [
            {
                "user": (comment.get("user") or {}).get("login"),
                "created_at": comment.get("created_at"),
                "html_url": comment.get("html_url"),
                "body": _trim(comment.get("body"), 1500),
            }
            for comment in comment_data
        ]
        return self.success_response({"issue": issue_data, "comments": comments})


class GitHubCodeSearch(GitHubReadOnlyTool):
    name: str = "github_code_search"
    description: str = "Search source code in the configured repository. GitHub requires GITHUB_TOKEN for code search."
    parameters: dict = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Code search terms."}},
        "required": ["query"],
        "additionalProperties": False,
    }

    async def execute(self, query: str):
        query = _without_repository_qualifier(query)
        if not query:
            return self.fail_response("Code search query must not be empty")
        params = {"q": f"{query} repo:{self.context.owner}/{self.context.repo}", "per_page": 10}
        result = await self.get("/search/code", {"query": query}, params)
        if result.error:
            return result
        data = json.loads(result.output)
        matches = [
            {
                "path": item.get("path"),
                "name": item.get("name"),
                "html_url": item.get("html_url"),
            }
            for item in data.get("items", [])
        ]
        return self.success_response({"total_count": data.get("total_count", 0), "items": matches})


class GitHubReadFile(GitHubReadOnlyTool):
    name: str = "github_read_file"
    description: str = "Read a text file from the configured repository at the selected ref or default branch."
    parameters: dict = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Repository-relative file path."}},
        "required": ["path"],
        "additionalProperties": False,
    }

    async def execute(self, path: str):
        path = path.strip().strip("/")
        if not path or any(part == ".." for part in path.split("/")):
            return self.fail_response("File path must be a repository-relative path without '..' segments")
        params = {"ref": self.context.ref} if self.context.ref else None
        result = await self.get(
            f"{self.context.api_path}/contents/{quote(path, safe='/')}",
            {"path": path, **({"ref": self.context.ref} if self.context.ref else {})},
            params,
        )
        if result.error:
            return result
        try:
            data = json.loads(result.output)
            content = base64.b64decode(data["content"]).decode("utf-8")
        except (KeyError, TypeError, ValueError, UnicodeDecodeError) as exc:
            return self.fail_response(f"GitHub did not return a readable text file: {exc}")
        if len(content) > 12000:
            content = content[:12000] + "\n... [truncated at 12000 characters]"
        return self.success_response({"path": data.get("path", path), "html_url": data.get("html_url"), "content": content})


def _trim(value: Optional[str], limit: int) -> Optional[str]:
    if value is None or len(value) <= limit:
        return value
    return value[:limit] + "\n... [truncated]"


def _without_repository_qualifier(query: str) -> str:
    return re.sub(r"(?i)\brepo:[^\s]+", "", query).strip()
