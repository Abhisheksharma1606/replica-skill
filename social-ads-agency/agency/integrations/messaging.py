"""Layer 6: outbound messages to clients (WhatsApp Cloud API) and to the team (Slack).

Web chat and voice replies are returned in the HTTP response, so only
WhatsApp needs an outbound sender. Messages the system sends on its own
(payment confirmed, campaign live, campaign paused) also go through here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Optional

import httpx2 as httpx

from agency.config import Settings
from agency.models import Approval, Campaign, AdCreative
from agency.workers.pricing import format_money

log = logging.getLogger(__name__)


class ClientMessenger:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.sent: list[tuple[str, str]] = []  # (client_key, text), kept for dry runs and tests
        # Web chat clients poll this for messages the system pushes between their turns.
        self.outbox: dict[str, list[str]] = {}

    def send(self, client_key: str, text: str) -> None:
        self.sent.append((client_key, text))
        if client_key.startswith("web:"):
            self.outbox.setdefault(client_key, []).append(text)
            return
        if not client_key.startswith("wa:"):
            return
        if self.s.dry_run or not self.s.whatsapp_token:
            log.info("[dry-run whatsapp -> %s] %s", client_key, text)
            return
        for chunk in _chunks(text, 4000):
            resp = httpx.post(
                f"https://graph.facebook.com/{self.s.meta_api_version}/{self.s.whatsapp_phone_number_id}/messages",
                headers={"Authorization": f"Bearer {self.s.whatsapp_token}"},
                json={"messaging_product": "whatsapp", "to": client_key[3:], "type": "text",
                      "text": {"body": chunk, "preview_url": True}},
                timeout=20.0,
            )
            if resp.status_code >= 400:
                log.error("WhatsApp send failed: %s %s", resp.status_code, resp.text[:300])


def _chunks(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


# ------------------------------------------------------------------ Slack

class Slack:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.posted: list[dict[str, Any]] = []

    def _post(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        self.posted.append({"method": method, **body})
        if self.s.dry_run or not self.s.slack_bot_token:
            log.info("[dry-run slack %s] %s", method, json.dumps(body)[:500])
            return {"ok": True, "ts": f"dry.{len(self.posted)}"}
        resp = httpx.post(f"https://slack.com/api/{method}", json=body, timeout=20.0,
                          headers={"Authorization": f"Bearer {self.s.slack_bot_token}"})
        data = resp.json()
        if not data.get("ok"):
            log.error("Slack %s failed: %s", method, data.get("error"))
        return data

    def notify(self, text: str) -> None:
        self._post("chat.postMessage", {"channel": self.s.slack_approval_channel, "text": text})

    def request_approval(self, approval: Approval, campaign: Campaign, creatives: list[AdCreative],
                         business_name: str) -> Optional[str]:
        d = campaign.draft
        g = campaign.guardrails
        ads = "\n".join(f"*{i + 1}. {c.headline}*\n{c.primary_text[:280]}\n_CTA {c.call_to_action}; "
                        f"visual: {c.visual_brief[:140]}_"
                        + (f"\n:warning: {'; '.join(c.policy_notes)}" if c.policy_notes else "")
                        for i, c in enumerate(creatives))
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": f"Launch approval: {business_name}"[:150]}},
            {"type": "section", "fields": [
                {"type": "mrkdwn", "text": f"*Platform*\n{d.platform}"},
                {"type": "mrkdwn", "text": f"*Objective*\n{d.objective}"},
                {"type": "mrkdwn", "text": f"*Daily budget*\n{format_money(d.daily_budget_minor, d.currency)}"},
                {"type": "mrkdwn", "text": f"*Countries / ages*\n{', '.join(d.countries)} / {d.age_min}-{d.age_max}"},
                {"type": "mrkdwn", "text": f"*Landing page*\n{d.landing_url}"},
                {"type": "mrkdwn", "text": "*Kill-switch*\n" + (
                    f"pause above {g.max_overspend_ratio:g}x budget"
                    + (f", or CPA over {g.max_cpa_multiple:g}x {format_money(g.target_cpa_minor, d.currency)}"
                       if g.target_cpa_minor else ""))},
            ]},
            {"type": "section", "text": {"type": "mrkdwn", "text": ads[:2900] or "_no creatives_"}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text":
                f"Campaign `{campaign.id}`, created PAUSED on the platform. Approval `{approval.id}` "
                f"expires {approval.expires_at:%d %b %H:%M} UTC. Approving turns it on."}]},
            {"type": "actions", "elements": [
                {"type": "button", "style": "primary", "action_id": "approve_launch", "value": approval.id,
                 "text": {"type": "plain_text", "text": "Approve and launch"},
                 "confirm": {"title": {"type": "plain_text", "text": "Go live?"},
                             "text": {"type": "plain_text", "text": "This starts spending the client's budget."},
                             "confirm": {"type": "plain_text", "text": "Launch"},
                             "deny": {"type": "plain_text", "text": "Cancel"}}},
                {"type": "button", "style": "danger", "action_id": "reject_launch", "value": approval.id,
                 "text": {"type": "plain_text", "text": "Reject"}},
            ]},
        ]
        data = self._post("chat.postMessage", {"channel": self.s.slack_approval_channel,
                                               "text": f"Launch approval needed for {business_name}",
                                               "blocks": blocks})
        return data.get("ts")


def verify_slack(signing_secret: str, body: bytes, timestamp: Optional[str], signature: Optional[str],
                 now: Optional[float] = None) -> bool:
    if not signing_secret or not timestamp or not signature or not timestamp.isdigit():
        return False
    if abs((now or time.time()) - int(timestamp)) > 300:
        return False
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
