"""Deterministic event handlers: the only code paths that move money or go live.

  payment webhook   -> mark paid (amount and quote checked) -> send onboarding link
  Leadsie webhook   -> record ad account access
  Slack Approve     -> verify approver, expiry and draft hash -> turn the campaign on
  Slack Reject      -> campaign stays paused, client told the strategist wants changes

No model is involved in any of these decisions.
"""

from __future__ import annotations

import logging
from typing import Optional

from agency.integrations.leadsie import AccessGranted
from agency.integrations.payments import PaymentEvent
from agency.models import ConversationState, now_utc
from agency.orchestrator.state import derive_stage
from agency.orchestrator.tools import draft_hash
from agency.workers.pricing import format_money

log = logging.getLogger(__name__)


class EventError(Exception):
    pass


def _refresh(sv, state: ConversationState) -> None:
    state.stage = derive_stage(state, sv.repo.list_campaigns(client_key=state.client_key))
    state.updated_at = now_utc()
    sv.sessions.save(state)
    sv.repo.save_client(state)


def on_payment(sv, ev: PaymentEvent) -> None:
    with sv.sessions.lock(ev.client_key):
        state = sv.sessions.load(ev.client_key)
        if state is None or state.quote is None:
            raise EventError(f"payment for unknown client or quote: {ev.client_key}")
        if state.payment_status == "paid":
            return  # webhooks are retried; be idempotent
        q = state.quote
        if ev.quote_id != q.id or ev.currency != q.currency or ev.amount_minor < q.total_minor:
            sv.repo.log_event(ev.client_key, "payment.mismatch", ev.__dict__)
            sv.slack.notify(f":warning: Payment mismatch for `{ev.client_key}`: got "
                            f"{format_money(ev.amount_minor, ev.currency)} for {ev.quote_id}, expected "
                            f"{format_money(q.total_minor, q.currency)} for {q.id}. Check manually.")
            return
        state.payment_status = "paid"
        state.onboarding_link_sent = True
        sv.repo.log_event(ev.client_key, "payment.paid", ev.__dict__)
        _refresh(sv, state)
    link = sv.leadsie.connect_link(ev.client_key)
    sv.messenger.send(ev.client_key,
                      f"Payment received, thank you! {format_money(ev.amount_minor, ev.currency)} confirmed.\n\n"
                      "Next step takes 2 minutes: grant us access to your ad account, page and pixel here. "
                      "You stay the owner and can remove access any time.\n" + link)
    sv.slack.notify(f":moneybag: {state.profile.business_name or ev.client_key} paid "
                    f"{format_money(ev.amount_minor, ev.currency)} ({ev.provider} {ev.reference}).")


def on_access_granted(sv, grants: list[AccessGranted]) -> None:
    by_client: dict[str, list[AccessGranted]] = {}
    for g in grants:
        by_client.setdefault(g.client_key, []).append(g)
    for client_key, items in by_client.items():
        with sv.sessions.lock(client_key):
            state = sv.sessions.load(client_key)
            if state is None:
                log.warning("access granted for unknown client %s", client_key)
                continue
            for g in items:
                state.ad_accounts_connected[g.platform] = g.ad_account_id
            sv.repo.log_event(client_key, "access.granted", {"platforms": [g.platform for g in items]})
            _refresh(sv, state)
        sv.messenger.send(client_key, "Your ad account is connected. I'm drafting your first ads now; "
                                      "reply here any time with ideas, offers or things to avoid.")


def on_slack_decision(sv, approval_id: str, decision: str, slack_user: str) -> str:
    """Returns a short line to post back in Slack."""
    s = sv.settings
    if s.slack_approver_ids and slack_user not in s.slack_approver_ids:
        return f"<@{slack_user}> is not on the approver list; nothing changed."
    return decide(sv, approval_id, decision, slack_user, actor_label=f"<@{slack_user}>")


def decide(sv, approval_id: str, decision: str, actor: str, actor_label: Optional[str] = None) -> str:
    """The single approval path, shared by Slack and the web control room.
    The caller has already authenticated the actor."""
    if decision not in ("approve", "reject"):
        return "Unknown decision."
    slack_user = actor
    label = actor_label or actor
    approval = sv.repo.get_approval(approval_id)
    if approval is None:
        return "Unknown approval."
    if approval.status != "pending":
        return f"Already {approval.status}."
    if approval.expires_at < now_utc():
        approval.status = "expired"
        sv.repo.save_approval(approval)
        return "Approval expired; ask the agent to resubmit."
    campaign = sv.repo.get_campaign(approval.campaign_id)
    if campaign is None or campaign.status != "pending_approval":
        return "Campaign is no longer pending."

    with sv.sessions.lock(approval.client_key):
        state = sv.sessions.load(approval.client_key)
        if state is None:
            return "Client state missing."
        creatives = [c for c in state.creatives if c.id in campaign.draft.creative_ids]
        if draft_hash(campaign.draft, creatives) != approval.draft_hash:
            approval.status = "rejected"
            sv.repo.save_approval(approval)
            return "The draft changed after review was requested. Rejected; resubmit for a fresh review."

        approval.decided_by, approval.decided_at = slack_user, now_utc()
        if decision == "approve":
            sv.ads.for_platform(campaign.draft.platform).set_status(campaign, active=True)
            campaign.status = "active"
            approval.status = "approved"
            client_text = (f"Good news: your {campaign.draft.platform.title()} campaign is now live with a daily "
                           f"budget of {format_money(campaign.draft.daily_budget_minor, campaign.draft.currency)}. "
                           "I'll watch it closely and share the first numbers within 48 hours.")
        else:
            campaign.status = "rejected"
            approval.status = "rejected"
            client_text = ("Our strategist reviewed the ads and wants to sharpen them before launch. "
                           "I'll come back with an improved set shortly.")
        sv.repo.save_campaign(campaign)
        sv.repo.save_approval(approval)
        sv.repo.log_event(approval.client_key, f"approval.{approval.status}",
                          {"approval_id": approval.id, "campaign_id": campaign.id, "by": slack_user})
        _refresh(sv, state)
    sv.messenger.send(approval.client_key, client_text)
    return f"{'Launched' if decision == 'approve' else 'Rejected'} by {label}."


def resolve_dry_run_payment(sv, client_key: str) -> Optional[PaymentEvent]:
    """DRY_RUN helper: simulate the provider webhook for the client's current quote."""
    state = sv.sessions.load(client_key)
    if not state or not state.quote:
        return None
    q = state.quote
    return PaymentEvent(client_key, q.id, q.total_minor, q.currency, "dry_run", f"dry_{q.id}")
