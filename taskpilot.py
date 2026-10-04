import argparse
import asyncio

from app.agent.repository_investigation import RepositoryInvestigationAgent
from app.taskpilot.github_tools import RepositoryContext


async def run(repository: str, prompt: str, ref: str | None = None) -> str:
    context = RepositoryContext.parse(repository, ref=ref, source="user_input")
    agent = RepositoryInvestigationAgent.create(context)
    try:
        return await agent.run(prompt)
    finally:
        await agent.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Investigate a GitHub repository with read-only tools"
    )
    parser.add_argument(
        "--repository", required=True, help="Target GitHub repository as owner/repo"
    )
    parser.add_argument(
        "--ref", help="Optional branch, tag, or commit to use for file reads"
    )
    parser.add_argument(
        "--prompt", required=True, help="Natural-language repository investigation task"
    )
    args = parser.parse_args()
    print(asyncio.run(run(args.repository, args.prompt, args.ref)))


if __name__ == "__main__":
    main()
