"""The orchestrator's tools: how the one agent reaches every specialist and worker.

Each tool is a thin, guarded adapter. The guard order is always:
  1. is this tool allowed in the client's current stage?   (state.py)
  2. are the facts it needs present?                        (here)
  3. run the specialist or the deterministic worker
  4. write results to state, return a short JSON summary to the model

Money-moving actions are not tools. The model can draft a campaign and ask
for approval; only a human in Slack can turn it on (events.py).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from typing import Any, Callable

from agency.agents.base import SpecialistError
from agency.agents.competitor_audit import CompetitorAuditAgent
from agency.agents.creative import CreativeAgent
from agency.agents.proposal_writer import ProposalWriterAgent
from agency.models import (AdCreative, Approval, Campaign, CampaignDraft, ConversationState, Guardrails,
                           now_utc)
from agency.orchestrator.state import derive_stage, stage_guidance, tool_allowed
from agency.workers import pricing
from agency.workers.documents import render_proposal_html, save_document

log = logging.getLogger(__name__)


class ToolError(Exception):
    """Returned to the model as an is_error tool result; the model adapts."""


def _schema(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_pipeline_status",
        "description": "Get the client's current stage, profile, quote, payment, access and campaign status. "
                       "Call this when unsure what has already happened.",
        "input_schema": _schema({}),
    },
    {
        "name": "remember_brand_fact",
        "description": "Save a durable fact about the client's brand to the Brand Brain so creatives use it: "
                       "tone of voice, offers, audience insights, objections, proof points, claims they must "
                       "never make, or ads that worked before.",
        "input_schema": _schema({
            "kind": {"type": "string",
                     "enum": ["tone", "offer", "audience", "objection", "proof", "banned_claim", "winner"]},
            "text": {"type": "string", "description": "One fact, one sentence."},
        }),
    },
    {
        "name": "handoff_to_human",
        "description": "Hand the conversation to a human strategist. Use when the client asks for a person, "
                       "is upset, negotiates on price, raises a legal or refund issue, or is in a restricted "
                       "category you cannot judge.",
        "input_schema": _schema({"reason": {"type": "string"}}),
    },
    {
        "name": "run_competitor_audit",
        "description": "Scan the client's website for tracking pixels and research their competitors on the "
                       "web. Takes about a minute. Needs business name, industry, offer and goal.",
        "input_schema": _schema({}),
    },
    {
        "name": "build_quote",
        "description": "Price the engagement from the rate card using the client's monthly ad budget, "
                       "currency and platforms. The only source of prices. Pass package null to use the "
                       "recommended package.",
        "input_schema": _schema({
            "package": {"type": ["string", "null"], "enum": ["starter", "growth", "scale", None]},
            "extra_creative_packs": {"type": "integer", "description": "0 unless the client asked for more ads"},
        }),
    },
    {
        "name": "write_proposal",
        "description": "Write the proposal from the audit and the quote and publish it as a link the client "
                       "can open. Needs a finished audit and a quote.",
        "input_schema": _schema({}),
    },
    {
        "name": "send_payment_link",
        "description": "Create a payment link for the current quote (Razorpay for INR, Stripe otherwise). "
                       "Only after the client has agreed to the proposal.",
        "input_schema": _schema({}),
    },
    {
        "name": "send_onboarding_link",
        "description": "Get the Leadsie link the client uses to grant ad account, page and pixel access. "
                       "Only after payment is confirmed.",
        "input_schema": _schema({}),
    },
    {
        "name": "generate_ad_creatives",
        "description": "Have the Creative Agent write ads for one platform from the Brand Brain and audit. "
                       "Pass the client's feedback on earlier ads, or null.",
        "input_schema": _schema({
            "platform": {"type": "string", "enum": ["meta", "tiktok"]},
            "count": {"type": "integer", "description": "1 to 6"},
            "feedback": {"type": ["string", "null"]},
        }),
    },
    {
        "name": "request_launch_approval",
        "description": "Build the campaign on the platform in a PAUSED state and send it to a human "
                       "strategist in Slack for approval. Does not launch anything. Pass daily_budget null "
                       "to derive it from the monthly budget, and landing_url null to use their website.",
        "input_schema": _schema({
            "platform": {"type": "string", "enum": ["meta", "tiktok"]},
            "creative_ids": {"type": "array", "items": {"type": "string"}},
            "daily_budget": {"type": ["integer", "null"], "description": "Whole currency units per day"},
            "landing_url": {"type": ["string", "null"]},
        }),
    },
    {
        "name": "get_campaign_performance",
        "description": "Today's spend, impressions, clicks, conversions and any automatic pause reasons for "
                       "this client's campaigns.",
        "input_schema": _schema({}),
    },
]
for _t in TOOLS:
    _t["strict"] = True

GOAL_TO_OBJECTIVE = {
    "leads": "OUTCOME_LEADS",
    "sales": "OUTCOME_SALES",
    "traffic": "OUTCOME_TRAFFIC",
    "awareness": "OUTCOME_AWARENESS",
    "app_installs": "OUTCOME_TRAFFIC",
}


def draft_hash(draft: CampaignDraft, creatives: list[AdCreative]) -> str:
    blob = draft.model_dump_json() + "|" + "|".join(c.model_dump_json() for c in creatives)
    return hashlib.sha256(blob.encode()).hexdigest()


class ToolExecutor:
    def __init__(self, services, state: ConversationState) -> None:
        self.sv = services
        self.state = state
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            t["name"]: getattr(self, f"_{t['name']}") for t in TOOLS
        }

    # ---------------------------------------------------------------- entry

    def run(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """Returns (json content, is_error)."""
        handler = self._handlers.get(name)
        if handler is None:
            return json.dumps({"error": f"unknown tool {name}"}), True
        if not tool_allowed(name, self.state.stage):
            return json.dumps({"error": f"{name} is not available in stage {self.state.stage.value}",
                               "what_to_do_now": stage_guidance(self.state.stage)}), True
        try:
            result = handler(args)
        except ToolError as exc:
            return json.dumps({"error": str(exc)}), True
        except SpecialistError as exc:
            log.warning("specialist failed in %s: %s", name, exc)
            return json.dumps({"error": f"temporary failure, tell the client you will follow up: {exc}"}), True
        self.refresh_stage()
        self.sv.repo.log_event(self.state.client_key, f"tool.{name}", {"args": args})
        return json.dumps(result, default=str), False

    def refresh_stage(self) -> None:
        campaigns = self.sv.repo.list_campaigns(client_key=self.state.client_key)
        self.state.stage = derive_stage(self.state, campaigns)

    # ---------------------------------------------------------------- always available

    def _get_pipeline_status(self, _: dict) -> dict:
        return {**self.state.snapshot(), "guidance": stage_guidance(self.state.stage)}

    def _remember_brand_fact(self, a: dict) -> dict:
        self.sv.brand.remember(self.state.client_key, a["kind"], a["text"])
        return {"saved": True}

    def _handoff_to_human(self, a: dict) -> dict:
        self.state.handoff_reason = a["reason"][:500]
        name = self.state.profile.business_name or self.state.client_key
        self.sv.slack.notify(f":raising_hand: Handoff requested for *{name}* (`{self.state.client_key}`): "
                             f"{self.state.handoff_reason}")
        return {"handed_off": True, "tell_client": "A strategist from the team will reply here shortly."}

    # ---------------------------------------------------------------- audit and proposal

    def _run_competitor_audit(self, _: dict) -> dict:
        p = self.state.profile
        missing = p.missing(p.REQUIRED_FOR_AUDIT)
        if missing:
            raise ToolError(f"ask the client for: {', '.join(missing)}")
        if p.website:
            self.state.site = self.sv.scan_site(p.website)
            if self.state.site.reachable and self.state.site.meta_description:
                self.sv.brand.remember(self.state.client_key, "offer",
                                       f"Website says: {self.state.site.meta_description}")
        agent = CompetitorAuditAgent(self.sv.llm, self.sv.settings)
        audit = agent.run(p, self.state.site)
        self.state.audit = audit
        for opp in audit.opportunities[:3]:
            self.sv.brand.remember(self.state.client_key, "audience", f"Opportunity: {opp}")
        if not p.platforms and audit.recommended_platforms:
            p.platforms = list(audit.recommended_platforms)
        return {
            "summary": audit.summary,
            "tracking_gaps": audit.tracking_gaps,
            "top_opportunities": audit.opportunities[:3],
            "competitors": [c.name for c in audit.competitors],
            "recommended_package": audit.recommended_package,
            "recommended_platforms": audit.recommended_platforms,
        }

    def _build_quote(self, a: dict) -> dict:
        p = self.state.profile
        missing = p.missing(p.REQUIRED_FOR_QUOTE)
        if missing:
            raise ToolError(f"ask the client for: {', '.join(missing)}")
        currency = p.currency or self.sv.settings.default_currency
        try:
            quote = pricing.build_quote(
                currency=currency,
                monthly_ad_budget=p.monthly_ad_budget_minor or 0,
                platforms=list(p.platforms),
                package=a.get("package"),
                extra_creative_packs=int(a.get("extra_creative_packs") or 0),
            )
        except pricing.PricingError as exc:
            raise ToolError(str(exc)) from exc
        self.state.quote = quote
        self.state.proposal = None
        self.state.proposal_url = None
        if self.state.payment_status == "link_sent":
            self.state.payment_status = "none"  # old link is for the old amount
            self.state.payment_link = None
        self.sv.repo.save_quote(self.state.client_key, quote)
        fm = lambda v: pricing.format_money(v, quote.currency)  # noqa: E731
        return {
            "quote_id": quote.id,
            "package": quote.package,
            "line_items": [{"item": i.description, "amount": fm(i.amount_minor)} for i in quote.line_items],
            "subtotal": fm(quote.subtotal_minor),
            "tax": f"{quote.tax_label}: {fm(quote.tax_minor)}",
            "total_due": fm(quote.total_minor),
            "ad_budget_note": f"Ad spend of {fm(quote.monthly_ad_budget_minor)}/month is paid to the platforms "
                              "directly by the client and is not part of this total.",
            "valid_until": quote.valid_until.date().isoformat(),
        }

    def _write_proposal(self, _: dict) -> dict:
        if not self.state.audit:
            raise ToolError("run_competitor_audit first")
        if not self.state.quote:
            raise ToolError("build_quote first")
        agent = ProposalWriterAgent(self.sv.llm, self.sv.settings)
        proposal = agent.run(self.sv.settings.agency_name, self.state.profile, self.state.audit, self.state.quote)
        doc = save_document(self.sv.settings.files_dir,
                            render_proposal_html(self.sv.settings.agency_name, self.state.profile,
                                                 proposal, self.state.quote))
        self.state.proposal = proposal
        self.state.proposal_url = f"{self.sv.settings.public_base_url}/files/{doc.file_id}"
        return {"title": proposal.title, "url": self.state.proposal_url,
                "executive_summary": proposal.executive_summary}

    def _send_payment_link(self, _: dict) -> dict:
        q = self.state.quote
        if not q:
            raise ToolError("build_quote first")
        if q.valid_until < now_utc():
            raise ToolError("quote expired; call build_quote again")
        if self.state.payment_status == "link_sent" and self.state.payment_link:
            return {"payment_link": self.state.payment_link, "note": "same link as before"}
        link = self.sv.payments.create_link(self.state.client_key, q, self.state.profile)
        self.state.payment_status = "link_sent"
        self.state.payment_link = link.url
        self.sv.repo.log_event(self.state.client_key, "payment.link_created",
                               {"provider": link.provider, "reference": link.reference, "quote_id": q.id})
        return {"payment_link": link.url,
                "amount": pricing.format_money(q.total_minor, q.currency),
                "note": "Work starts automatically once the payment is confirmed."}

    # ---------------------------------------------------------------- onboarding and creative

    def _send_onboarding_link(self, _: dict) -> dict:
        self.state.onboarding_link_sent = True
        return {"onboarding_link": self.sv.leadsie.connect_link(self.state.client_key),
                "platforms_needed": self.state.profile.platforms or ["meta"]}

    def _generate_ad_creatives(self, a: dict) -> dict:
        count = max(1, min(6, int(a["count"])))
        platform = a["platform"]
        memories = self.sv.brand.recall(self.state.client_key,
                                        f"{platform} ad for {self.state.profile.offer} {a.get('feedback') or ''}")
        agent = CreativeAgent(self.sv.llm, self.sv.settings)
        new = agent.run(self.state.profile, self.state.audit, memories, platform, count, a.get("feedback"))
        self.state.creatives = (self.state.creatives + new)[-24:]
        return {"creatives": [
            {"id": c.id, "angle": c.angle, "headline": c.headline, "primary_text": c.primary_text,
             "cta": c.call_to_action, "visual": c.visual_brief, "policy_notes": c.policy_notes}
            for c in new
        ]}

    def _request_launch_approval(self, a: dict) -> dict:
        st, p, s = self.state, self.state.profile, self.sv.settings
        platform = a["platform"]
        if st.payment_status != "paid":
            raise ToolError("payment is not confirmed")
        account = st.ad_accounts_connected.get(platform)
        if not account:
            raise ToolError(f"no {platform} ad account access yet; send the onboarding link")
        chosen = [c for c in st.creatives if c.id in set(a["creative_ids"]) and c.platform == platform]
        if not chosen:
            raise ToolError(f"no matching {platform} creatives; ids must come from generate_ad_creatives")
        if not p.monthly_ad_budget_minor:
            raise ToolError("monthly ad budget unknown")

        n_platforms = max(1, len(p.platforms))
        planned_daily = p.monthly_ad_budget_minor // 30 // n_platforms
        if a.get("daily_budget") is not None:
            daily = int(a["daily_budget"]) * 100
            if daily > planned_daily * 3 // 2:
                raise ToolError(f"daily budget is above 1.5x the planned {planned_daily // 100}/day; "
                                "confirm a higher monthly budget with the client first")
            if daily <= 0:
                raise ToolError("daily budget must be positive")
        else:
            daily = planned_daily
        landing = a.get("landing_url") or p.website
        if not landing:
            raise ToolError("no landing page: ask for the website or landing page URL")
        if not landing.startswith("http"):
            landing = "https://" + landing

        draft = CampaignDraft(
            platform=platform,
            name=f"{(p.business_name or 'Client')[:40]} | {p.goal or 'traffic'} | {now_utc():%Y-%m-%d}",
            objective=GOAL_TO_OBJECTIVE.get(p.goal or "traffic", "OUTCOME_TRAFFIC"),
            daily_budget_minor=daily,
            currency=p.currency or s.default_currency,
            countries=[p.country_code or ("IN" if (p.currency or s.default_currency) == "INR" else "US")],
            landing_url=landing,
            creative_ids=[c.id for c in chosen],
        )
        campaign = Campaign(client_key=st.client_key, draft=draft,
                            guardrails=Guardrails(daily_budget_minor=daily, target_cpa_minor=p.target_cpa_minor),
                            external_ids={"ad_account": account})
        campaign.external_ids = self.sv.ads.for_platform(platform).create_paused(campaign, chosen)
        campaign.status = "pending_approval"
        approval = Approval(campaign_id=campaign.id, client_key=st.client_key,
                            draft_hash=draft_hash(draft, chosen),
                            expires_at=now_utc() + timedelta(hours=s.approval_ttl_hours))
        approval.slack_ts = self.sv.slack.request_approval(approval, campaign, chosen,
                                                           p.business_name or st.client_key)
        self.sv.repo.save_campaign(campaign)
        self.sv.repo.save_approval(approval)
        st.campaign_ids.append(campaign.id)
        return {"campaign_id": campaign.id, "status": "sent to a strategist for approval; nothing is live yet",
                "daily_budget": pricing.format_money(daily, draft.currency), "ads": len(chosen)}

    def _get_campaign_performance(self, _: dict) -> dict:
        out = []
        for c in self.sv.repo.list_campaigns(client_key=self.state.client_key):
            row: dict[str, Any] = {"campaign_id": c.id, "platform": c.draft.platform, "status": c.status,
                                   "daily_budget": pricing.format_money(c.draft.daily_budget_minor, c.draft.currency),
                                   "pause_reasons": c.pause_reasons}
            if c.status in ("active", "paused"):
                try:
                    m = self.sv.ads.for_platform(c.draft.platform).today_metrics(c)
                    row["today"] = {"spend": pricing.format_money(m.spend_minor, c.draft.currency),
                                    "impressions": m.impressions, "clicks": m.clicks, "ctr": f"{m.ctr:.2%}",
                                    "conversions": m.conversions}
                except Exception as exc:  # report, do not crash the conversation
                    row["today"] = f"metrics unavailable: {exc}"
            out.append(row)
        return {"campaigns": out}
