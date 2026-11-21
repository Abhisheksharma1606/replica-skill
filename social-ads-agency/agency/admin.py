"""API for the web control room (/admin).

Everything the agents know and do is visible here: every client and their
stage, what each specialist produced, pending launch approvals (approve or
reject from the browser as well as Slack), the kill-switch and the event log.
Every route needs the ADMIN_TOKEN as a bearer token (open only in DRY_RUN
when no token is set).
"""

from __future__ import annotations

import hmac
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from agency.integrations.leadsie import AccessGranted
from agency.models import Stage, Turn, now_utc
from agency.orchestrator import events
from agency.orchestrator.state import TOOL_STAGES, ALWAYS, derive_stage
from agency.orchestrator.tools import TOOLS
from agency.workers import killswitch
from agency.workers.pricing import format_money

PIPELINE = [Stage.DISCOVERY, Stage.AUDIT, Stage.PROPOSAL, Stage.AWAITING_PAYMENT, Stage.ONBOARDING,
            Stage.CREATIVE, Stage.AWAITING_APPROVAL, Stage.LIVE, Stage.PAUSED, Stage.HANDOFF]


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    reviewer: str = Field(default="admin", min_length=1, max_length=60)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class SimulateIn(BaseModel):
    event: Literal["payment", "access_meta", "access_tiktok"]


