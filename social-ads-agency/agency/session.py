"""Layer 2: per-client session state with a lock.

Two messages from the same client (a double-tap on WhatsApp, a payment
webhook racing a chat reply) must never run the orchestrator at the same time
on the same state. Redis gives a cross-process lock; the in-memory store is
for local runs and tests.
"""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional, Protocol

from agency.models import Channel, ConversationState

STATE_TTL_SECONDS = 60 * 60 * 24 * 90  # keep 90 days of hot state; Postgres keeps the rest
LOCK_TTL_MS = 120_000
LOCK_WAIT_SECONDS = 30.0


class LockTimeout(RuntimeError):
    pass


class SessionStore(Protocol):
    def load(self, client_key: str) -> Optional[ConversationState]: ...
    def save(self, state: ConversationState) -> None: ...
    def lock(self, client_key: str) -> "contextmanager[None]": ...  # type: ignore[valid-type]
    def all_keys(self) -> list[str]: ...

    def load_or_create(self, client_key: str, channel: Channel) -> ConversationState: ...


class _Base:
    def load_or_create(self, client_key: str, channel: Channel) -> ConversationState:
        state = self.load(client_key)  # type: ignore[attr-defined]
        if state is None:
            state = ConversationState(client_key=client_key, channel=channel)
        return state


class InMemorySessionStore(_Base):
    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def load(self, client_key: str) -> Optional[ConversationState]:
        raw = self._data.get(client_key)
        return ConversationState.model_validate_json(raw) if raw else None

    def save(self, state: ConversationState) -> None:
        self._data[state.client_key] = state.model_dump_json()

    def all_keys(self) -> list[str]:
        return list(self._data)

    @contextmanager
    def lock(self, client_key: str) -> Iterator[None]:
        with self._guard:
            lk = self._locks.setdefault(client_key, threading.Lock())
        if not lk.acquire(timeout=LOCK_WAIT_SECONDS):
            raise LockTimeout(client_key)
        try:
            yield
        finally:
            lk.release()


class RedisSessionStore(_Base):
    _RELEASE = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('del', KEYS[1]) else return 0 end"
    )

    def __init__(self, url: str) -> None:
        import redis  # imported lazily so the in-memory path needs no Redis

        self._r = redis.Redis.from_url(url, decode_responses=True)
        self._release = self._r.register_script(self._RELEASE)

    def load(self, client_key: str) -> Optional[ConversationState]:
        raw = self._r.get(f"state:{client_key}")
        return ConversationState.model_validate_json(raw) if raw else None

    def save(self, state: ConversationState) -> None:
        self._r.set(f"state:{state.client_key}", state.model_dump_json(), ex=STATE_TTL_SECONDS)
        self._r.sadd("state:keys", state.client_key)

    def all_keys(self) -> list[str]:
        return sorted(self._r.smembers("state:keys"))

    @contextmanager
    def lock(self, client_key: str) -> Iterator[None]:
        key = f"lock:{client_key}"
        token = uuid.uuid4().hex
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while not self._r.set(key, token, nx=True, px=LOCK_TTL_MS):
            if time.monotonic() > deadline:
                raise LockTimeout(client_key)
            time.sleep(0.1)
        try:
            yield
        finally:
            self._release(keys=[key], args=[token])


def build_session_store(redis_url: str) -> SessionStore:
    if redis_url:
        return RedisSessionStore(redis_url)  # type: ignore[return-value]
    return InMemorySessionStore()  # type: ignore[return-value]
