"""An in-memory index over the live Tool metadata supplied by ToolCollection."""

import asyncio
import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Iterator, Protocol, Sequence

import numpy as np

from app.tool.base import ToolMetadata
from app.tool.tool_collection import ToolCollection


MODEL_ID = "sentence-transformers/paraphrase-MiniLM-L3-v2"
MODEL_REVISION = "4ca70771034acceecb2e72475f72050fcdde4ddc"
DOCUMENT_VERSION = "tool-metadata-json-v1/cosine-v1"
_embedding_event_hook: ContextVar[
    Callable[[str, dict[str, Any]], None] | None
] = ContextVar("embedding_event_hook", default=None)


@contextmanager
def embedding_event_scope(
    hook: Callable[[str, dict[str, Any]], None] | None,
) -> Iterator[None]:
    token = _embedding_event_hook.set(hook)
    try:
        yield
    finally:
        _embedding_event_hook.reset(token)


class EmbeddingIndexError(RuntimeError):
    """The current tool pool could not be indexed or queried."""


class EmbeddingBackend(Protocol):
    model_id: str
    revision: str
    vector_version: str

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        ...


class LocalTransformerEmbeddingBackend:
    """Pinned, CPU-only sentence embeddings using existing locked dependencies."""

    model_id = MODEL_ID
    revision = MODEL_REVISION
    vector_version = "mean-pool-128-v1"

    def __init__(self):
        self._tokenizer = None
        self._model = None
        self._lock = asyncio.Lock()

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        async with self._lock:
            return await asyncio.to_thread(self._embed_sync, list(texts))

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        import torch
        from transformers import AutoModel, AutoTokenizer

        if self._model is None:
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_id, revision=self.revision, trust_remote_code=False
            )
            self._model = AutoModel.from_pretrained(
                self.model_id,
                revision=self.revision,
                trust_remote_code=False,
                use_safetensors=True,
            ).eval()

        encoded = self._tokenizer(
            texts, padding=True, truncation=True, max_length=128, return_tensors="pt"
        )
        with torch.inference_mode():
            token_vectors = self._model(**encoded).last_hidden_state
            attention = encoded["attention_mask"].unsqueeze(-1)
            pooled = (token_vectors * attention).sum(dim=1) / attention.sum(dim=1)
        return pooled.cpu().tolist()


@dataclass(frozen=True)
class IndexVersion:
    model_id: str
    model_revision: str
    vector_version: str
    content_fingerprint: str


@dataclass(frozen=True)
class ToolMatch:
    name: str
    identity: str
    score: float


def _metadata_document(metadata: ToolMetadata) -> str:
    payload = {
        "identity": metadata.identity,
        "name": metadata.name,
        "description": metadata.description,
        "schema": metadata.schema,
        "capabilities": metadata.capabilities,
        "examples": metadata.examples,
        "source": metadata.source,
        "server_id": metadata.server_id,
        "original_name": metadata.original_name,
        "risk": metadata.risk,
    }
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EmbeddingIndexError(
            f"Tool metadata for {metadata.identity} is not JSON serializable"
        ) from exc


