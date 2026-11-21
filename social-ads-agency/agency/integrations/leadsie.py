"""Layer 6: ad account access through Leadsie.

Leadsie gives your agency one connect link; the client clicks it, logs into
Meta / TikTok and grants your Business Manager access to their ad account,
page and pixel. They keep ownership. When access is granted Leadsie calls
your webhook, and only that moves the client out of ONBOARDING.

Set LEADSIE_CONNECT_URL to your agency's connect link. The client_key is
appended as a query parameter so the webhook can be matched to the client.
Check your Leadsie dashboard for the exact parameter and webhook field names
on your plan, and adjust CLIENT_PARAM / parse_webhook if they differ.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from agency.config import Settings

CLIENT_PARAM = "customUserId"


@dataclass
class AccessGranted:
    client_key: str
    platform: str  # "meta" | "tiktok"
    ad_account_id: str


class Leadsie:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def connect_link(self, client_key: str) -> str:
        base = self.s.leadsie_connect_url or f"{self.s.public_base_url}/dry-run/connect"
        parts = urlparse(base)
        query = dict(parse_qsl(parts.query))
        query[CLIENT_PARAM] = client_key
        return urlunparse(parts._replace(query=urlencode(query)))

    def parse_webhook(self, body: bytes, signature: Optional[str], payload: dict) -> list[AccessGranted]:
        if self.s.leadsie_webhook_secret:
            expected = hmac.new(self.s.leadsie_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
            if not signature or not hmac.compare_digest(expected, signature):
                raise PermissionError("bad Leadsie signature")
        elif not self.s.dry_run:
            raise PermissionError("LEADSIE_WEBHOOK_SECRET is required outside DRY_RUN")

        client_key = payload.get(CLIENT_PARAM) or payload.get("client_key")
        if not client_key:
            return []
        out = []
        for asset in payload.get("assets", []) or []:
            kind = (asset.get("type") or "").lower()
            platform = (asset.get("platform") or "").lower()
            if kind in ("ad_account", "adaccount") and platform in ("meta", "facebook", "tiktok") \
                    and asset.get("status", "granted") == "granted":
                out.append(AccessGranted(client_key, "tiktok" if platform == "tiktok" else "meta",
                                         str(asset.get("id"))))
        return out
