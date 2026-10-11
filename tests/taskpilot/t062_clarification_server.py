"""Test-only HTTP entry point for the T062 controlled-omission deployment case.

The real provider selects github_read_file. Only its first path is removed so
the deployed service's clarification boundary can be exercised over TCP.
"""

from app.agent.repository_investigation import RepositoryInvestigationAgent
from app.config import config
from app.llm import LLM
from app.taskpilot.service import create_app


class ControlledOmissionLLM(LLM):
    injected: bool = False

    async def ask_tool(self, *args, **kwargs):
        response = await super().ask_tool(*args, **kwargs)
        if response and response.tool_calls and not self.injected:
            for call in response.tool_calls:
                if call.function.name == "github_read_file":
                    call.function.arguments = "{}"
                    self.injected = True
                    break
        return response


def make_agent(context):
    settings = config.llm["default"]
    llm = ControlledOmissionLLM(
        config_name="t062_clarification",
        llm_config={"default": settings, "t062_clarification": settings},
    )
    llm.client = llm.client.with_options(max_retries=0)
    return RepositoryInvestigationAgent.create(context, llm=llm, max_steps=5)


app = create_app(agent_factory=make_agent, clarification_timeout_seconds=90.0)
