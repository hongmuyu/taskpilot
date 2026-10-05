import asyncio
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from jsonschema import Draft7Validator


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


DATASETS = Path(__file__).resolve().parents[2] / "eval" / "datasets"
ROUTING_FILES = {
    "experiment": DATASETS / "routing_v1_experiment.json",
    "evaluation": DATASETS / "routing_v1_evaluation.json",
}
REQUIRED_CATEGORIES = {
    "repository_understanding",
    "issue_investigation",
    "issue_to_code",
    "multi_step",
    "missing_parameters",
    "similar_capabilities",
    "no_match_clarification",
}


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def real_tool_metadata():
    client = GitHubClient(http_client=httpx.AsyncClient(base_url="https://example.test"))
    context = RepositoryContext.parse("FoundationAgents/OpenManus", source="user_input")
    tools = (
        GitHubRepositoryInfo(context=context, client=client),
        GitHubIssueSearch(context=context, client=client),
        GitHubIssueDetail(context=context, client=client),
        GitHubCodeSearch(context=context, client=client),
        GitHubReadFile(context=context, client=client),
    )
    try:
        yield {item.name: item for item in ToolCollection(*tools).to_metadata()}
    finally:
        asyncio.run(client.aclose())


def test_routing_dataset_schema_and_split():
    schema = load_json(DATASETS / "routing_v1.schema.json")
    Draft7Validator.check_schema(schema)
    datasets = {split: load_json(path) for split, path in ROUTING_FILES.items()}
    validator = Draft7Validator(schema)

    for split, dataset in datasets.items():
        assert not list(validator.iter_errors(dataset))
        assert dataset["dataset"] == "taskpilot_routing"
        assert dataset["version"] == "1.1.0"
        assert dataset["split"] == split
        assert dataset["evidence_scope"] == "current_step"
        assert dataset["tool_pool"] == [
            "github_repository_info",
            "github_issue_search",
            "github_issue_detail",
            "github_code_search",
            "github_read_file",
        ]

    experiment = datasets["experiment"]["samples"]
    evaluation = datasets["evaluation"]["samples"]
    assert len(experiment) == 28
    assert len(evaluation) == 12
    assert not {item["id"] for item in experiment} & {item["id"] for item in evaluation}
    assert not {item["task"].casefold() for item in experiment} & {
        item["task"].casefold() for item in evaluation
    }
    assert all(item["origin"] == "new" for item in evaluation)
    for samples in (experiment, evaluation):
        assert len({item["id"] for item in samples}) == len(samples)
        categories = Counter(item["category"] for item in samples)
        assert REQUIRED_CATEGORIES <= categories.keys()
        assert any(len(item["acceptable_tools"]) > 1 for item in samples)
        scenarios = defaultdict(list)
        for item in samples:
            if item["category"] == "multi_step":
                scenarios[item["scenario"]].append(item)
        assert scenarios
        for steps in scenarios.values():
            assert {item["step"] for item in steps} == {1, 2}
            assert len({item["task"] for item in steps}) == 1
            assert steps[0]["context"] == steps[1]["context"]
            second = next(item for item in steps if item["step"] == 2)
            assert second["observation"]
            assert steps[0]["acceptable_tools"] != steps[1]["acceptable_tools"]
            if steps[0]["acceptable_tools"] == ["github_issue_search"]:
                assert re.search(r"issue #\d+", second["observation"], re.I)
                assert "https://" in second["observation"]
            if steps[0]["acceptable_tools"] == ["github_code_search"]:
                assert "https://" in second["observation"]


def test_v0_tasks_are_annotated_as_experiment_only():
    v0 = load_json(DATASETS / "repository_investigation_v0.json")
    experiment = load_json(ROUTING_FILES["experiment"])["samples"]
    migrated = {item["origin"]: item for item in experiment if item["origin"] != "new"}
    assert set(migrated) == {f"v0:{index}" for index in range(1, 17)}
    for index, original in enumerate(v0["tasks"], 1):
        sample = migrated[f"v0:{index}"]
        assert sample["task"] == original["task"]
        assert sample["context"]["repository"] == original["repository"]
        if index in (15, 16):
            assert sample["evidence_requirement"] != original["evidence_requirement"]
        else:
            assert sample["evidence_requirement"] == original["evidence_requirement"]


def test_labels_reference_live_tool_metadata_and_schema(real_tool_metadata):
    names = set(real_tool_metadata)
    for split, path in ROUTING_FILES.items():
        dataset = load_json(path)
        assert set(dataset["tool_pool"]) == names
        for sample in dataset["samples"]:
            selected = sample["acceptable_tools"]
            slots = sample["critical_slots"]
            assert len(selected) == len(set(selected)), sample["id"]
            assert set(selected) <= names, sample["id"]
            assert sample["label_rationale"].strip(), sample["id"]
            assert sample["evidence_requirement"].strip(), sample["id"]
            assert sample["id"].startswith(f"{split[:3]}-"), sample["id"]
            if sample["expected_action"] == "no_match":
                assert not selected and not slots, sample["id"]
            else:
                assert selected, sample["id"]
            if sample["expected_action"] == "clarify":
                assert any(slot["source"] == "missing" for slot in slots.values()), sample["id"]
            if sample["expected_action"] == "tool":
                assert all(slot["source"] != "missing" for slot in slots.values()), sample["id"]

            if sample["context"]["repository"] is None:
                assert sample["context"]["provenance"] == "absent", sample["id"]
                assert sample["context"]["ref"] is None, sample["id"]
            else:
                assert sample["context"]["provenance"] == "confirmed_user", sample["id"]

            for name, slot in slots.items():
                supported = name in {"repository", "ref"} or any(
                    name in real_tool_metadata[tool].schema.get("properties", {})
                    for tool in selected
                )
                assert supported, (sample["id"], name)
                assert ("value" in slot) == (slot["source"] != "missing"), (sample["id"], name)
                if slot["source"] == "confirmed_context":
                    assert slot["value"] == sample["context"][name], (sample["id"], name)
                if slot["source"] == "observation":
                    assert sample["observation"], (sample["id"], name)
                    if name in {"issue_number", "path"}:
                        assert str(slot["value"]) in sample["observation"], (sample["id"], name)
                if name == "query" and slot["source"] == "task" and "_" in slot["value"]:
                    assert slot["value"] in sample["task"], (sample["id"], name)
                if name == "repository" and slot["source"] == "missing":
                    assert sample["context"]["repository"] is None, sample["id"]
                if "value" in slot and isinstance(slot["value"], str):
                    assert slot["value"].strip(), (sample["id"], name)

            for tool_name in selected:
                metadata = real_tool_metadata[tool_name]
                assert metadata.source == "local"
                assert metadata.schema is metadata.tool.parameters
                for required in metadata.schema.get("required", []):
                    assert required in slots, (sample["id"], tool_name, required)
                    if sample["expected_action"] == "tool":
                        assert slots[required]["source"] != "missing", (sample["id"], tool_name, required)
                for name, slot in slots.items():
                    property_schema = metadata.schema.get("properties", {}).get(name)
                    if property_schema and "value" in slot:
                        assert not list(Draft7Validator(property_schema).iter_errors(slot["value"])), (sample["id"], tool_name, name)
