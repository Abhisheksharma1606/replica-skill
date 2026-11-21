"""Layer 6: payment links and payment webhooks (Stripe and Razorpay).

INR quotes go to Razorpay (UPI, cards, netbanking); everything else to Stripe.
The client_key and quote_id ride along as metadata, so the webhook knows who
paid without any lookup. Only a verified webhook marks a client as paid.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx2 as httpx

from agency.config import Settings
from agency.models import ClientProfile, Quote

log = logging.getLogger(__name__)


@dataclass
class PaymentLink:
    provider: str
    url: str
    reference: str


@dataclass
class PaymentEvent:
    client_key: str
    quote_id: str
    amount_minor: int
    currency: str
    provider: str
    reference: str


class Payments:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def create_link(self, client_key: str, quote: Quote, profile: ClientProfile) -> PaymentLink:
        provider = "razorpay" if quote.currency == "INR" else "stripe"
        if self.s.dry_run:
            ref = f"dry_{provider}_{quote.id}"
            return PaymentLink(provider, f"{self.s.public_base_url}/dry-run/pay/{quote.id}", ref)
        if provider == "razorpay":
            return self._razorpay(client_key, quote, profile)
        return self._stripe(client_key, quote, profile)

    def _razorpay(self, client_key: str, quote: Quote, profile: ClientProfile) -> PaymentLink:
        body: dict[str, Any] = {
            "amount": quote.total_minor,
            "currency": quote.currency,
            "accept_partial": False,
            "reference_id": quote.id,
            "description": f"{self.s.agency_name}: {quote.package.title()} package",
            "expire_by": int(quote.valid_until.timestamp()),
            "notes": {"client_key": client_key, "quote_id": quote.id},
            "reminder_enable": True,
        }
        customer = {k: v for k, v in {"name": profile.contact_name or profile.business_name,
                                      "email": profile.email}.items() if v}
        if client_key.startswith("wa:"):
            customer["contact"] = "+" + client_key[3:]
        if customer:
            body["customer"] = customer
        resp = httpx.post("https://api.razorpay.com/v1/payment_links", json=body,
                          auth=(self.s.razorpay_key_id, self.s.razorpay_key_secret), timeout=20.0)
        resp.raise_for_status()
        data = resp.json()
        return PaymentLink("razorpay", data["short_url"], data["id"])

    def _stripe(self, client_key: str, quote: Quote, profile: ClientProfile) -> PaymentLink:
        form = {
            "mode": "payment",
            "success_url": f"{self.s.public_base_url}/paid",
            "client_reference_id": quote.id,
            "metadata[client_key]": client_key,
            "metadata[quote_id]": quote.id,
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": quote.currency.lower(),
            "line_items[0][price_data][unit_amount]": str(quote.total_minor),
            "line_items[0][price_data][product_data][name]": f"{self.s.agency_name}: {quote.package.title()} package",
        }
        if profile.email:
            form["customer_email"] = profile.email
        resp = httpx.post("https://api.stripe.com/v1/checkout/sessions", data=form,
                          auth=(self.s.stripe_secret_key, ""), timeout=20.0)
        resp.raise_for_status()
        data = resp.json()
        return PaymentLink("stripe", data["url"], data["id"])

    # ------------------------------------------------------------ webhooks

    def parse_razorpay_webhook(self, body: bytes, signature: Optional[str], payload: dict) -> Optional[PaymentEvent]:
        if not verify_razorpay(self.s.razorpay_webhook_secret, body, signature):
            raise PermissionError("bad Razorpay signature")
        if payload.get("event") != "payment_link.paid":
            return None
        link = payload["payload"]["payment_link"]["entity"]
        notes = link.get("notes") or {}
        return PaymentEvent(notes["client_key"], notes["quote_id"], int(link["amount_paid"]),
                            link["currency"], "razorpay", link["id"])

    def parse_stripe_webhook(self, body: bytes, signature: Optional[str], payload: dict) -> Optional[PaymentEvent]:
        if not verify_stripe(self.s.stripe_webhook_secret, body, signature):
            raise PermissionError("bad Stripe signature")
        if payload.get("type") != "checkout.session.completed":
            return None
        obj = payload["data"]["object"]
        if obj.get("payment_status") != "paid":
            return None
        meta = obj.get("metadata") or {}
        return PaymentEvent(meta["client_key"], meta["quote_id"], int(obj["amount_total"]),
                            obj["currency"].upper(), "stripe", obj["id"])


def verify_razorpay(secret: str, body: bytes, signature: Optional[str]) -> bool:
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_stripe(secret: str, body: bytes, header: Optional[str], tolerance: int = 300,
                  now: Optional[float] = None) -> bool:
    if not secret or not header:
        return False
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    ts, sig = parts.get("t"), parts.get("v1")
    if not ts or not sig or not ts.isdigit():
        return False
    if abs((now or time.time()) - int(ts)) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)
