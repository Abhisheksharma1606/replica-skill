"""Layer 1: turn every channel's webhook into one InboundMessage.

Nothing downstream knows which channel a message came from except through
InboundMessage.channel, which is only used to pick the reply transport.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Iterable

from pydantic import BaseModel, Field

from agency.models import Channel, InboundMessage

MAX_TEXT = 4000  # longer inbound text is cut here, before it reaches any model


def _clip(text: str) -> str:
    text = (text or "").strip()
    return text[:MAX_TEXT]


# ---------------------------------------------------------------- WhatsApp

def verify_meta_signature(app_secret: str, body: bytes, header: str | None) -> bool:
    """Meta signs webhook bodies with X-Hub-Signature-256: sha256=<hex hmac>."""
    if not app_secret:
        return False
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


def from_whatsapp(payload: dict[str, Any]) -> list[InboundMessage]:
    """WhatsApp Cloud API webhook -> zero or more messages (status callbacks yield none)."""
    out: list[InboundMessage] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            names = {
                c.get("wa_id"): (c.get("profile") or {}).get("name")
                for c in value.get("contacts", []) or []
            }
            for m in value.get("messages", []) or []:
                text = _whatsapp_text(m)
                if text is None:
                    continue
                sender = str(m.get("from", ""))
                if not sender:
                    continue
                out.append(InboundMessage(
                    channel=Channel.WHATSAPP,
                    client_key=f"wa:{sender}",
                    text=_clip(text),
                    display_name=names.get(sender),
                    media_urls=_whatsapp_media(m),
                    external_message_id=m.get("id"),
                ))
    return out


def _whatsapp_text(m: dict[str, Any]) -> str | None:
    kind = m.get("type")
    if kind == "text":
        return (m.get("text") or {}).get("body", "")
    if kind == "button":
        return (m.get("button") or {}).get("text", "")
    if kind == "interactive":
        inter = m.get("interactive") or {}
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        return reply.get("title", "")
    if kind in ("image", "video", "document"):
        caption = (m.get(kind) or {}).get("caption") or ""
        return f"[sent a {kind}] {caption}".strip()
    if kind == "audio":
        return "[sent a voice note]"
    return None


def _whatsapp_media(m: dict[str, Any]) -> list[str]:
    kind = m.get("type")
    if kind in ("image", "video", "document", "audio"):
        media_id = (m.get(kind) or {}).get("id")
        return [f"whatsapp-media:{media_id}"] if media_id else []
    return []


# ---------------------------------------------------------------- Web chat

class WebChatIn(BaseModel):
    session_id: str = Field(min_length=6, max_length=128)
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    name: str | None = Field(default=None, max_length=120)


def from_webchat(body: WebChatIn) -> InboundMessage:
    return InboundMessage(
        channel=Channel.WEBCHAT,
        client_key=f"web:{body.session_id}",
        text=_clip(body.text),
        display_name=body.name,
    )


# ---------------------------------------------------------------- Voice AI

class VoiceTurnIn(BaseModel):
    """Generic shape for a voice AI platform (Vapi, Retell, Bland...) custom-LLM webhook.

    Map your provider's payload to this in a few lines; the agent only needs
    who is calling and what they just said.
    """

    call_id: str = Field(min_length=1, max_length=128)
    caller: str = Field(min_length=3, max_length=40)  # E.164 phone number
    transcript: str = Field(min_length=1, max_length=MAX_TEXT)


def from_voice(body: VoiceTurnIn) -> InboundMessage:
    digits = "".join(ch for ch in body.caller if ch.isdigit())
    # Same client_key as WhatsApp so a caller who later messages keeps their history.
    return InboundMessage(
        channel=Channel.VOICE,
        client_key=f"wa:{digits}",
        text=_clip(body.transcript),
        external_message_id=body.call_id,
    )


def first(messages: Iterable[InboundMessage]) -> InboundMessage | None:
    for m in messages:
        return m
    return None
