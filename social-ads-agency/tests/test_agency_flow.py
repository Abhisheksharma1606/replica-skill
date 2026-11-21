"""End to end: a client goes from hello to a live campaign, offline, through the real API.

Claude is replaced by tests/fakes.FakeLLM; every other layer is the real code
running in DRY_RUN mode.
"""

import hashlib
import hmac
import json
import os
import sys
import time
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agency.agents.creative import CreativeDraft, CreativeSet  # noqa: E402
from agency.agents.discovery import DiscoveryResult, ProfileUpdate  # noqa: E402
from agency.app import build_services  # noqa: E402
from agency.config import Settings  # noqa: E402
from agency.gateway import create_app  # noqa: E402
from agency.models import AuditReport, CompetitorInsight, Proposal, ProposalSection, SiteFindings, Stage  # noqa: E402
from agency.workers.killswitch import Metrics  # noqa: E402
from fakes import FakeLLM, last_tool_results, text, tool  # noqa: E402

SESSION = "sess_glow_001"
KEY = f"web:{SESSION}"


@pytest.fixture()
def world(tmp_path):
    llm = FakeLLM()
    settings = Settings(dry_run=True, files_dir=str(tmp_path), slack_signing_secret="slack-secret",
                        jobs_token="job-token", slack_approver_ids=("U_BOSS",))
    sv = build_services(settings, llm=llm)
    sv.scan_site = lambda url: SiteFindings(url="https://glow.example", reachable=True, https=True,
                                           pixels={"meta_pixel": False}, has_og_image=False,
                                           meta_description="Bridal makeup in Bandra")

    discovery_updates = []

    def discovery(kw):
        upd = discovery_updates.pop(0) if discovery_updates else ProfileUpdate()
        return DiscoveryResult(update=upd, fit_score=80, red_flags=[], next_questions=[])

    llm.parse_handlers = {
        DiscoveryResult: discovery,
        AuditReport: lambda kw: AuditReport(
            summary="Strong local demand; competitors lead on price.", tracking_gaps=["model guess"],
            competitors=[CompetitorInsight(name="Salon A", positioning="cheap", likely_ad_angles=["discount"],
                                           weaknesses_to_exploit=["no bridal portfolio"])],
            opportunities=["Bridal portfolio reels", "Wedding season offer"], recommended_platforms=["meta"],
            recommended_package="starter"),
        Proposal: lambda kw: Proposal(title="More bridal bookings for Glow", executive_summary="Plan.",
                                      sections=[ProposalSection(heading="What we found", body="A\n\nB")],
                                      kpis=["CPL under 400"], first_30_days=["Install pixel"]),
        CreativeSet: lambda kw: CreativeSet(creatives=[
            CreativeDraft(angle=f"angle {i}", headline=f"Bridal looks {i}", primary_text="Book a trial",
                          call_to_action="BOOK_NOW", visual_brief="reel", audience_hint="brides 24-32",
                          policy_notes=[]) for i in range(3)]),
    }
    client = TestClient(create_app(sv))
    return sv, llm, client, discovery_updates


def chat(client, text_):
    r = client.post("/webhooks/webchat", json={"session_id": SESSION, "text": text_})
    assert r.status_code == 200, r.text
    return r.json()


