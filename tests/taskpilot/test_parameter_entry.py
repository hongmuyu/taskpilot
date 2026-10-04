import sys
from unittest.mock import patch

import pytest


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
    import taskpilot


class FakeAgent:
    def __init__(self):
        self.requests = []
        self.cleaned = False

    async def run(self, request):
        self.requests.append(request)
        return "completed"

    async def cleanup(self):
        self.cleaned = True


@pytest.mark.asyncio
async def test_missing_repository_forces_cli_clarification_before_agent_creation(
    monkeypatch,
):
    prompts = []
    created = []
    agent = FakeAgent()

    async def answer(self, inquire):
        prompts.append(inquire)
        assert created == []
        return "FoundationAgents/OpenManus"

    monkeypatch.setattr(taskpilot.AskHuman, "execute", answer)
    monkeypatch.setattr(
        taskpilot.RepositoryInvestigationAgent,
        "create",
        lambda context: created.append(context) or agent,
    )

    result = await taskpilot.run(None, "Inspect the repository", ref="main")

    assert result == "completed"
    assert len(prompts) == 1 and "repository" in prompts[0].lower()
    assert created[0].owner == "FoundationAgents"
    assert created[0].repo == "OpenManus"
    assert created[0].ref == "main"
    assert created[0].source == "user_input"
    assert agent.requests == ["Inspect the repository"]
    assert agent.cleaned


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["cancel", "", "invalid"])
async def test_cancel_empty_or_invalid_repository_never_creates_agent(
    monkeypatch, reply
):
    async def answer(self, inquire):
        return reply

    monkeypatch.setattr(taskpilot.AskHuman, "execute", answer)
    monkeypatch.setattr(
        taskpilot.RepositoryInvestigationAgent,
        "create",
        lambda context: pytest.fail("unconfirmed repository reached agent creation"),
    )

    with pytest.raises(ValueError):
        await taskpilot.run(None, "Inspect the repository")


def test_cli_accepts_missing_repository_for_clarification(monkeypatch, capsys):
    received = []

    async def run(repository, prompt, ref=None):
        received.append((repository, prompt, ref))
        return "completed"

    monkeypatch.setattr(taskpilot, "run", run)
    monkeypatch.setattr(
        sys, "argv", ["taskpilot.py", "--prompt", "Inspect the repository"]
    )

    taskpilot.main()

    assert received == [(None, "Inspect the repository", None)]
    assert capsys.readouterr().out.strip() == "completed"
