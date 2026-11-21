"""Layer 5: the Brand Brain. Per-client vector memory the creative agent writes from.

Stores short facts (tone, offers, objections, banned claims, past winning
hooks) and retrieves the most relevant ones for a task.

Embeddings: Voyage AI (Anthropic's recommended embeddings provider) when
VOYAGE_API_KEY is set; otherwise a deterministic hashing embedder, which is
good enough for local runs and tests but not for production recall.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from dataclasses import dataclass
from typing import Optional, Protocol

import httpx2 as httpx

DIM = 1024


class Embedder(Protocol):
    def embed(self, texts: list[str], kind: str) -> list[list[float]]: ...


class VoyageEmbedder:
    def __init__(self, api_key: str, model: str = "voyage-3.5") -> None:
        self.api_key, self.model = api_key, model

    def embed(self, texts: list[str], kind: str) -> list[list[float]]:
        resp = httpx.post(
            "https://api.voyageai.com/v1/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"input": texts, "model": self.model, "input_type": kind, "output_dimension": DIM},
            timeout=30.0,
        )
        resp.raise_for_status()
        return [d["embedding"] for d in sorted(resp.json()["data"], key=lambda d: d["index"])]


class HashingEmbedder:
    def embed(self, texts: list[str], kind: str) -> list[list[float]]:
        out = []
        for t in texts:
            vec = [0.0] * DIM
            for tok in re.findall(r"[a-z0-9]+", t.lower()):
                h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
                vec[h % DIM] += 1.0 if (h >> 32) & 1 else -1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


@dataclass
class Memory:
    kind: str
    text: str
    score: float = 0.0


class BrandBrain(Protocol):
    def remember(self, client_key: str, kind: str, text: str) -> None: ...
    def recall(self, client_key: str, query: str, k: int = 8) -> list[Memory]: ...


class InMemoryBrandBrain:
    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder
        self._rows: dict[str, list[tuple[str, str, list[float]]]] = {}
        self._lock = threading.Lock()

    def remember(self, client_key: str, kind: str, text: str) -> None:
        text = text.strip()[:1000]
        if not text:
            return
        [vec] = self.embedder.embed([text], "document")
        with self._lock:
            rows = self._rows.setdefault(client_key, [])
            if any(t == text for _, t, _ in rows):
                return
            rows.append((kind, text, vec))

    def recall(self, client_key: str, query: str, k: int = 8) -> list[Memory]:
        rows = self._rows.get(client_key, [])
        if not rows:
            return []
        [q] = self.embedder.embed([query], "query")
        scored = [Memory(kind, text, sum(a * b for a, b in zip(q, vec))) for kind, text, vec in rows]
        return sorted(scored, key=lambda m: m.score, reverse=True)[:k]


class PgVectorBrandBrain:
    def __init__(self, dsn: str, embedder: Embedder) -> None:
        from psycopg_pool import ConnectionPool

        self.pool = ConnectionPool(dsn, min_size=1, max_size=5, open=True)
        self.embedder = embedder

    @staticmethod
    def _lit(vec: list[float]) -> str:
        return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"

    def remember(self, client_key: str, kind: str, text: str) -> None:
        text = text.strip()[:1000]
        if not text:
            return
        [vec] = self.embedder.embed([text], "document")
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO brand_memories (client_key, kind, text, embedding) "
                "SELECT %s, %s, %s, %s::vector WHERE NOT EXISTS "
                "(SELECT 1 FROM brand_memories WHERE client_key=%s AND text=%s)",
                (client_key, kind, text, self._lit(vec), client_key, text),
            )

    def recall(self, client_key: str, query: str, k: int = 8) -> list[Memory]:
        [q] = self.embedder.embed([query], "query")
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT kind, text, 1 - (embedding <=> %s::vector) AS score FROM brand_memories "
                "WHERE client_key=%s ORDER BY embedding <=> %s::vector LIMIT %s",
                (self._lit(q), client_key, self._lit(q), k),
            ).fetchall()
        return [Memory(r[0], r[1], float(r[2])) for r in rows]


def build_brand_brain(database_url: str, voyage_api_key: str, embedder: Optional[Embedder] = None) -> BrandBrain:
    embedder = embedder or (VoyageEmbedder(voyage_api_key) if voyage_api_key else HashingEmbedder())
    if database_url:
        return PgVectorBrandBrain(database_url, embedder)  # type: ignore[return-value]
    return InMemoryBrandBrain(embedder)  # type: ignore[return-value]
