import hashlib
import hmac
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agency.ingestion import normalize  # noqa: E402
from agency.integrations.messaging import verify_slack  # noqa: E402
from agency.integrations.payments import verify_razorpay, verify_stripe  # noqa: E402
from agency.models import (AuditReport, Campaign, CampaignDraft, ClientProfile, ConversationState,  # noqa: E402
                           Channel, Guardrails, Stage)
from agency.orchestrator.state import derive_stage, tool_allowed  # noqa: E402
from agency.workers import pricing  # noqa: E402
from agency.workers.killswitch import Metrics, evaluate  # noqa: E402
from agency.workers.scraper import analyze_html, detect_pixels, normalize_url, tracking_gaps, UnsafeURL  # noqa: E402


# ------------------------------------------------------------------ pricing

def test_growth_quote_inr_with_gst():
    q = pricing.build_quote(currency="INR", monthly_ad_budget=2_00_000_00, platforms=["meta", "tiktok"])
    assert q.package == "growth"
    # 45,000 management + 15,000 setup; two platforms are included in growth
    assert q.subtotal_minor == 60_000_00
    assert q.tax_rate_bps == 1800
    assert q.tax_minor == 10_800_00
    assert q.total_minor == 70_800_00
    assert pricing.format_money(q.total_minor, "INR") == "INR 70,800.00"


def test_scale_quote_has_performance_fee_and_extra_platform_is_free():
    q = pricing.build_quote(currency="INR", monthly_ad_budget=10_00_000_00, platforms=["meta"])
    assert q.package == "scale"
    perf = [i for i in q.line_items if i.description.startswith("Performance fee")]
    assert perf and perf[0].amount_minor == 40_000_00  # 8% of 5L above the 5L threshold


def test_starter_extra_platform_and_creatives():
    q = pricing.build_quote(currency="USD", monthly_ad_budget=3_000_00, platforms=["meta", "tiktok"],
                            package="starter", extra_creative_packs=2, waive_setup=True)
    amounts = {i.description.split(" x")[0]: i.amount_minor for i in q.line_items}
    assert amounts["Additional platform"] == 300_00
    assert amounts["Extra creative pack"] == 500_00
    assert q.tax_minor == 0 and q.total_minor == 900_00 + 300_00 + 500_00


def test_pricing_rejects_bad_input():
    for kwargs in ({"monthly_ad_budget": 0, "platforms": ["meta"]},
                   {"monthly_ad_budget": 100, "platforms": []}):
        try:
            pricing.build_quote(currency="INR", **kwargs)
        except pricing.PricingError:
            continue
        raise AssertionError("expected PricingError")
    try:
        pricing.build_quote(currency="XYZ", monthly_ad_budget=100, platforms=["meta"])
    except pricing.PricingError:
        pass
    else:
        raise AssertionError("unknown currency should fail")


def test_indian_grouping():
    assert pricing.format_money(1_23_45_678_90, "INR") == "INR 1,23,45,678.90"
    assert pricing.format_money(999_00, "INR") == "INR 999.00"
    assert pricing.format_money(1_234_567_00, "USD") == "USD 1,234,567.00"


# ------------------------------------------------------------------ scraper

HTML = """<html><head><title> Glow  Salon | Bandra </title>
<meta name="description" content="Bridal makeup and hair in Bandra">
<meta property="og:image" content="https://x/y.jpg">
<script>!function(f,b,e,v){}(window,document,'script','https://connect.facebook.net/en_US/fbevents.js');
fbq('init', '123');</script>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-ABC123"></script>
</head></html>"""


def test_pixel_detection_and_gaps():
    px = detect_pixels(HTML)
    assert px["meta_pixel"] and px["google_analytics_4"]
    assert not px["tiktok_pixel"] and not px["google_ads"]
    site = analyze_html("https://glow.example", HTML, 200, 300)
    assert site.title == "Glow Salon | Bandra"
    assert site.meta_description == "Bridal makeup and hair in Bandra"
    assert site.has_og_image
    gaps = tracking_gaps(site, ["meta", "tiktok"])
    assert any("TikTok Pixel" in g for g in gaps)
    assert not any("Meta Pixel" in g for g in gaps)


def test_url_normalization_rejects_non_web():
    assert normalize_url("glow.example") == "https://glow.example"
    try:
        normalize_url("ftp://x")
    except UnsafeURL:
        pass
    else:
        raise AssertionError


# ------------------------------------------------------------------ kill-switch

