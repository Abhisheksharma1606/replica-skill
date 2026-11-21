"""Shared data shapes. Everything that crosses a layer boundary is one of these.

Money is always an integer in minor units (paise, cents) plus a currency code.
Floats never touch a price.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


# --------------------------------------------------------------------------
# Layer 1: ingestion
# --------------------------------------------------------------------------

class Channel(str, Enum):
    WHATSAPP = "whatsapp"
    WEBCHAT = "webchat"
    VOICE = "voice"


class InboundMessage(BaseModel):
    """The one normalized shape every channel is converted into."""

    channel: Channel
    client_key: str  # stable per-client id: phone number, web session, caller id
    text: str
    display_name: Optional[str] = None
    media_urls: list[str] = Field(default_factory=list)
    external_message_id: Optional[str] = None
    received_at: datetime = Field(default_factory=now_utc)


# --------------------------------------------------------------------------
# Layer 2: conversation state
# --------------------------------------------------------------------------

class Stage(str, Enum):
    DISCOVERY = "discovery"            # learning about the business
    AUDIT = "audit"                    # site + competitor research
    PROPOSAL = "proposal"              # quote + proposal being prepared or reviewed
    AWAITING_PAYMENT = "awaiting_payment"
    ONBOARDING = "onboarding"          # paid, waiting for ad account access
    CREATIVE = "creative"              # building ads
    AWAITING_APPROVAL = "awaiting_approval"  # draft campaign waiting on a human in Slack
    LIVE = "live"
    PAUSED = "paused"
    HANDOFF = "handoff"                # a human has taken over


Platform = Literal["meta", "tiktok"]


class ClientProfile(BaseModel):
    business_name: Optional[str] = None
    contact_name: Optional[str] = None
    email: Optional[str] = None
    website: Optional[str] = None
    industry: Optional[str] = None
    location: Optional[str] = None
    country_code: Optional[str] = None  # ISO 3166 alpha-2, used for targeting and tax
    offer: Optional[str] = None  # what they sell, and the price point
    goal: Optional[str] = None  # leads, sales, app installs, awareness
    target_audience: Optional[str] = None
    monthly_ad_budget_minor: Optional[int] = None
    currency: Optional[str] = None
    platforms: list[Platform] = Field(default_factory=list)
    competitors: list[str] = Field(default_factory=list)
    target_cpa_minor: Optional[int] = None

    REQUIRED_FOR_AUDIT: ClassVar[tuple[str, ...]] = ("business_name", "industry", "offer", "goal")
    REQUIRED_FOR_QUOTE: ClassVar[tuple[str, ...]] = ("monthly_ad_budget_minor", "platforms")

    def missing(self, fields: tuple[str, ...]) -> list[str]:
        out = []
        for f in fields:
            v = getattr(self, f)
            if v is None or v == [] or v == "":
                out.append(f)
        return out


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    text: str
    at: datetime = Field(default_factory=now_utc)


# --------------------------------------------------------------------------
# Layer 3 outputs (LLM specialists, all structured)
# --------------------------------------------------------------------------

class SiteFindings(BaseModel):
    url: str
    reachable: bool
    status_code: Optional[int] = None
    https: bool = False
    title: Optional[str] = None
    meta_description: Optional[str] = None
    pixels: dict[str, bool] = Field(default_factory=dict)
    has_og_image: bool = False
    response_ms: Optional[int] = None
    error: Optional[str] = None


class CompetitorInsight(BaseModel):
    name: str
    positioning: str
    likely_ad_angles: list[str]
    weaknesses_to_exploit: list[str]


class AuditReport(BaseModel):
    summary: str
    tracking_gaps: list[str]
    competitors: list[CompetitorInsight]
    opportunities: list[str]
    recommended_platforms: list[Platform]
    recommended_package: Literal["starter", "growth", "scale"]
    sources: list[str] = Field(default_factory=list)


class ProposalSection(BaseModel):
    heading: str
    body: str


class Proposal(BaseModel):
    title: str
    executive_summary: str
    sections: list[ProposalSection]
    kpis: list[str]
    first_30_days: list[str]


class AdCreative(BaseModel):
    id: str = Field(default_factory=lambda: new_id("cr"))
    platform: Platform
    angle: str
    headline: str
    primary_text: str
    call_to_action: Literal["LEARN_MORE", "SHOP_NOW", "SIGN_UP", "CONTACT_US", "BOOK_NOW", "GET_QUOTE"]
    visual_brief: str
    audience_hint: str
    policy_notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Layer 4 outputs (deterministic)
# --------------------------------------------------------------------------

class LineItem(BaseModel):
    description: str
    amount_minor: int


class Quote(BaseModel):
    id: str = Field(default_factory=lambda: new_id("q"))
    package: Literal["starter", "growth", "scale"]
    currency: str
    line_items: list[LineItem]
    subtotal_minor: int
    tax_label: str
    tax_rate_bps: int  # basis points: 1800 = 18.00%
    tax_minor: int
    total_minor: int
    monthly_ad_budget_minor: int  # paid by the client straight to the platform, not to us
    valid_until: datetime
    created_at: datetime = Field(default_factory=now_utc)


class Guardrails(BaseModel):
    daily_budget_minor: int
    target_cpa_minor: Optional[int] = None
    max_overspend_ratio: float = 1.2
    min_ctr: float = 0.004
    min_impressions_for_ctr: int = 2000
    max_frequency: float = 4.0
    max_cpa_multiple: float = 2.5
    no_conversion_spend_multiple: float = 3.0


class CampaignDraft(BaseModel):
    platform: Platform
    name: str
    objective: str
    daily_budget_minor: int
    currency: str
    countries: list[str]
    age_min: int = 18
    age_max: int = 65
    landing_url: str
    creative_ids: list[str]


class Campaign(BaseModel):
    id: str = Field(default_factory=lambda: new_id("cmp"))
    client_key: str
    draft: CampaignDraft
    guardrails: Guardrails
    status: Literal["draft", "pending_approval", "active", "paused", "rejected"] = "draft"
    external_ids: dict[str, str] = Field(default_factory=dict)
    pause_reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now_utc)


class Approval(BaseModel):
    id: str = Field(default_factory=lambda: new_id("apr"))
    campaign_id: str
    client_key: str
    draft_hash: str  # approval is bound to this exact draft; any change needs a new approval
    status: Literal["pending", "approved", "rejected", "expired"] = "pending"
    requested_at: datetime = Field(default_factory=now_utc)
    expires_at: datetime
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    slack_ts: Optional[str] = None


# --------------------------------------------------------------------------
# The full per-client state the orchestrator reads and writes
# --------------------------------------------------------------------------

class ConversationState(BaseModel):
    client_key: str
    channel: Channel
    profile: ClientProfile = Field(default_factory=ClientProfile)
    history: list[Turn] = Field(default_factory=list)
    site: Optional[SiteFindings] = None
    audit: Optional[AuditReport] = None
    quote: Optional[Quote] = None
    proposal: Optional[Proposal] = None
    proposal_url: Optional[str] = None
    payment_status: Literal["none", "link_sent", "paid"] = "none"
    payment_link: Optional[str] = None
    ad_accounts_connected: dict[str, str] = Field(default_factory=dict)  # platform -> account id
    onboarding_link_sent: bool = False
    creatives: list[AdCreative] = Field(default_factory=list)
    campaign_ids: list[str] = Field(default_factory=list)
    handoff_reason: Optional[str] = None
    stage: Stage = Stage.DISCOVERY
    updated_at: datetime = Field(default_factory=now_utc)

    def snapshot(self) -> dict[str, Any]:
        """Compact view handed to the orchestrator each turn (no history)."""
        return {
            "stage": self.stage.value,
            "profile": self.profile.model_dump(),
            "site_checked": self.site is not None,
            "audit_done": self.audit is not None,
            "audit_summary": self.audit.summary if self.audit else None,
            "quote": (
                {"id": self.quote.id, "package": self.quote.package, "total_minor": self.quote.total_minor,
                 "currency": self.quote.currency}
                if self.quote else None
            ),
            "proposal_url": self.proposal_url,
            "payment_status": self.payment_status,
            "ad_accounts_connected": self.ad_accounts_connected,
            "onboarding_link_sent": self.onboarding_link_sent,
            "creatives": [{"id": c.id, "platform": c.platform, "headline": c.headline} for c in self.creatives],
            "campaign_ids": self.campaign_ids,
        }
