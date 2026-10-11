# T059 Docker startup (Linux amd64)

This runs the existing read-only TaskPilot FastAPI service. It uses Python
3.12.14 and the pinned CPU-only dependencies in
`requirements-service.lock.txt`; the host `.venv` is not used. The pinned
embedding model is downloaded on the first container start into a Docker
volume, outside the image. The service listens on localhost port 8000 by
default.

## Configure and start

From a clean checkout, create a local model configuration. This file is Git
ignored and excluded from the Docker build context. Set `llm.model`,
`llm.base_url`, and `llm.api_key` to values for an OpenAI-compatible provider;
keep the other example settings unless your provider requires changes.

```bash
cp config/config.example-baseline.toml config/config.toml
chmod 600 config/config.toml
# Edit config/config.toml locally; never commit or paste its key.
export TASKPILOT_MODEL_CONFIG="$PWD/config/config.toml"
docker compose -f compose.taskpilot.yaml build
docker compose -f compose.taskpilot.yaml up -d --no-build
curl --fail http://127.0.0.1:8000/health
```

The clean-checkout build and real HTTP deployment check are recorded in
[T062](T062_DEPLOYMENT.md). The default builder's optional `build --no-cache`
attempt failed during a Docker BuildKit snapshot export. A later isolated
builder [reproduced the pinned image from zero](evidence/t062_no_cache_builder_review.json);
the normal build and all runtime checks passed.

The first start downloads the pinned
`sentence-transformers/paraphrase-MiniLM-L3-v2` revision before serving HTTP.
The container is not healthy until both model initialization and the HTTP
server complete. A missing/invalid config or failed model download exits with
an error, without printing the model key. Allow several minutes for a cold
download on a slow connection. Subsequent starts reuse the `model-cache`
volume; `HF_HUB_OFFLINE=1` can verify a warm cache without network access to
the model registry. `docker compose down` retains the volume;
`docker compose down -v` removes it and causes another cold start.

On a Linux host whose required HTTP proxy listens **only** on host loopback,
the default Docker bridge cannot reach that proxy. Prewarm a new Docker
volume with the same image, then start the normal localhost-bound service:

```bash
export TASKPILOT_MODEL_CONFIG="$PWD/config/config.toml"
docker compose -f compose.taskpilot.yaml create --no-build
docker run --rm --network host -e http_proxy -e https_proxy \
  --mount type=volume,source=taskpilot_model-cache,target=/var/cache/taskpilot/huggingface \
  --entrypoint python taskpilot-service:t059 \
  -c 'import asyncio; from app.taskpilot.tool_embedding_index import LocalTransformerEmbeddingBackend; asyncio.run(LocalTransformerEmbeddingBackend().embed(["startup"]))'
HF_HUB_OFFLINE=1 docker compose -f compose.taskpilot.yaml up -d --no-build
```

This download runs inside a disposable Docker container and does not copy the
host's model cache. The proxy variables are passed by name, so their values
are absent from the command line. Hosts with direct model-registry access can
use the normal `up` command above without this step.

## Submit a read-only task

```bash
curl --fail -X POST http://127.0.0.1:8000/tasks \
  -H 'Content-Type: application/json' \
  -d '{"repository":"FoundationAgents/OpenManus","task":"Read README.md and summarize its purpose with source evidence."}'
# Use the returned task_id:
curl --fail http://127.0.0.1:8000/tasks/TASK_ID
```

The task response separates Agent execution status from
`business_outcome=not_verified`. If it returns `clarification_pending`, answer
only the displayed missing fields through the existing
`POST /tasks/{task_id}/clarifications/{tool_call_id}/reply` API. A model
`terminate(success)` declaration is not independent proof of the answer.
GitHub Code Search needs an optional GitHub token; the README and repository
metadata tools do not. This Compose file does not pass any token by default.

## Stop and diagnose

```bash
docker compose -f compose.taskpilot.yaml ps
docker compose -f compose.taskpilot.yaml logs --tail=50 service
docker compose -f compose.taskpilot.yaml down
```

`TASKPILOT_MODEL_CONFIG` must name an existing absolute file. The bind mount
is read-only. The image contains the example config only, never the local
`config.toml`. The model cache is a named volume, and there is no database or
multi-tenant isolation. The container process runs as root so it can read a
host-owned `0600` configuration mount; no Docker socket is mounted. Keep the
bound API on localhost; public deployment safety is outside T059.
