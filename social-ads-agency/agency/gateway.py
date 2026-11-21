"""Layer 2 entry point: the API gateway (FastAPI).

Run:  uvicorn agency.gateway:create_app --factory --host 0.0.0.0 --port 8000

Routes
  GET  /health
  GET  /webhooks/whatsapp            Meta webhook verification
  POST /webhooks/whatsapp            inbound WhatsApp (acked fast, processed in background)
  POST /webhooks/webchat             inbound web chat (reply returned inline)
  GET  /webchat/{session_id}/outbox  messages pushed to a web chat client between turns
  POST /webhooks/voice               voice AI turn (reply returned inline, spoken by the voice agent)
  POST /webhooks/razorpay | /webhooks/stripe | /webhooks/leadsie
  POST /slack/actions                Slack interactivity (approve / reject launch)
  POST /jobs/killswitch              for cron, n8n or Temporal (X-Job-Token)
  GET  /files/{file_id}              proposal documents
  GET  /dry-run/pay/{quote_id}, GET /dry-run/connect   simulators, only when DRY_RUN=true
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
from collections import OrderedDict
from typing import Optional
from urllib.parse import parse_qs

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool

from agency.app import Services, build_services
from agency.ingestion import normalize
from agency.integrations.leadsie import AccessGranted
from agency.integrations.messaging import verify_slack
from agency.orchestrator import events
from agency.orchestrator.brain import Orchestrator
from agency.workers import killswitch

log = logging.getLogger(__name__)

FILE_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


class _Seen:
    """Drop webhook retries for messages already handled (bounded, per process)."""

    def __init__(self, size: int = 5000) -> None:
        self.size, self.items = size, OrderedDict()

    def add(self, key: Optional[str]) -> bool:
        if not key:
            return True
        if key in self.items:
            return False
        self.items[key] = True
        if len(self.items) > self.size:
            self.items.popitem(last=False)
        return True


def _secret_ok(expected: str, given: Optional[str]) -> bool:
    return bool(expected) and bool(given) and hmac.compare_digest(expected, given or "")


def create_app(services: Optional[Services] = None) -> FastAPI:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    sv = services or build_services()
    brain = Orchestrator(sv)
    seen = _Seen()
    s = sv.settings
    app = FastAPI(title=f"{s.agency_name} agent", version="1.0.0")
    app.state.services = sv

    def process_whatsapp(msg) -> None:
        try:
            reply = brain.handle(msg)
        except Exception:
            log.exception("failed to handle message from %s", msg.client_key)
            reply = "Sorry, something went wrong on our side. A teammate will follow up shortly."
        sv.messenger.send(msg.client_key, reply)

    # ------------------------------------------------------------- health

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "dry_run": s.dry_run, "model": s.model}

    # ------------------------------------------------------------- WhatsApp

    @app.get("/webhooks/whatsapp")
    def whatsapp_verify(mode: str = Query(alias="hub.mode", default=""),
                        token: str = Query(alias="hub.verify_token", default=""),
                        challenge: str = Query(alias="hub.challenge", default="")):
        if mode == "subscribe" and _secret_ok(s.whatsapp_verify_token, token):
            return PlainTextResponse(challenge)
        raise HTTPException(403, "verification failed")

    @app.post("/webhooks/whatsapp")
    async def whatsapp_in(request: Request, background: BackgroundTasks,
                          x_hub_signature_256: Optional[str] = Header(default=None)) -> dict:
        body = await request.body()
        if not (s.dry_run and not s.whatsapp_app_secret):
            if not normalize.verify_meta_signature(s.whatsapp_app_secret, body, x_hub_signature_256):
                raise HTTPException(401, "bad signature")
        for msg in normalize.from_whatsapp(json.loads(body or b"{}")):
            if seen.add(msg.external_message_id):
                background.add_task(process_whatsapp, msg)
        return {"ok": True}  # Meta needs a fast 200; the reply goes out via the send API

    # ------------------------------------------------------------- Web chat

    @app.post("/webhooks/webchat")
    async def webchat_in(body: normalize.WebChatIn) -> dict:
        msg = normalize.from_webchat(body)
        reply = await run_in_threadpool(brain.handle, msg)
        pushed = sv.messenger.outbox.pop(msg.client_key, [])
        return {"reply": reply, "pushed": pushed}

    @app.get("/webchat/{session_id}/outbox")
    def webchat_outbox(session_id: str) -> dict:
        return {"messages": sv.messenger.outbox.pop(f"web:{session_id}", [])}

    # ------------------------------------------------------------- Voice

    @app.post("/webhooks/voice")
    async def voice_in(body: normalize.VoiceTurnIn, x_voice_secret: Optional[str] = Header(default=None)) -> dict:
        if not (s.dry_run and not s.voice_webhook_secret) and not _secret_ok(s.voice_webhook_secret, x_voice_secret):
            raise HTTPException(401, "bad secret")
        reply = await run_in_threadpool(brain.handle, normalize.from_voice(body))
        return {"reply": reply}

    # ------------------------------------------------------------- Payments and access

    @app.post("/webhooks/razorpay")
    async def razorpay_in(request: Request, x_razorpay_signature: Optional[str] = Header(default=None)) -> dict:
        body = await request.body()
        try:
            ev = sv.payments.parse_razorpay_webhook(body, x_razorpay_signature, json.loads(body))
        except PermissionError:
            raise HTTPException(401, "bad signature")
        if ev:
            await run_in_threadpool(events.on_payment, sv, ev)
        return {"ok": True}

    @app.post("/webhooks/stripe")
    async def stripe_in(request: Request, stripe_signature: Optional[str] = Header(default=None)) -> dict:
        body = await request.body()
        try:
            ev = sv.payments.parse_stripe_webhook(body, stripe_signature, json.loads(body))
        except PermissionError:
            raise HTTPException(401, "bad signature")
        if ev:
            await run_in_threadpool(events.on_payment, sv, ev)
        return {"ok": True}

    @app.post("/webhooks/leadsie")
    async def leadsie_in(request: Request, x_leadsie_signature: Optional[str] = Header(default=None)) -> dict:
        body = await request.body()
        try:
            grants = sv.leadsie.parse_webhook(body, x_leadsie_signature, json.loads(body))
        except PermissionError:
            raise HTTPException(401, "bad signature")
        await run_in_threadpool(events.on_access_granted, sv, grants)
        return {"ok": True, "granted": len(grants)}

    # ------------------------------------------------------------- Slack HITL gate

    @app.post("/slack/actions")
    async def slack_actions(request: Request, background: BackgroundTasks,
                            x_slack_request_timestamp: Optional[str] = Header(default=None),
                            x_slack_signature: Optional[str] = Header(default=None)) -> dict:
        body = await request.body()
        if not verify_slack(s.slack_signing_secret, body, x_slack_request_timestamp, x_slack_signature):
            raise HTTPException(401, "bad signature")
        payload = json.loads(parse_qs(body.decode()).get("payload", ["{}"])[0])
        user = (payload.get("user") or {}).get("id", "unknown")
        for action in payload.get("actions", []) or []:
            decision = {"approve_launch": "approve", "reject_launch": "reject"}.get(action.get("action_id"))
            if decision:
                def run(aid=action.get("value", ""), d=decision) -> None:
                    sv.slack.notify(events.on_slack_decision(sv, aid, d, user))
                background.add_task(run)  # Slack wants a 200 within 3 seconds
        return {"ok": True}

    # ------------------------------------------------------------- Jobs

    @app.post("/jobs/killswitch")
    async def job_killswitch(x_job_token: Optional[str] = Header(default=None)) -> dict:
        if not _secret_ok(s.jobs_token, x_job_token):
            raise HTTPException(401, "bad job token")
        return {"report": await run_in_threadpool(killswitch.sweep, sv)}

    # ------------------------------------------------------------- Files

    @app.get("/files/{file_id}")
    def get_file(file_id: str):
        if not FILE_ID.match(file_id):
            raise HTTPException(404)
        pdf = os.path.join(s.files_dir, f"{file_id}.pdf")
        html_path = os.path.join(s.files_dir, f"{file_id}.html")
        if os.path.exists(pdf):
            return FileResponse(pdf, media_type="application/pdf")
        if os.path.exists(html_path):
            return FileResponse(html_path, media_type="text/html")
        raise HTTPException(404)

    # ------------------------------------------------------------- Dry-run simulators

    if s.dry_run:
        @app.get("/dry-run/pay/{quote_id}", response_class=HTMLResponse)
        def dry_pay(quote_id: str) -> str:
            for key in sv.sessions.all_keys():
                ev = events.resolve_dry_run_payment(sv, key)
                if ev and ev.quote_id == quote_id:
                    events.on_payment(sv, ev)
                    return "<p>Dry run: payment simulated. Return to the chat.</p>"
            raise HTTPException(404, "quote not found")

        @app.get("/dry-run/connect", response_class=HTMLResponse)
        def dry_connect(customUserId: str, platform: str = "meta") -> str:
            events.on_access_granted(sv, [AccessGranted(customUserId, platform, f"dry_{platform}_account")])
            return f"<p>Dry run: {platform} ad account connected. Return to the chat.</p>"

    return app
