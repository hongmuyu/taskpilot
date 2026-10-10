"""Small process-local HTTP entry point for read-only repository tasks."""

import asyncio
import math
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schema import AgentState
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


class ClarificationReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1, max_length=4000, strict=True)

    @field_validator("reply")
    @classmethod
    def nonempty_reply(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Reply must not be empty")
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
    agent: Any = field(default=None, repr=False)
    clarification_call_id: str | None = field(default=None, repr=False)
    reply_queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue, repr=False)
    reply_queued: bool = field(default=False, repr=False)

    def response(self) -> dict[str, Any]:
        pending = (
            self.agent.pending_tool_calls.get(self.clarification_call_id)
            if self.status == "clarification_pending"
            and self.agent is not None
            and self.clarification_call_id is not None
            else None
        )
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
            "clarification": (
                {
                    "tool_call_id": pending.tool_call_id,
                    "tool_name": pending.tool_name,
                    "missing_fields": pending.missing_fields,
                }
                if pending is not None
                else None
            ),
        }


def _default_agent(context: RepositoryContext):
    from app.agent.repository_investigation import RepositoryInvestigationAgent

    return RepositoryInvestigationAgent.create(context)


def create_app(
    agent_factory: Callable | None = None,
    routing_top_k: int | None = 3,
    clarification_timeout_seconds: float = 120.0,
) -> FastAPI:
    if (
        not math.isfinite(clarification_timeout_seconds)
        or clarification_timeout_seconds <= 0
    ):
        raise ValueError("Clarification timeout must be positive")
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
            record.agent = agent
            agent.routing_top_k = routing_top_k
            agent.interactive_clarification = False

            async def await_clarification(call_id: str) -> str | None:
                record.clarification_call_id = call_id
                record.status = "clarification_pending"
                try:
                    while True:
                        wait_started = perf_counter()
                        try:
                            reply = await asyncio.wait_for(
                                record.reply_queue.get(),
                                timeout=clarification_timeout_seconds,
                            )
                        except TimeoutError:
                            record.error = "clarification_timeout"
                            return None
                        agent._trace_event(
                            "clarification",
                            "waited",
                            call_id=call_id,
                            tool_name=agent.pending_tool_calls[call_id].tool_name,
                            duration_ms=(perf_counter() - wait_started) * 1000,
                        )
                        try:
                            observation = await agent.submit_clarification_reply(
                                call_id, reply, record_tool_message=False
                            )
                        finally:
                            record.reply_queued = False
                        if call_id not in agent.pending_tool_calls:
                            cancelled = reply.strip().lower() == "cancel"
                            if cancelled:
                                agent._trace_final_status = "cancelled"
                                agent._trace_finish_source = "runtime_exit"
                                agent.state = AgentState.FINISHED
                            record.status = "running"
                            record.error = (
                                "clarification_cancelled" if cancelled else None
                            )
                            return observation
                        record.error = "clarification_still_pending"
                finally:
                    record.clarification_call_id = None

            agent.async_clarification_handler = await_clarification
            record.result = await agent.run(request.task)
            events = agent.execution_trace.events if agent.execution_trace else []
            record.run_id = (
                agent.execution_trace.run_id if agent.execution_trace else None
            )
            finish = events[-1] if events and events[-1]["event"] == "finish" else None
            record.execution_status = finish["status"] if finish else "unknown"
            record.finish_source = finish["finish_source"] if finish else None
            if record.error == "clarification_timeout":
                final_status = "clarification_timeout"
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
                agent.async_clarification_handler = None
            record.agent = None
            record.reply_queue = asyncio.Queue()
            record.reply_queued = False
            record.clarification_call_id = None
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

    @app.post("/tasks/{task_id}/clarifications/{tool_call_id}/reply", status_code=202)
    async def reply_to_clarification(
        task_id: UUID, tool_call_id: str, request: ClarificationReplyRequest
    ):
        record = find_task(task_id)
        if (
            record.status != "clarification_pending"
            or record.clarification_call_id != tool_call_id
            or record.reply_queued
        ):
            raise HTTPException(status_code=409, detail="Clarification is not pending")
        record.error = None
        record.reply_queued = True
        record.reply_queue.put_nowait(request.reply)
        return {
            "task_id": record.task_id,
            "tool_call_id": tool_call_id,
            "status": "accepted",
        }

    @app.delete("/tasks/{task_id}")
    async def cancel_task(task_id: UUID):
        record = find_task(task_id)
        if record.status == "queued":
            record.status = "cancelled"
            record.execution_status = "cancelled"
            record.cleanup_status = "not_required"
            record.job.cancel()
        elif record.status in {"running", "clarification_pending"}:
            record.status = "cancelling"
            record.job.cancel()
        return record.response()

    return app


app = create_app()
