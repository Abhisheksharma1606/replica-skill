"""Layer 5: durable records (clients, quotes, campaigns, approvals, audit events)."""

from __future__ import annotations

import json
import threading
from typing import Any, Optional, Protocol

from agency.models import Approval, Campaign, ConversationState, Quote, now_utc


class Repository(Protocol):
    def save_client(self, state: ConversationState) -> None: ...
    def save_quote(self, client_key: str, quote: Quote) -> None: ...
    def save_campaign(self, campaign: Campaign) -> None: ...
    def get_campaign(self, campaign_id: str) -> Optional[Campaign]: ...
    def list_campaigns(self, status: Optional[str] = None, client_key: Optional[str] = None) -> list[Campaign]: ...
    def save_approval(self, approval: Approval) -> None: ...
    def get_approval(self, approval_id: str) -> Optional[Approval]: ...
    def log_event(self, client_key: str, kind: str, data: dict[str, Any]) -> None: ...
    def list_approvals(self, status: Optional[str] = None) -> list[Approval]: ...
    def recent_events(self, client_key: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]: ...


class InMemoryRepository:
    def __init__(self) -> None:
        self.clients: dict[str, ConversationState] = {}
        self.quotes: dict[str, Quote] = {}
        self.campaigns: dict[str, Campaign] = {}
        self.approvals: dict[str, Approval] = {}
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def save_client(self, state: ConversationState) -> None:
        self.clients[state.client_key] = state.model_copy(deep=True)

    def save_quote(self, client_key: str, quote: Quote) -> None:
        self.quotes[quote.id] = quote

    def save_campaign(self, campaign: Campaign) -> None:
        self.campaigns[campaign.id] = campaign.model_copy(deep=True)

    def get_campaign(self, campaign_id: str) -> Optional[Campaign]:
        c = self.campaigns.get(campaign_id)
        return c.model_copy(deep=True) if c else None

    def list_campaigns(self, status: Optional[str] = None, client_key: Optional[str] = None) -> list[Campaign]:
        return [
            c.model_copy(deep=True) for c in self.campaigns.values()
            if (status is None or c.status == status) and (client_key is None or c.client_key == client_key)
        ]

    def save_approval(self, approval: Approval) -> None:
        self.approvals[approval.id] = approval.model_copy(deep=True)

    def get_approval(self, approval_id: str) -> Optional[Approval]:
        a = self.approvals.get(approval_id)
        return a.model_copy(deep=True) if a else None

    def log_event(self, client_key: str, kind: str, data: dict[str, Any]) -> None:
        with self._lock:
            self.events.append({"client_key": client_key, "kind": kind, "data": data, "at": now_utc()})

    def list_approvals(self, status: Optional[str] = None) -> list[Approval]:
        return sorted((a.model_copy(deep=True) for a in self.approvals.values()
                       if status is None or a.status == status), key=lambda a: a.requested_at, reverse=True)

    def recent_events(self, client_key: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
        rows = [e for e in self.events if client_key is None or e["client_key"] == client_key]
        return list(reversed(rows[-limit:]))


class PostgresRepository:
    def __init__(self, dsn: str) -> None:
        from psycopg_pool import ConnectionPool  # lazy: only needed with DATABASE_URL

        self.pool = ConnectionPool(dsn, min_size=1, max_size=10, open=True)

    def _exec(self, sql: str, params: tuple) -> list[tuple]:
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def save_client(self, state: ConversationState) -> None:
        self._exec(
            "INSERT INTO clients (client_key, business_name, stage, state, updated_at) VALUES (%s,%s,%s,%s,now()) "
            "ON CONFLICT (client_key) DO UPDATE SET business_name=EXCLUDED.business_name, stage=EXCLUDED.stage, "
            "state=EXCLUDED.state, updated_at=now()",
            (state.client_key, state.profile.business_name, state.stage.value, state.model_dump_json()),
        )

    def save_quote(self, client_key: str, quote: Quote) -> None:
        self._exec(
            "INSERT INTO quotes (id, client_key, package, currency, total_minor, data) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (id) DO NOTHING",
            (quote.id, client_key, quote.package, quote.currency, quote.total_minor, quote.model_dump_json()),
        )

    def save_campaign(self, campaign: Campaign) -> None:
        self._exec(
            "INSERT INTO campaigns (id, client_key, platform, status, data, updated_at) VALUES (%s,%s,%s,%s,%s,now()) "
            "ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status, data=EXCLUDED.data, updated_at=now()",
            (campaign.id, campaign.client_key, campaign.draft.platform, campaign.status, campaign.model_dump_json()),
        )

    def get_campaign(self, campaign_id: str) -> Optional[Campaign]:
        rows = self._exec("SELECT data FROM campaigns WHERE id=%s", (campaign_id,))
        return Campaign.model_validate(rows[0][0]) if rows else None

    def list_campaigns(self, status: Optional[str] = None, client_key: Optional[str] = None) -> list[Campaign]:
        rows = self._exec(
            "SELECT data FROM campaigns WHERE (%s::text IS NULL OR status=%s) AND (%s::text IS NULL OR client_key=%s)",
            (status, status, client_key, client_key),
        )
        return [Campaign.model_validate(r[0]) for r in rows]

    def save_approval(self, approval: Approval) -> None:
        self._exec(
            "INSERT INTO approvals (id, campaign_id, status, decided_by, data, updated_at) VALUES (%s,%s,%s,%s,%s,now()) "
            "ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status, decided_by=EXCLUDED.decided_by, "
            "data=EXCLUDED.data, updated_at=now()",
            (approval.id, approval.campaign_id, approval.status, approval.decided_by, approval.model_dump_json()),
        )

    def get_approval(self, approval_id: str) -> Optional[Approval]:
        rows = self._exec("SELECT data FROM approvals WHERE id=%s", (approval_id,))
        return Approval.model_validate(rows[0][0]) if rows else None

    def log_event(self, client_key: str, kind: str, data: dict[str, Any]) -> None:
        self._exec("INSERT INTO events (client_key, kind, data) VALUES (%s,%s,%s)",
                   (client_key, kind, json.dumps(data, default=str)))

    def list_approvals(self, status: Optional[str] = None) -> list[Approval]:
        rows = self._exec("SELECT data FROM approvals WHERE (%s::text IS NULL OR status=%s) "
                          "ORDER BY updated_at DESC LIMIT 200", (status, status))
        return [Approval.model_validate(r[0]) for r in rows]

    def recent_events(self, client_key: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._exec("SELECT client_key, kind, data, at FROM events WHERE (%s::text IS NULL OR client_key=%s) "
                          "ORDER BY at DESC LIMIT %s", (client_key, client_key, limit))
        return [{"client_key": r[0], "kind": r[1], "data": r[2], "at": r[3]} for r in rows]


def build_repository(database_url: str) -> Repository:
    if database_url:
        return PostgresRepository(database_url)  # type: ignore[return-value]
    return InMemoryRepository()  # type: ignore[return-value]