def _normalized_vectors(vectors: Sequence[Sequence[float]], count: int) -> np.ndarray:
    try:
        array = np.asarray(vectors, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise EmbeddingIndexError(
            "invalid embedding vector: non-numeric value"
        ) from exc
    if array.ndim != 2 or array.shape[0] != count or array.shape[1] == 0:
        raise EmbeddingIndexError("invalid embedding vector: shape mismatch")
    if not np.isfinite(array).all():
        raise EmbeddingIndexError("invalid embedding vector: non-finite value")
    norms = np.linalg.norm(array, axis=1)
    if not np.isfinite(norms).all() or (norms == 0).any():
        raise EmbeddingIndexError("invalid embedding vector: zero or infinite norm")
    return array / norms[:, np.newaxis]


class InMemoryToolIndex:
    """Rebuilds from the current ToolCollection whenever its metadata changes."""

    def __init__(self, backend: EmbeddingBackend):
        self.backend = backend
        self.version: IndexVersion | None = None
        self._entries: list[tuple[str, str]] = []
        self._vectors: np.ndarray | None = None
        self._lock = asyncio.Lock()

    async def _embed(
        self, texts: Sequence[str], phase: str
    ) -> Sequence[Sequence[float]]:
        started = perf_counter()
        try:
            vectors = await self.backend.embed(texts)
        except BaseException as exc:
            hook = _embedding_event_hook.get()
            if hook is not None:
                hook(
                    "cancelled"
                    if isinstance(exc, asyncio.CancelledError)
                    else "unknown"
                    if isinstance(exc, TimeoutError)
                    else "failure",
                    {
                        "embedding_phase": phase,
                        "duration_ms": (perf_counter() - started) * 1000,
                    },
                )
            raise
        hook = _embedding_event_hook.get()
        if hook is not None:
            hook(
                "success",
                {
                    "embedding_phase": phase,
                    "duration_ms": (perf_counter() - started) * 1000,
                },
            )
        return vectors

    async def refresh(self, tools: ToolCollection) -> bool:
        """Return whether the current tool pool required a rebuild."""
        async with self._lock:
            try:
                metadata = sorted(tools.to_metadata(), key=lambda item: item.identity)
                identities = [item.identity for item in metadata]
                if len(set(identities)) != len(identities):
                    raise EmbeddingIndexError("duplicate tool metadata identity")
                documents = [_metadata_document(item) for item in metadata]
                fingerprint = hashlib.sha256(
                    json.dumps(documents, ensure_ascii=False).encode("utf-8")
                ).hexdigest()
                version = IndexVersion(
                    model_id=self.backend.model_id,
                    model_revision=self.backend.revision,
                    vector_version=f"{DOCUMENT_VERSION}/{self.backend.vector_version}",
                    content_fingerprint=fingerprint,
                )
            except Exception:
                self._clear()
                raise

            if version == self.version:
                return False

            self._clear()
            if not metadata:
                self._entries = []
                self._vectors = np.empty((0, 0), dtype=np.float64)
                self.version = version
                return True

            try:
                vectors = await self._embed(documents, "index")
            except Exception as exc:
                raise EmbeddingIndexError(f"Tool embedding failed: {exc}") from exc
            self._vectors = _normalized_vectors(vectors, len(metadata))
            self._entries = [(item.name, item.identity) for item in metadata]
            self.version = version
            return True

    async def search(self, query: str, tools: ToolCollection) -> list[ToolMatch]:
        """Score every current tool; candidate selection belongs to T025."""
        _, matches = await self.search_with_version(query, tools)
        return matches

    async def search_with_version(
        self, query: str, tools: ToolCollection
    ) -> tuple[IndexVersion, list[ToolMatch]]:
        """Return scores with the version of the exact snapshot used."""
        await self.refresh(tools)
        version = self.version
        if not self._entries:
            return version, []
        entries, vectors = self._entries, self._vectors
        if not query.strip():
            raise EmbeddingIndexError("embedding query must not be empty")
        try:
            query_vectors = await self._embed([query], "query")
        except Exception as exc:
            raise EmbeddingIndexError(f"Tool query embedding failed: {exc}") from exc
        query_vector = _normalized_vectors(query_vectors, 1)[0]
        if query_vector.shape[0] != vectors.shape[1]:
            raise EmbeddingIndexError("invalid embedding vector: dimension mismatch")
        scores = vectors @ query_vector
        matches = [
            ToolMatch(name=name, identity=identity, score=float(score))
            for (name, identity), score in zip(entries, scores)
        ]
        return version, sorted(
            matches, key=lambda match: (-match.score, match.identity)
        )

    def _clear(self) -> None:
        self.version = None
        self._entries = []
        self._vectors = None
