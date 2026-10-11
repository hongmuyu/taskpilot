"""Offline integrity checks for the frozen Core holdout and scoring rubric."""

import asyncio
import hashlib
import importlib.metadata
import json
import re
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from jsonschema import Draft7Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
DATASETS = ROOT / "eval" / "datasets"
BENCHMARK = DATASETS / "core_benchmark_v1_holdout.json"
SCHEMA = DATASETS / "core_benchmark_v1.schema.json"
SOURCES = DATASETS / "core_benchmark_v1_sources.json"
SCORING = DATASETS / "core_benchmark_v1_scoring.json"
REVIEW = ROOT / "docs" / "T065_BENCHMARK_REVIEW.json"
OLD_SETS = (
    DATASETS / "routing_v1_experiment.json",
    DATASETS / "routing_v1_evaluation.json",
)
CATEGORIES = {
    "repository_understanding",
    "issue_investigation",
    "issue_to_code",
    "parameter_validation",
    "semantic_routing",
    "real_mcp",
    "failure_timeout_retry",
    "multi_step_investigation",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_refs(catalog):
    refs = set()
    for repo in catalog["github_repositories"]:
        refs.update(f'{repo["id"]}:{item["path"]}' for item in repo["files"])
        refs.update(f'{repo["id"]}#issue:{item["number"]}' for item in repo["issues"])
    return refs


def test_schema_and_frozen_holdout_are_valid():
    assert BENCHMARK.exists(), "T065 holdout is missing"
    assert SCHEMA.exists(), "T065 schema is missing"
    schema = read_json(SCHEMA)
    Draft7Validator.check_schema(schema)
    dataset = read_json(BENCHMARK)
    assert not list(
        Draft7Validator(schema, format_checker=FormatChecker()).iter_errors(dataset)
    )
    assert (dataset["dataset"], dataset["version"], dataset["split"]) == (
        "taskpilot_core_final",
        "1.0.0",
        "independent_holdout",
    )
    assert len(dataset["samples"]) == 21
    assert len({sample["id"] for sample in dataset["samples"]}) == 21
    assert {sample["category"] for sample in dataset["samples"]} == CATEGORIES

    malformed = deepcopy(dataset)
    del malformed["samples"][0]["business_evidence"]
    assert list(Draft7Validator(schema).iter_errors(malformed))


def test_prior_routing_sets_are_provenance_only_and_holdout_is_distinct():
    dataset = read_json(BENCHMARK)
    prior = {item["path"]: item for item in dataset["prior_sets"]}
    assert set(prior) == {str(path.relative_to(ROOT)) for path in OLD_SETS}
    assert {item["role"] for item in prior.values()} == {
        "k_selection_experiment",
        "routing_stage_evaluation_used_in_t030",
    }
    previous_repositories = set()
    previous_tasks = set()
    for path in OLD_SETS:
        entry = prior[str(path.relative_to(ROOT))]
        old = read_json(path)
        assert entry["version"] == old["version"] == "1.1.0"
        assert entry["sample_count"] == len(old["samples"])
        assert entry["sha256"] == digest(path)
        previous_repositories.update(
            sample["context"]["repository"]
            for sample in old["samples"]
            if sample["context"]["repository"]
        )
        previous_tasks.update(sample["task"].casefold() for sample in old["samples"])
    holdout = dataset["samples"]
    assert not previous_repositories & {
        item["context"]["repository"] for item in holdout
    }
    assert not previous_tasks & {item["task"].casefold() for item in holdout}
    assert all(item["id"].startswith("core-") for item in holdout)


def test_source_catalog_and_live_tool_schema_references_are_stable():
    dataset = read_json(BENCHMARK)
    catalog = read_json(SOURCES)
    assert dataset["source_catalog_sha256"] == digest(SOURCES)
    assert len(catalog["github_repositories"]) == 3
    assert sum(len(repo["issues"]) for repo in catalog["github_repositories"]) == 8
    assert sum(len(repo["files"]) for repo in catalog["github_repositories"]) == 11
    for repo in catalog["github_repositories"]:
        assert re.fullmatch(r"[0-9a-f]{40}", repo["ref"])
        for item in repo["files"]:
            assert re.fullmatch(r"[0-9a-f]{40}", item["blob_sha"])
            assert f'/blob/{repo["ref"]}/' in item["url"]
        for item in repo["issues"]:
            assert re.fullmatch(r"[0-9a-f]{64}", item["body_sha256"])
            assert item["updated_at"] and item["url"].endswith(
                f'/issues/{item["number"]}'
            )

    mcp = catalog["mcp_server"]
    assert mcp["transport"] == "stdio"
    assert mcp["mcp_sdk_version"] == importlib.metadata.version("mcp") == "1.5.0"
    assert mcp["source_sha256"] == digest(ROOT / mcp["source_path"])
    assert {variant["name"] for variant in mcp["variants"]} == {"v1", "v2"}

    with patch(
        "tomllib.load",
        return_value={
            "llm": {
                "model": "test",
                "base_url": "http://127.0.0.1:9/v1",
                "api_key": "test",
            },
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
        from app.tool.tool_collection import ToolCollection

    client = GitHubClient(
        http_client=httpx.AsyncClient(base_url="https://example.test")
    )
    context = RepositoryContext.parse("encode/httpx", source="user_input")
    metadata = ToolCollection(
        *(
            cls(context=context, client=client)
            for cls in (
                GitHubRepositoryInfo,
                GitHubIssueSearch,
                GitHubIssueDetail,
                GitHubCodeSearch,
                GitHubReadFile,
            )
        )
    ).to_metadata()
    actual = {
        item.name: hashlib.sha256(
            json.dumps(item.schema, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for item in metadata
    }
    asyncio.run(client.aclose())
    assert {
        item["name"]: item["schema_sha256"] for item in catalog["local_tools"]
    } == actual


@pytest.mark.asyncio
async def test_frozen_mcp_schemas_match_real_stdio_discovery(tmp_path):
    catalog = read_json(SOURCES)
    with patch(
        "tomllib.load",
        return_value={
            "llm": {
                "model": "test",
                "base_url": "http://127.0.0.1:9/v1",
                "api_key": "test",
            },
            "daytona": {"daytona_api_key": "unused-test"},
        },
    ):
        from app.tool.mcp import MCPClients

    fixture = ROOT / catalog["mcp_server"]["source_path"]
    for variant in catalog["mcp_server"]["variants"]:
        audit = tmp_path / f'{variant["name"]}.jsonl'
        clients = MCPClients(connect_timeout_seconds=5, cleanup_timeout_seconds=2)
        try:
            await clients.connect_stdio(
                sys.executable,
                [str(fixture)],
                server_id="fixture",
                env={
                    "TASKPILOT_MCP_AUDIT_PATH": str(audit),
                    "TASKPILOT_MCP_VARIANT": variant["name"],
                },
            )
            discovered = {
                name: {
                    "original_name": tool.original_name,
                    "input_schema": tool.parameters,
                }
                for name, tool in clients.tool_map.items()
            }
            expected = {
                tool["public_name"]: {
                    "original_name": tool["original_name"],
                    "input_schema": tool["input_schema"],
                }
                for tool in variant["tools"]
            }
            assert discovered == expected
            assert not audit.exists(), "Discovery must not dispatch any server tool"
        finally:
            await clients.disconnect("fixture")


def test_step_labels_slots_and_evidence_references_are_coherent():
    dataset = read_json(BENCHMARK)
    catalog = read_json(SOURCES)
    repo_by_id = {item["id"]: item for item in catalog["github_repositories"]}
    refs = source_refs(catalog)
    mcp_schemas = {
        variant["name"]: {
            tool["public_name"]: tool["input_schema"] for tool in variant["tools"]
        }
        for variant in catalog["mcp_server"]["variants"]
    }
    profile_tools = {item["id"]: set(item["tools"]) for item in dataset["profiles"]}
    assert profile_tools["github_readonly"] == {
        item["name"] for item in catalog["local_tools"]
    }
    for variant in ("v1", "v2"):
        assert profile_tools[f"mcp_stdio_{variant}"] == set(mcp_schemas[variant])

    for sample in dataset["samples"]:
        profile = sample["profile"]
        source = repo_by_id.get(sample["source_id"])
        context = sample["context"]
        if profile == "github_readonly":
            assert source is not None, sample["id"]
            assert context["repository"] == source["repository"], sample["id"]
            assert context["ref"] == source["ref"], sample["id"]
        else:
            assert source is None and context["repository"] is None, sample["id"]
        assert sample["failure_conditions"] and sample["unsupported_conditions"]
        assert sample["business_evidence"]
        setup = sample["setup"]
        if setup["mode"] != "natural_task":
            assert setup["tool"] in profile_tools[profile]
            if "arguments_json" in setup:
                try:
                    arguments = json.loads(setup["arguments_json"])
                except json.JSONDecodeError:
                    assert sample["id"] == "core-009"
                else:
                    assert isinstance(arguments, dict)
        if "clarification_reply" in setup:
            assert any(step["expected_action"] == "clarify" for step in sample["steps"])
        assert [step["index"] for step in sample["steps"]] == list(
            range(1, len(sample["steps"]) + 1)
        )
        for evidence in sample["business_evidence"]:
            if evidence["source_ref"] is not None:
                assert evidence["source_ref"] in refs, sample["id"]
        for step in sample["steps"]:
            action = step["expected_action"]
            tools = step["acceptable_tools"]
            execution = step["expected_execution"]
            assert set(tools) <= profile_tools[profile], sample["id"]
            assert len(tools) == len(set(tools)), sample["id"]
            assert step["label_rationale"].strip(), sample["id"]
            if action == "tool":
                assert tools and step["pending_tool"] is None
                assert execution["tool_dispatches"] == 1
                assert execution["transport_attempts"] >= 1
                if execution["transport_attempts"] > 1:
                    assert setup["mode"] == "controlled_fault"
            else:
                assert not tools and execution["tool_dispatches"] == 0
                assert execution["transport_attempts"] == 0
                assert not step["selection_eligible"]
                if action in {"clarify", "reject"}:
                    assert step["pending_tool"] in profile_tools[profile]
            if step["selection_eligible"]:
                assert action == "tool" and sample["setup"]["mode"] == "natural_task"
            observation = step["observation"]
            if step["index"] == 1:
                assert observation is None
            else:
                assert observation["from_step"] == step["index"] - 1
                if observation["source_ref"] is not None:
                    assert observation["source_ref"] in refs
            for name, slot in step["critical_slots"].items():
                if slot["source"] == "missing":
                    assert "value" not in slot
                else:
                    assert "value" in slot
                if slot["source"] == "confirmed_context":
                    assert name in {"repository", "ref"}
                    assert slot["value"] == context[name]
                if slot["source"] == "user_reply":
                    assert setup["clarification_reply"][name] == slot["value"]
                if slot["source"] == "invalid":
                    assert sample["id"] == "core-010" and slot["value"] is True
                if name in {"repository", "ref"}:
                    continue
                relevant = tools or [step["pending_tool"]]
                if profile.startswith("mcp_stdio_"):
                    properties = mcp_schemas[profile.removeprefix("mcp_stdio_")]
                    assert any(
                        name in properties[tool].get("properties", {})
                        for tool in relevant
                    )
                else:
                    assert name in {"query", "issue_number", "path", "per_page"}

    counts = Counter(sample["category"] for sample in dataset["samples"])
    assert counts == {
        "repository_understanding": 2,
        "issue_investigation": 2,
        "issue_to_code": 2,
        "parameter_validation": 4,
        "semantic_routing": 2,
        "real_mcp": 3,
        "failure_timeout_retry": 4,
        "multi_step_investigation": 2,
    }
    assert any(
        len(step["acceptable_tools"]) > 1
        for sample in dataset["samples"]
        for step in sample["steps"]
    )
    assert any(
        len({tuple(step["acceptable_tools"]) for step in sample["steps"]}) > 1
        for sample in dataset["samples"]
        if sample["category"] == "multi_step_investigation"
    )


def test_scoring_and_review_are_frozen_before_any_run():
    dataset = read_json(BENCHMARK)
    scoring = read_json(SCORING)
    review = read_json(REVIEW)
    assert dataset["scoring_sha256"] == digest(SCORING)
    assert scoring["version"] == dataset["version"] == "1.0.0"
    assert scoring["frozen_before_runs"] is True
    assert scoring["benchmark_runs_recorded"] == 0
    metrics = scoring["metrics"]
    assert set(metrics) == {
        "candidate_recall",
        "tool_selection_accuracy",
        "task_success_rate",
        "slot_precision_recall_f1",
        "provider_token_usage",
        "end_to_end_latency",
    }
    assert metrics["tool_selection_accuracy"]["eligible_actions"] == ["tool"]
    assert metrics["task_success_rate"]["finish_declaration_is_success"] is False
    assert scoring["status_policy"]["unsupported"] == "predeclared_exclude_and_report"
    assert (
        scoring["status_policy"]["not_verified"]
        == "primary_denominator_non_success_and_report"
    )
    assert scoring["status_policy"]["failed"] == "primary_denominator_non_success"
    assert metrics["provider_token_usage"]["missing_usage"] == "unknown_not_zero"
    assert metrics["end_to_end_latency"]["includes_cleanup"] is True
    assert metrics["end_to_end_latency"]["includes_clarification_wait"] is True
    assert review["dataset_version"] == "1.0.0"
    assert review["method"] == "manual_source_and_label_crosscheck_by_agent"
    assert {item["id"] for item in review["items"]} == {
        item["id"] for item in dataset["samples"]
    }
    assert len(review["items"]) == len(dataset["samples"])
    assert all(
        item["decision"] == "approved" and item["note"] for item in review["items"]
    )
