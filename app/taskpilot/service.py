"""Small process-local HTTP entry point for read-only repository tasks."""

import asyncio
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.taskpilot.github_tools import RepositoryContext


class StartTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str = Field(min_length=3, max_length=141, strict=True)
    task: str = Field(min_length=1, max_length=4000, strict=True)
    ref: str | None = Field(default=None, min_length=1, max_length=255, strict=True)

    @field_validator("repository")
    @classmethod
    def valid_repository(cls, value: str) -> str:
        value = value.strip()
        RepositoryContext.parse(value, source="user_input")
        return value

    @field_validator("task")
    @classmethod
    def nonempty_task(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Task must not be empty")
        return value

    @field_validator("ref")
    @classmethod
    def valid_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value)
            or ".." in value
            or "//" in value
            or value.endswith(("/", ".", ".lock"))
        ):
            raise ValueError("Invalid GitHub ref")
        return value


@dataclass
class TaskRecord:
    task_id: str
    repository: str
    ref: str | None
    status: str = "queued"
    execution_status: str | None = None
    finish_source: str | None = None
    business_outcome: str = "not_verified"
    failure_observation_count: int = 0
    cleanup_status: str = "pending"
    run_id: str | None = None
    result: str | None = None
    error: str | None = None
    job: asyncio.Task | None = field(default=None, repr=False)

    def response(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "repository": self.repository,
            "ref": self.ref,
            "status": self.status,
            "execution_status": self.execution_status,
            "finish_source": self.finish_source,
            "business_outcome": self.business_outcome,
            "failure_observation_count": self.failure_observation_count,
            "cleanup_status": self.cleanup_status,
            "run_id": self.run_id,
            "result": self.result,
            "error": self.error,
        }


def _default_agent(context: RepositoryContext):
    from app.agent.repository_investigation import RepositoryInvestigationAgent

    return RepositoryInvestigationAgent.create(context)


def create_app(
    agent_factory: Callable | None = None, routing_top_k: int | None = 3
) -> FastAPI:
    records: dict[str, TaskRecord] = {}
    factory = agent_factory or _default_agent

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        active = [
            record
            for record in records.values()
            if record.job and not record.job.done()
        ]
        for record in active:
            if record.status == "queued":
                record.status = "cancelled"
                record.execution_status = "cancelled"
                record.cleanup_status = "not_required"
            else:
                record.status = "cancelling"
            record.job.cancel()
        if active:
            _, pending = await asyncio.wait(
                [record.job for record in active], timeout=15.0
            )
            for record in active:
                if record.job in pending:
                    record.status = "unknown"
                    record.execution_status = "unknown"
                    record.cleanup_status = "unknown"
                    record.error = "shutdown_cleanup_timeout"

    app = FastAPI(title="TaskPilot read-only service", lifespan=lifespan)

    async def execute_task(record: TaskRecord, request: StartTaskRequest) -> None:
        agent = None
        final_status = "failed"
        record.status = "running"
        try:
            context = RepositoryContext.parse(
                request.repository, ref=request.ref, source="user_input"
            )
            agent = factory(context)
            agent.routing_top_k = routing_top_k
            agent.interactive_clarification = False
            record.result = await agent.run(request.task)
            events = agent.execution_trace.events if agent.execution_trace else []
            record.run_id = (
                agent.execution_trace.run_id if agent.execution_trace else None
            )
            finish = events[-1] if events and events[-1]["event"] == "finish" else None
            record.execution_status = finish["status"] if finish else "unknown"
            record.finish_source = finish["finish_source"] if finish else None
            if any(
                event["event"] == "clarification" and event["status"] == "pending"
                for event in events
            ):
                final_status = "clarification_unsupported"
                record.error = "clarification_not_supported"
            else:
                final_status = {
                    "success": "finished",
                    "failure": "failed",
                    "cancelled": "cancelled",
                }.get(record.execution_status, "unknown")
        except asyncio.CancelledError:
            final_status = "cancelled"
            record.execution_status = "cancelled"
        except Exception:
            final_status = "failed"
            record.execution_status = "failure"
            record.error = "agent_execution_failed"
        finally:
            if agent is None:
                record.cleanup_status = "not_required"
            else:
                if agent.execution_trace is not None:
                    record.run_id = agent.execution_trace.run_id
                    events = agent.execution_trace.events
                    if events and events[-1]["event"] == "finish":
                        record.finish_source = events[-1]["finish_source"]
                    record.failure_observation_count = sum(
                        event["event"] == "observation" and event["status"] == "failure"
                        for event in events
                    )
                try:
                    if not agent.github_client.http_client.is_closed:
                        await asyncio.wait_for(
                            agent.cleanup(), timeout=agent.cleanup_timeout_seconds
                        )
                    record.cleanup_status = (
                        "complete"
                        if agent.github_client.http_client.is_closed
                        else "unknown"
                    )
                except TimeoutError:
                    record.cleanup_status = "unknown"
                except Exception:
                    record.cleanup_status = "failed"
                if record.cleanup_status != "complete":
                    record.error = "cleanup_incomplete"
                    if final_status == "finished":
                        final_status = "failed"
            record.status = final_status

    def find_task(task_id: UUID) -> TaskRecord:
        record = records.get(task_id.hex)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return record

    @app.post("/tasks", status_code=202)
    async def start_task(request: StartTaskRequest):
        record = TaskRecord(
            task_id=uuid4().hex, repository=request.repository, ref=request.ref
        )
        records[record.task_id] = record
        record.job = asyncio.create_task(execute_task(record, request))
        return {"task_id": record.task_id, "status": "queued"}

    @app.get("/tasks/{task_id}")
    async def get_task(task_id: UUID):
        return find_task(task_id).response()

    @app.delete("/tasks/{task_id}")
    async def cancel_task(task_id: UUID):
        record = find_task(task_id)
        if record.status == "queued":
            record.status = "cancelled"
            record.execution_status = "cancelled"
            record.cleanup_status = "not_required"
            record.job.cancel()
        elif record.status == "running":
            record.status = "cancelling"
            record.job.cancel()
        return record.response()

    return app


app = create_app()
