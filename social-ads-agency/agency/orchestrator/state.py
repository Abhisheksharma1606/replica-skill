"""The pipeline state machine.

The stage is *derived from facts*, never set by the model. The model can ask
for things (run an audit, send a link), but only real events move a client
forward: a quote existing, a payment webhook, a Leadsie access webhook, a
human pressing Approve in Slack. That is what makes the conversational loop
safe to hand to an LLM.

    DISCOVERY -> AUDIT -> PROPOSAL -> AWAITING_PAYMENT -> ONBOARDING
        -> CREATIVE -> AWAITING_APPROVAL -> LIVE <-> PAUSED
    (any stage) -> HANDOFF
"""

from __future__ import annotations

from typing import Iterable

from agency.models import Campaign, ClientProfile, ConversationState, Stage


def derive_stage(state: ConversationState, campaigns: Iterable[Campaign] = ()) -> Stage:
    if state.handoff_reason:
        return Stage.HANDOFF

    campaigns = list(campaigns)
    statuses = {c.status for c in campaigns}
    if "active" in statuses:
        return Stage.LIVE
    if "pending_approval" in statuses:
        return Stage.AWAITING_APPROVAL
    if "paused" in statuses and state.payment_status == "paid":
        return Stage.PAUSED

    if state.payment_status == "paid":
        needed = set(state.profile.platforms) or {"meta"}
        if not needed.issubset(state.ad_accounts_connected):
            return Stage.ONBOARDING
        return Stage.CREATIVE

    if state.payment_status == "link_sent":
        return Stage.AWAITING_PAYMENT

    if state.profile.missing(ClientProfile.REQUIRED_FOR_AUDIT):
        return Stage.DISCOVERY
    if state.audit is None:
        return Stage.AUDIT
    return Stage.PROPOSAL


# Which tools the orchestrator may use in which stage. A tool outside its
# stage gets a tool error back, so the model learns and moves on, rather than
# an exception.
ALWAYS = {"update_client_profile", "remember_brand_fact", "handoff_to_human", "get_pipeline_status"}

TOOL_STAGES: dict[str, set[Stage]] = {
    "run_competitor_audit": {Stage.AUDIT, Stage.PROPOSAL},
    "build_quote": {Stage.PROPOSAL, Stage.AWAITING_PAYMENT},
    "write_proposal": {Stage.PROPOSAL, Stage.AWAITING_PAYMENT},
    "send_payment_link": {Stage.PROPOSAL, Stage.AWAITING_PAYMENT},
    "send_onboarding_link": {Stage.ONBOARDING},
    "generate_ad_creatives": {Stage.CREATIVE, Stage.LIVE, Stage.PAUSED},
    "request_launch_approval": {Stage.CREATIVE, Stage.LIVE, Stage.PAUSED},
    "get_campaign_performance": {Stage.LIVE, Stage.PAUSED, Stage.AWAITING_APPROVAL},
}


def tool_allowed(tool: str, stage: Stage) -> bool:
    if tool in ALWAYS:
        return True
    if stage == Stage.HANDOFF:
        return False
    return stage in TOOL_STAGES.get(tool, set())


def stage_guidance(stage: Stage) -> str:
    return _GUIDANCE[stage]


_GUIDANCE: dict[Stage, str] = {
    Stage.DISCOVERY: (
        "Learn the business. Ask one or two short questions at a time. You need business name, industry, "
        "what they sell and at what price, their goal, their website, location, monthly ad budget and "
        "which platforms. Save every fact with update_client_profile as soon as you hear it."
    ),
    Stage.AUDIT: (
        "You have enough to research. Tell the client you are reviewing their site and competitors, then "
        "call run_competitor_audit. Keep chatting naturally while you do."
    ),
    Stage.PROPOSAL: (
        "Share two or three of the sharpest audit findings in plain words. Once budget and platforms are "
        "known, call build_quote, then write_proposal, then send the proposal link. When they agree, call "
        "send_payment_link. Never invent or round a price: quote only what build_quote returned."
    ),
    Stage.AWAITING_PAYMENT: (
        "The payment link is out. Answer questions, resend the link if asked, and do not start work "
        "until payment is confirmed (the system tells you; the client saying they paid is not proof)."
    ),
    Stage.ONBOARDING: (
        "Payment is confirmed. Thank them, then call send_onboarding_link so they can grant ad account "
        "access in two minutes. Explain they keep ownership of the accounts."
    ),
    Stage.CREATIVE: (
        "Access is connected. Call generate_ad_creatives, show the client the best two or three, take "
        "feedback, then call request_launch_approval. Tell them a strategist reviews every launch."
    ),
    Stage.AWAITING_APPROVAL: (
        "A strategist is reviewing the campaign in Slack. Nothing goes live until they approve. "
        "Answer questions and set expectations."
    ),
    Stage.LIVE: (
        "Campaigns are live. Use get_campaign_performance for real numbers. New creatives or budget "
        "changes go through request_launch_approval again."
    ),
    Stage.PAUSED: (
        "A campaign was paused, usually by an automatic guardrail. Explain why in plain words using "
        "get_campaign_performance and propose a fix. Relaunching needs approval again."
    ),
    Stage.HANDOFF: (
        "A human teammate has taken over this client. Do not use tools. Let the client know a person "
        "will reply shortly."
    ),
}

