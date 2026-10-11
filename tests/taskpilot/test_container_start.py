"""T059 container startup checks without Docker or provider calls."""

import httpx
import pytest

from app.taskpilot.container_start import (
    ContainerStartupError,
    prime_embedding_model,
    validate_model_config,
)
from app.taskpilot.service import create_app


def test_missing_model_config_stops_startup(tmp_path):
    with pytest.raises(ContainerStartupError, match="model config mount is missing"):
        validate_model_config(tmp_path / "config.toml")


@pytest.mark.parametrize(
    "contents",
    [
        "not valid TOML =",
        '[llm]\nmodel = "test"\nbase_url = "https://example.test/v1"\n',
        '[llm]\nmodel = "test"\nbase_url = "https://example.test/v1"\napi_key = "YOUR_API_KEY"\n',
    ],
)
def test_invalid_model_config_stops_without_echoing_values(tmp_path, contents):
    path = tmp_path / "config.toml"
    path.write_text(contents)
    with pytest.raises(ContainerStartupError) as error:
        validate_model_config(path)
    assert "YOUR_API_KEY" not in str(error.value)
    assert "https://example.test" not in str(error.value)


def test_valid_model_config_is_accepted(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[llm]\nmodel = "test"\nbase_url = "https://example.test/v1"\n'
        'api_key = "non-secret-test-value"\n'
    )
    validate_model_config(path)


def test_embedding_failure_is_reported_without_exception_detail():
    class FailingBackend:
        async def embed(self, texts):
            raise RuntimeError("private diagnostic value")

    with pytest.raises(
        ContainerStartupError, match="embedding model unavailable"
    ) as error:
        prime_embedding_model(FailingBackend())
    assert "private diagnostic value" not in str(error.value)


@pytest.mark.asyncio
async def test_service_health_endpoint():
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
