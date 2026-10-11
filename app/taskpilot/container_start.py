"""Validate container inputs and warm the pinned embedding model before serving."""

import asyncio
import os
import sys
import tomllib
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ContainerStartupError(RuntimeError):
    """A required container input is unavailable."""


def validate_model_config(path: Path) -> None:
    if not path.is_file():
        raise ContainerStartupError("model config mount is missing")
    try:
        with path.open("rb") as handle:
            values = tomllib.load(handle).get("llm")
    except (OSError, tomllib.TOMLDecodeError):
        raise ContainerStartupError("model config is invalid") from None
    if not isinstance(values, dict) or any(
        not isinstance(values.get(field), str) or not values[field].strip()
        for field in ("model", "base_url", "api_key")
    ):
        raise ContainerStartupError("model config needs model, base_url and api_key")
    if values["api_key"].strip().lower() in {
        "your_api_key",
        "your api key",
        "placeholder",
    }:
        raise ContainerStartupError("model config api_key is a placeholder")


def prime_embedding_model(backend: Any = None) -> None:
    if backend is None:
        from app.taskpilot.tool_embedding_index import LocalTransformerEmbeddingBackend

        backend = LocalTransformerEmbeddingBackend()
    try:
        asyncio.run(backend.embed(["TaskPilot startup"]))
    except Exception:
        raise ContainerStartupError(
            "embedding model unavailable; check network or model cache"
        ) from None


def main() -> int:
    try:
        validate_model_config(PROJECT_ROOT / "config" / "config.toml")
        prime_embedding_model()
    except ContainerStartupError as error:
        print(f"taskpilot_startup_error: {error}", file=sys.stderr)
        return 1
    os.execvp(
        "uvicorn",
        [
            "uvicorn",
            "app.taskpilot.service:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--no-access-log",
        ],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