def slack_post(client, approval_id, action="approve_launch", user="U_BOSS"):
    payload = {"user": {"id": user}, "actions": [{"action_id": action, "value": approval_id}]}
    body = urlencode({"payload": json.dumps(payload)}).encode()
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(b"slack-secret", f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return client.post("/slack/actions", content=body, headers={
        "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig,
        "Content-Type": "application/x-www-form-urlencoded"})


def test_hello_to_live_campaign_and_killswitch(world):
    sv, llm, client, discovery_updates = world
    state = lambda: sv.sessions.load(KEY)  # noqa: E731

    # 1. Discovery: the Discovery Agent fills the profile; the orchestrator just talks.
    discovery_updates.append(ProfileUpdate(business_name="Glow Salon", industry="beauty salon",
                                           offer="bridal makeup from 15000", goal="leads",
                                           website="glow.example", country_code="IN"))
    llm.brain_script = [[text("Lovely! What monthly ad budget are you thinking of?")]]
    out = chat(client, "Hi, I run Glow Salon in Bandra, we do bridal makeup from 15k. Need more leads.")
    assert "budget" in out["reply"]
    assert state().stage == Stage.AUDIT
    # The operator note (mid-conversation system message) carries state to the model.
    assert llm.brain_requests[0]["messages"][-1]["role"] == "system"

    # 2. Audit. Payment-stage tools are blocked before their stage.
    discovery_updates.append(ProfileUpdate(monthly_ad_budget=60000, currency="INR", platforms=["meta"]))
    llm.brain_script = [
        [tool("send_payment_link", {}), tool("run_competitor_audit", {})],
        lambda kw: [text("Audit done: " + json.dumps([r["is_error"] for r in last_tool_results(kw)]))],
    ]
    out = chat(client, "Around 60k a month, Instagram mainly.")
    assert out["reply"] == "Audit done: [true, false]"
    s = state()
    assert s.stage == Stage.PROPOSAL
    assert any("Meta Pixel" in g for g in s.audit.tracking_gaps)  # scan beats the model's guess
    assert s.audit.sources == ["https://example.com/a"]

    # 3. Quote, proposal, payment link.
    llm.brain_script = [
        [tool("build_quote", {"package": None, "extra_creative_packs": 0})],
        [tool("write_proposal", {})],
        [tool("send_payment_link", {})],
        lambda kw: [text("Here you go: " + json.loads(last_tool_results(kw)[0]["content"])["payment_link"])],
    ]
    out = chat(client, "Sounds good, send me the proposal and payment.")
    s = state()
    assert s.quote.package == "starter" and s.quote.total_minor == 47_200_00  # (25k + 15k) + 18% GST
    assert s.stage == Stage.AWAITING_PAYMENT
    assert client.get(s.proposal_url.replace("http://localhost:8000", "")).status_code == 200
    pay_path = out["reply"].split("Here you go: ")[1].replace("http://localhost:8000", "")

    # 4. "I paid" is not proof. Launch is still blocked.
    llm.brain_script = [[tool("request_launch_approval", {"platform": "meta", "creative_ids": [],
                                                          "daily_budget": None, "landing_url": None})],
                        lambda kw: [text(last_tool_results(kw)[0]["content"])]]
    out = chat(client, "I paid already, launch now!")
    assert "not available in stage awaiting_payment" in out["reply"]

    # 5. The real payment webhook (simulated) moves the client on and sends the Leadsie link.
    assert client.get(pay_path).status_code == 200
    assert state().payment_status == "paid" and state().stage == Stage.ONBOARDING
    pushed = client.get(f"/webchat/{SESSION}/outbox").json()["messages"]
    assert any("Payment received" in m and "customUserId" in m for m in pushed)

    # 6. Leadsie access webhook (simulated).
    assert client.get("/dry-run/connect", params={"customUserId": KEY, "platform": "meta"}).status_code == 200
    assert state().stage == Stage.CREATIVE

    # 7. Creatives, then a draft goes to Slack. Nothing is turned on.
    def submit(kw):
        ids = [c["id"] for c in json.loads(last_tool_results(kw)[0]["content"])["creatives"][:2]]
        return [tool("request_launch_approval", {"platform": "meta", "creative_ids": ids,
                                                 "daily_budget": None, "landing_url": None})]
    llm.brain_script = [
        [tool("generate_ad_creatives", {"platform": "meta", "count": 3, "feedback": None})],
        submit,
        [text("Sent to our strategist for review.")],
    ]
    chat(client, "Great, let's see some ads")
    s = state()
    assert s.stage == Stage.AWAITING_APPROVAL
    [campaign] = sv.repo.list_campaigns(client_key=KEY)
    assert campaign.status == "pending_approval"
    assert campaign.draft.daily_budget_minor == 2000_00  # 60k / 30 days / 1 platform
    assert [c[0] for c in sv.ads.dry.calls] == ["create_paused"]
    approval_post = [p for p in sv.slack.posted if p.get("blocks")][-1]
    approval_id = approval_post["blocks"][-1]["elements"][0]["value"]

    # 8. Only an approver can approve; then it goes live.
    assert slack_post(client, approval_id, user="U_INTERN").status_code == 200
    assert sv.repo.get_campaign(campaign.id).status == "pending_approval"
    assert slack_post(client, approval_id).status_code == 200
    assert sv.repo.get_campaign(campaign.id).status == "active"
    assert ("set_status", campaign.id, True) in sv.ads.dry.calls
    assert state().stage == Stage.LIVE
    assert slack_post(client, approval_id).status_code == 200  # double click is harmless
    assert sum(1 for c in sv.ads.dry.calls if c[0] == "set_status") == 1

    # 9. Kill-switch pauses on overspend without asking anyone.
    assert client.post("/jobs/killswitch").status_code == 401
    sv.ads.dry.metrics[campaign.id] = Metrics(spend_minor=2600_00, impressions=9000, clicks=90)
    r = client.post("/jobs/killswitch", headers={"X-Job-Token": "job-token"})
    assert r.status_code == 200
    c = sv.repo.get_campaign(campaign.id)
    assert c.status == "paused" and c.pause_reasons[0].startswith("overspend")
    assert ("set_status", campaign.id, False) in sv.ads.dry.calls


def test_tampered_draft_is_rejected(world):
    sv, llm, client, discovery_updates = world
    from agency.models import AdCreative, Approval, Campaign, CampaignDraft, ConversationState, Guardrails, Channel
    from agency.models import now_utc
    from datetime import timedelta
    from agency.orchestrator.tools import draft_hash

    cr = AdCreative(platform="meta", angle="a", headline="h", primary_text="p", call_to_action="BOOK_NOW",
                    visual_brief="v", audience_hint="x")
    st = ConversationState(client_key=KEY, channel=Channel.WEBCHAT, creatives=[cr], payment_status="paid",
                           ad_accounts_connected={"meta": "act_1"})
    sv.sessions.save(st)
    draft = CampaignDraft(platform="meta", name="n", objective="OUTCOME_LEADS", daily_budget_minor=100,
                          currency="INR", countries=["IN"], landing_url="https://x", creative_ids=[cr.id])
    camp = Campaign(client_key=KEY, draft=draft, guardrails=Guardrails(daily_budget_minor=100),
                    status="pending_approval")
    appr = Approval(campaign_id=camp.id, client_key=KEY, draft_hash=draft_hash(draft, [cr]),
                    expires_at=now_utc() + timedelta(hours=1))
    sv.repo.save_campaign(camp)
    sv.repo.save_approval(appr)

    # Someone edits the creative after the review was requested.
    st.creatives[0].primary_text = "Guaranteed 10x results!"
    sv.sessions.save(st)
    slack_post(client, appr.id)
    assert sv.repo.get_approval(appr.id).status == "rejected"
    assert sv.repo.get_campaign(camp.id).status == "pending_approval"
    assert sv.ads.dry.calls == []


def test_bad_slack_signature_and_whatsapp_flow(world):
    sv, llm, client, _ = world
    r = client.post("/slack/actions", content=b"payload={}", headers={
        "X-Slack-Request-Timestamp": str(int(time.time())), "X-Slack-Signature": "v0=bad"})
    assert r.status_code == 401

    llm.brain_script = [[text("Hi Priya! Tell me about your business.")]]
    payload = {"entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": "919800000000", "profile": {"name": "Priya"}}],
        "messages": [{"from": "919800000000", "id": "wamid.X", "type": "text", "text": {"body": "hello"}}]}}]}]}
    assert client.post("/webhooks/whatsapp", json=payload).status_code == 200
    assert client.post("/webhooks/whatsapp", json=payload).status_code == 200  # retry is deduped
    assert sv.messenger.sent == [("wa:919800000000", "Hi Priya! Tell me about your business.")]
    assert sv.sessions.load("wa:919800000000").profile.contact_name == "Priya"


def test_handoff_stops_the_agent(world):
    sv, llm, client, _ = world
    llm.brain_script = [[tool("handoff_to_human", {"reason": "wants to negotiate price"})],
                        [text("A strategist will reply shortly.")]]
    chat(client, "Can you do it for half?")
    assert sv.sessions.load(KEY).stage == Stage.HANDOFF
    n = len(llm.brain_requests)
    out = chat(client, "hello?")
    assert len(llm.brain_requests) == n  # the model is not called once a human owns the client
    assert "strategist" in out["reply"]