def build_admin_router(sv) -> APIRouter:
    s = sv.settings

    def auth(authorization: Optional[str] = Header(default=None)) -> None:
        if not s.admin_token:
            if s.dry_run:
                return
            raise HTTPException(503, "set ADMIN_TOKEN to use the control room")
        token = (authorization or "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(token, s.admin_token):
            raise HTTPException(401, "bad admin token")

    r = APIRouter(prefix="/api/admin", dependencies=[Depends(auth)])

    def _states():
        out = []
        for key in sv.sessions.all_keys():
            st = sv.sessions.load(key)
            if st:
                out.append(st)
        return sorted(out, key=lambda x: x.updated_at, reverse=True)

    def _card(st) -> dict:
        p = st.profile
        return {
            "client_key": st.client_key,
            "name": p.business_name or p.contact_name or st.client_key,
            "industry": p.industry,
            "channel": st.channel.value,
            "stage": st.stage.value,
            "budget": format_money(p.monthly_ad_budget_minor, p.currency or s.default_currency)
            if p.monthly_ad_budget_minor else None,
            "platforms": p.platforms,
            "quote_total": format_money(st.quote.total_minor, st.quote.currency) if st.quote else None,
            "last_message": st.history[-1].text[:140] if st.history else "",
            "updated_at": st.updated_at,
        }

    @r.get("/overview")
    def overview() -> dict:
        states = _states()
        cards = [_card(st) for st in states]
        campaigns = sv.repo.list_campaigns()
        pending = sv.repo.list_approvals(status="pending")
        live_budget: dict[str, int] = {}
        for c in campaigns:
            if c.status == "active":
                live_budget[c.draft.currency] = live_budget.get(c.draft.currency, 0) + c.draft.daily_budget_minor
        paid: dict[str, int] = {}
        for st in states:
            if st.payment_status == "paid" and st.quote:
                paid[st.quote.currency] = paid.get(st.quote.currency, 0) + st.quote.total_minor
        return {
            "agency": s.agency_name,
            "dry_run": s.dry_run,
            "stages": [x.value for x in PIPELINE],
            "clients": cards,
            "stats": {
                "clients": len(states),
                "live_campaigns": sum(1 for c in campaigns if c.status == "active"),
                "paused_campaigns": sum(1 for c in campaigns if c.status == "paused"),
                "pending_approvals": len(pending),
                "revenue_collected": [format_money(v, k) for k, v in paid.items()] or ["0"],
                "live_daily_budget": [format_money(v, k) for k, v in live_budget.items()] or ["0"],
            },
        }

    @r.get("/clients/{client_key}")
    def client(client_key: str) -> dict:
        st = sv.sessions.load(client_key)
        if not st:
            raise HTTPException(404, "no such client")
        campaigns = sv.repo.list_campaigns(client_key=client_key)
        approvals = [a for a in sv.repo.list_approvals() if a.client_key == client_key]
        memories = sv.brand.recall(client_key, f"{st.profile.offer or ''} {st.profile.industry or ''}", k=12)
        return {
            "state": st.model_dump(mode="json"),
            "card": _card(st),
            "quote_lines": [
                {"item": i.description, "amount": format_money(i.amount_minor, st.quote.currency)}
                for i in st.quote.line_items
            ] if st.quote else [],
            "quote_summary": {
                "subtotal": format_money(st.quote.subtotal_minor, st.quote.currency),
                "tax": f"{st.quote.tax_label}: {format_money(st.quote.tax_minor, st.quote.currency)}",
                "total": format_money(st.quote.total_minor, st.quote.currency),
            } if st.quote else None,
            "campaigns": [c.model_dump(mode="json") for c in campaigns],
            "approvals": [a.model_dump(mode="json") for a in approvals],
            "brand_brain": [{"kind": m.kind, "text": m.text} for m in memories],
            "events": sv.repo.recent_events(client_key=client_key, limit=40),
        }

    @r.get("/approvals")
    def approvals() -> dict:
        out = []
        for a in sv.repo.list_approvals(status="pending"):
            c = sv.repo.get_campaign(a.campaign_id)
            st = sv.sessions.load(a.client_key)
            if not c or not st:
                continue
            creatives = [cr.model_dump(mode="json") for cr in st.creatives if cr.id in c.draft.creative_ids]
            out.append({
                "approval": a.model_dump(mode="json"),
                "client": _card(st),
                "campaign": c.model_dump(mode="json"),
                "daily_budget": format_money(c.draft.daily_budget_minor, c.draft.currency),
                "creatives": creatives,
            })
        return {"approvals": out}

    @r.post("/approvals/{approval_id}")
    async def decide(approval_id: str, body: DecisionIn) -> dict:
        actor = f"web:{body.reviewer}"
        msg = await run_in_threadpool(events.decide, sv, approval_id, body.decision, actor)
        sv.slack.notify(f"(control room) {msg}")
        return {"result": msg}

    @r.post("/killswitch")
    async def run_killswitch() -> dict:
        return {"report": await run_in_threadpool(killswitch.sweep, sv)}

    @r.post("/clients/{client_key}/message")
    def send_message(client_key: str, body: MessageIn) -> dict:
        """A human strategist writes to the client directly (used after a handoff)."""
        with sv.sessions.lock(client_key):
            st = sv.sessions.load(client_key)
            if not st:
                raise HTTPException(404, "no such client")
            st.history.append(Turn(role="assistant", text=body.text))
            st.updated_at = now_utc()
            sv.sessions.save(st)
        sv.messenger.send(client_key, body.text)
        sv.repo.log_event(client_key, "human.message", {"text": body.text[:200]})
        return {"sent": True}

    @r.post("/clients/{client_key}/release")
    def release(client_key: str) -> dict:
        """Hand a client back to the agent after a human handled them."""
        with sv.sessions.lock(client_key):
            st = sv.sessions.load(client_key)
            if not st:
                raise HTTPException(404, "no such client")
            st.handoff_reason = None
            st.stage = derive_stage(st, sv.repo.list_campaigns(client_key=client_key))
            sv.sessions.save(st)
        sv.repo.log_event(client_key, "handoff.released", {})
        return {"stage": st.stage.value}

    @r.post("/clients/{client_key}/simulate")
    async def simulate(client_key: str, body: SimulateIn) -> dict:
        if not s.dry_run:
            raise HTTPException(403, "simulators only run in DRY_RUN")
        if body.event == "payment":
            ev = events.resolve_dry_run_payment(sv, client_key)
            if not ev:
                raise HTTPException(409, "this client has no quote yet")
            await run_in_threadpool(events.on_payment, sv, ev)
        else:
            platform = body.event.split("_", 1)[1]
            await run_in_threadpool(events.on_access_granted, sv,
                                    [AccessGranted(client_key, platform, f"dry_{platform}_account")])
        st = sv.sessions.load(client_key)
        return {"stage": st.stage.value if st else None}

    @r.get("/events")
    def all_events(limit: int = 80) -> dict:
        return {"events": sv.repo.recent_events(limit=min(limit, 300))}

    @r.get("/agents")
    def agents() -> dict:
        tool_names = [t["name"] for t in TOOLS]
        return {
            "model": s.model,
            "agents": [
                {"id": "orchestrator", "name": "Account Manager", "kind": "llm", "layer": "Orchestration",
                 "effort": s.brain_effort,
                 "what": "The one agent clients talk to. Reads the pipeline state, chooses the next step and "
                         "calls every other agent and worker as a tool.",
                 "tools": tool_names},
                {"id": "discovery", "name": "Discovery Agent", "kind": "llm", "layer": "Agents", "effort": "low",
                 "what": "Runs on every message. Extracts business facts into the profile, scores fit, flags "
                         "restricted categories.", "tools": []},
                {"id": "competitor_audit", "name": "Competitor Audit Agent", "kind": "llm", "layer": "Agents",
                 "effort": s.specialist_effort,
                 "what": "Live web research on competitors and buyers, combined with the site and pixel scan.",
                 "tools": ["run_competitor_audit"]},
                {"id": "proposal_writer", "name": "Proposal Writer Agent", "kind": "llm", "layer": "Agents",
                 "effort": s.specialist_effort,
                 "what": "Writes the proposal and publishes it as a document; prices come from the quote.",
                 "tools": ["write_proposal"]},
                {"id": "creative", "name": "Creative Agent", "kind": "llm", "layer": "Agents",
                 "effort": s.specialist_effort,
                 "what": "Platform-native Meta and TikTok ads from the Brand Brain, with an ad policy check.",
                 "tools": ["generate_ad_creatives"]},
                {"id": "pricing", "name": "Pricing and Tax Engine", "kind": "worker", "layer": "Deterministic",
                 "what": "Rate card, packages, GST. The only source of prices.", "tools": ["build_quote"]},
                {"id": "scraper", "name": "Pixel and Site Scanner", "kind": "worker", "layer": "Deterministic",
                 "what": "Fetches the client site safely and detects Meta, TikTok, GA4, GTM and other tags.",
                 "tools": ["run_competitor_audit"]},
                {"id": "killswitch", "name": "Kill-Switch", "kind": "worker", "layer": "Deterministic",
                 "what": "Pauses live campaigns on overspend, no conversions or runaway cost per result.",
                 "tools": []},
            ],
            "tool_stages": {name: sorted(x.value for x in stages) for name, stages in TOOL_STAGES.items()},
            "always_available": sorted(ALWAYS),
            "integrations": [
                {"name": "Claude API", "ok": True, "note": s.model},
                {"name": "WhatsApp Business", "ok": bool(s.whatsapp_token)},
                {"name": "Voice AI webhook", "ok": bool(s.voice_webhook_secret)},
                {"name": "Razorpay", "ok": bool(s.razorpay_key_id)},
                {"name": "Stripe", "ok": bool(s.stripe_secret_key)},
                {"name": "Leadsie", "ok": bool(s.leadsie_connect_url)},
                {"name": "Meta Marketing API", "ok": bool(s.meta_access_token)},
                {"name": "TikTok Business API", "ok": bool(s.tiktok_access_token)},
                {"name": "Slack approvals", "ok": bool(s.slack_bot_token)},
                {"name": "Redis sessions", "ok": bool(s.redis_url), "note": "in-memory" if not s.redis_url else ""},
                {"name": "Postgres + pgvector", "ok": bool(s.database_url),
                 "note": "in-memory" if not s.database_url else ""},
            ],
        }

    return r
