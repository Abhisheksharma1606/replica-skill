"""The web app: client chat page, control room pages and the admin API."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from agency.agents.discovery import ProfileUpdate  # noqa: E402
from agency.app import build_services  # noqa: E402
from agency.config import Settings  # noqa: E402
from agency.gateway import create_app  # noqa: E402
from agency.models import Stage  # noqa: E402
from fakes import FakeLLM, last_tool_results, text, tool  # noqa: E402
from test_agency_flow import KEY, chat, world  # noqa: E402,F401  (fixture)


def test_pages_are_served(world):
    _, _, client, _ = world
    for path, marker in (("/", "webhooks/webchat"), ("/admin", "Control room"),
                         ("/static/admin.js", "api/admin"), ("/static/styles.css", "--accent")):
        r = client.get(path)
        assert r.status_code == 200 and marker in r.text, path
    assert client.get("/api/public/config").json()["dry_run"] is True


def test_admin_requires_token_when_set(tmp_path):
    settings = Settings(dry_run=True, files_dir=str(tmp_path), admin_token="adm-secret")
    client = TestClient(create_app(build_services(settings, llm=FakeLLM())))
    assert client.get("/api/admin/overview").status_code == 401
    assert client.get("/api/admin/overview", headers={"authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/admin/overview", headers={"authorization": "Bearer adm-secret"}).status_code == 200


def test_admin_refuses_open_access_outside_dry_run(tmp_path):
    settings = Settings(dry_run=False, files_dir=str(tmp_path), admin_token="")
    client = TestClient(create_app(build_services(settings, llm=FakeLLM())))
    assert client.get("/api/admin/overview").status_code == 503


def test_control_room_drives_the_pipeline(world):
    sv, llm, client, discovery_updates = world

    # A client arrives through the chat page and gets quoted.
    discovery_updates.append(ProfileUpdate(business_name="Glow Salon", industry="salon", offer="bridal 15k",
                                           goal="leads", website="glow.example", monthly_ad_budget=60000,
                                           currency="INR", platforms=["meta"], country_code="IN"))
    llm.brain_script = [[tool("run_competitor_audit", {})], [tool("build_quote", {"package": None, "extra_creative_packs": 0})],
                        [tool("send_payment_link", {})], [text("Here is your link.")]]
    chat(client, "Hi, Glow Salon, bridal makeup, 60k a month on Instagram")

    o = client.get("/api/admin/overview").json()
    assert o["stats"]["clients"] == 1
    [card] = o["clients"]
    assert card["name"] == "Glow Salon" and card["stage"] == "awaiting_payment"
    assert card["quote_total"] == "INR 47,200.00"

    d = client.get(f"/api/admin/clients/{KEY}").json()
    assert d["quote_summary"]["total"] == "INR 47,200.00"
    assert d["state"]["audit"]["summary"]
    assert any(e["kind"] == "tool.build_quote" for e in d["events"])
    assert d["brand_brain"]  # site description and audit opportunities were remembered

    # Simulators move the client like the real webhooks would.
    assert client.post(f"/api/admin/clients/{KEY}/simulate", json={"event": "payment"}).json()["stage"] == "onboarding"
    assert client.post(f"/api/admin/clients/{KEY}/simulate", json={"event": "access_meta"}).json()["stage"] == "creative"

    # The agents build ads and ask for approval.
    def submit(kw):
        ids = [c["id"] for c in json.loads(last_tool_results(kw)[0]["content"])["creatives"]]
        return [tool("request_launch_approval", {"platform": "meta", "creative_ids": ids,
                                                 "daily_budget": None, "landing_url": None})]
    llm.brain_script = [[tool("generate_ad_creatives", {"platform": "meta", "count": 2, "feedback": None})],
                        submit, [text("Sent for review.")]]
    chat(client, "Show me ads")

    [pending] = client.get("/api/admin/approvals").json()["approvals"]
    assert pending["daily_budget"] == "INR 2,000.00" and len(pending["creatives"]) == 2

    # Approve from the browser: same path as Slack, so it goes live once.
    aid = pending["approval"]["id"]
    r = client.post(f"/api/admin/approvals/{aid}", json={"decision": "approve", "reviewer": "asha"})
    assert r.json()["result"] == "Launched by web:asha."
    assert client.post(f"/api/admin/approvals/{aid}", json={"decision": "approve"}).json()["result"] == "Already approved."
    assert sv.sessions.load(KEY).stage == Stage.LIVE
    assert sum(1 for c in sv.ads.dry.calls if c[0] == "set_status") == 1
    assert client.get("/api/admin/overview").json()["stats"]["live_campaigns"] == 1

    agents = client.get("/api/admin/agents").json()
    assert {a["id"] for a in agents["agents"]} >= {"orchestrator", "discovery", "competitor_audit",
                                                    "proposal_writer", "creative", "pricing", "killswitch"}
    assert "request_launch_approval" in agents["tool_stages"]
    assert client.post("/api/admin/killswitch").status_code == 200


def test_human_takeover_and_release(world):
    sv, llm, client, _ = world
    llm.brain_script = [[tool("handoff_to_human", {"reason": "price negotiation"})], [text("A strategist will reply.")]]
    chat(client, "Can you do it cheaper?")
    assert sv.sessions.load(KEY).stage == Stage.HANDOFF

    assert client.post(f"/api/admin/clients/{KEY}/message", json={"text": "Hi, Ravi here. Let's talk."}).status_code == 200
    assert sv.sessions.load(KEY).history[-1].text == "Hi, Ravi here. Let's talk."
    assert "Hi, Ravi here. Let's talk." in client.get("/webchat/sess_glow_001/outbox").json()["messages"]

    assert client.post(f"/api/admin/clients/{KEY}/release").json()["stage"] == "discovery"
    llm.brain_script = [[text("Back to it! What's your monthly budget?")]]
    assert "budget" in chat(client, "ok")["reply"]


def test_client_text_is_escaped_in_the_control_room(world):
    _, _, client, _ = world
    js = client.get("/static/admin.js").text
    # Every client-supplied field goes through esc(); there is no raw innerHTML of state text.
    assert "const esc = " in js
    assert "${cl.name}" not in js and "${t.text}" not in js and "${cl.last_message}" not in js