def test_killswitch_rules():
    g = Guardrails(daily_budget_minor=1000_00, target_cpa_minor=200_00)
    assert evaluate(Metrics(spend_minor=500_00, impressions=1000, clicks=20, conversions=3), g) == []
    rules = {v.rule for v in evaluate(Metrics(spend_minor=1300_00), g)}
    assert rules == {"overspend", "no_conversions"}
    v = evaluate(Metrics(spend_minor=800_00, conversions=0), g)
    assert [x.rule for x in v] == ["no_conversions"]
    v = evaluate(Metrics(spend_minor=1100_00, conversions=3), g)  # CPA 366 < 2.5x200
    assert v == []
    v = evaluate(Metrics(spend_minor=1100_00, conversions=3, impressions=5000, clicks=5, frequency=5), g)
    assert {x.rule for x in v} == {"low_ctr", "high_frequency"}
    assert all(x.action == "alert" for x in v)


# ------------------------------------------------------------------ state machine

def _state(**kw) -> ConversationState:
    return ConversationState(client_key="web:abcdef", channel=Channel.WEBCHAT, **kw)


def test_stage_is_derived_from_facts():
    s = _state()
    assert derive_stage(s) == Stage.DISCOVERY
    s.profile = ClientProfile(business_name="Glow", industry="salon", offer="bridal 15k", goal="leads",
                              platforms=["meta"], monthly_ad_budget_minor=50_000_00)
    assert derive_stage(s) == Stage.AUDIT
    s.audit = AuditReport(summary="x", tracking_gaps=[], competitors=[], opportunities=[],
                          recommended_platforms=["meta"], recommended_package="starter")
    assert derive_stage(s) == Stage.PROPOSAL
    s.payment_status = "link_sent"
    assert derive_stage(s) == Stage.AWAITING_PAYMENT
    s.payment_status = "paid"
    assert derive_stage(s) == Stage.ONBOARDING
    s.ad_accounts_connected = {"meta": "act_1"}
    assert derive_stage(s) == Stage.CREATIVE
    draft = CampaignDraft(platform="meta", name="n", objective="OUTCOME_LEADS", daily_budget_minor=100,
                          currency="INR", countries=["IN"], landing_url="https://x", creative_ids=[])
    c = Campaign(client_key=s.client_key, draft=draft, guardrails=Guardrails(daily_budget_minor=100),
                 status="pending_approval")
    assert derive_stage(s, [c]) == Stage.AWAITING_APPROVAL
    c.status = "active"
    assert derive_stage(s, [c]) == Stage.LIVE
    s.handoff_reason = "asked for a human"
    assert derive_stage(s, [c]) == Stage.HANDOFF


def test_tool_gating():
    assert not tool_allowed("request_launch_approval", Stage.PROPOSAL)
    assert not tool_allowed("send_payment_link", Stage.DISCOVERY)
    assert tool_allowed("send_payment_link", Stage.PROPOSAL)
    assert tool_allowed("handoff_to_human", Stage.LIVE)
    assert not tool_allowed("build_quote", Stage.HANDOFF)


# ------------------------------------------------------------------ ingestion and signatures

def test_whatsapp_normalization():
    payload = {"entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": "919800000000", "profile": {"name": "Priya"}}],
        "messages": [
            {"from": "919800000000", "id": "wamid.1", "type": "text", "text": {"body": " Hi there "}},
            {"from": "919800000000", "id": "wamid.2", "type": "image", "image": {"id": "m1", "caption": "logo"}},
            {"from": "919800000000", "id": "wamid.3", "type": "reaction", "reaction": {}},
        ]}}]}]}
    msgs = normalize.from_whatsapp(payload)
    assert [m.text for m in msgs] == ["Hi there", "[sent a image] logo"]
    assert msgs[0].client_key == "wa:919800000000" and msgs[0].display_name == "Priya"
    assert msgs[1].media_urls == ["whatsapp-media:m1"]
    assert normalize.from_whatsapp({"entry": [{"changes": [{"value": {"statuses": [{}]}}]}]}) == []


def test_voice_and_whatsapp_share_a_client_key():
    v = normalize.from_voice(normalize.VoiceTurnIn(call_id="c1", caller="+91 98000 00000", transcript="hello"))
    assert v.client_key == "wa:919800000000"


def test_signatures():
    body = b'{"a":1}'
    sig = "sha256=" + hmac.new(b"app", body, hashlib.sha256).hexdigest()
    assert normalize.verify_meta_signature("app", body, sig)
    assert not normalize.verify_meta_signature("app", body + b" ", sig)
    assert not normalize.verify_meta_signature("", body, sig)

    rz = hmac.new(b"rz", body, hashlib.sha256).hexdigest()
    assert verify_razorpay("rz", body, rz) and not verify_razorpay("rz", body, "00")

    ts = str(int(time.time()))
    st = hmac.new(b"whsec", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert verify_stripe("whsec", body, f"t={ts},v1={st}")
    assert not verify_stripe("whsec", body, f"t={int(ts) - 1000},v1={st}")

    sl = "v0=" + hmac.new(b"slack", f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    assert verify_slack("slack", body, ts, sl)
    assert not verify_slack("slack", body, str(int(ts) - 1000), sl)
