# Social Ads Agency Agent

One AI account manager that runs a social media ads agency from first hello to live
campaigns: it talks to clients on WhatsApp, web chat and phone, audits their site and
competitors, quotes and writes the proposal, collects payment, gets ad account access,
writes the ads, and hands every launch to a human in Slack before anything spends money.
A kill-switch watches live campaigns and pauses the ones that go wrong.

All the specialist agents sit behind **one orchestrator agent**. The client always talks
to one voice; the orchestrator calls the specialists and the deterministic workers as tools.

```
+-------------------------------------------------------------------------+
|  1. CLIENT INGESTION        agency/ingestion/normalize.py               |
|  [WhatsApp Business API]      [Web Chat Widget]      [Voice AI Agent]   |
+------------------------------------+------------------------------------+
                                     | InboundMessage (one shape for all)
                                     v
+-------------------------------------------------------------------------+
|  2. ORCHESTRATION           agency/gateway.py   agency/session.py       |
|  FastAPI gateway  <-->  Redis lock + session state                      |
|        |                                                                |
|        +--> Orchestrator (the ONE agent)   agency/orchestrator/brain.py |
|        |      stage machine                agency/orchestrator/state.py |
|        |      guarded tools                agency/orchestrator/tools.py |
|        +--> Event handlers (no LLM)        agency/orchestrator/events.py|
|        +--> Jobs: cron / n8n / Temporal -> POST /jobs/killswitch        |
+--------------+---------------------+--------------------+---------------+
               v                     v                    v
+--------------------+  +-------------------------+  +--------------------+
| 3. AGENTS (Claude) |  | 4. DETERMINISTIC        |  | 5. MEMORY          |
| agency/agents/     |  | agency/workers/         |  | agency/memory/     |
| - discovery        |  | - pricing (rate + GST)  |  | - repository       |
| - competitor_audit |  | - scraper (pixels,SSRF) |  |   (Postgres)       |
| - proposal_writer  |  | - documents (proposal)  |  | - brand_brain      |
| - creative         |  | - killswitch (rules)    |  |   (pgvector)       |
+--------------------+  +------------+------------+  +--------------------+
                                     v
+-------------------------------------------------------------------------+
|  6. EXTERNAL APIs + HUMAN GATE       agency/integrations/               |
|  payments.py (Razorpay/Stripe)  leadsie.py  ads.py (Meta/TikTok)        |
|  messaging.py: WhatsApp outbound + Slack approval gate (mandatory)      |
+-------------------------------------------------------------------------+
```

## How "all agents in one agent" works

| Agent | What it does | When it runs | Output |
| --- | --- | --- | --- |
| **Orchestrator** (account manager) | Talks to the client, decides the next step, calls tools | Every message | Reply text |
| **Discovery Agent** | Extracts business facts into the profile, scores fit, flags restricted categories | Automatically before every orchestrator turn (low effort) | `DiscoveryResult` |
| **Competitor Audit Agent** | Live web research (Claude web search) plus the deterministic site and pixel scan | Tool `run_competitor_audit` | `AuditReport` |
| **Proposal Writer Agent** | Writes the proposal; prices are inserted from the quote, never written by the model | Tool `write_proposal` | `Proposal` + document link |
| **Creative Agent** | Platform-native ads from the Brand Brain, with an ad-policy self-check | Tool `generate_ad_creatives` | `AdCreative` list |

Every specialist returns a validated Pydantic object (structured outputs), so nothing is
parsed out of free text. All Claude calls use `claude-opus-5-5` with adaptive thinking,
an effort level per role, prompt caching on the static system prompts, and server-side
refusal fallback (`fallbacks: "default"`) so a declined request is retried on a fallback
model inside the same call.

## The client journey and its gates

The stage is **derived from facts**, never set by the model (`orchestrator/state.py`):

```
DISCOVERY -> AUDIT -> PROPOSAL -> AWAITING_PAYMENT -> ONBOARDING -> CREATIVE
   -> AWAITING_APPROVAL -> LIVE <-> PAUSED            (any stage) -> HANDOFF
```

| Gate | Moved by | Never by |
| --- | --- | --- |
| Price | `workers/pricing.py` rate card, integer money, GST 18% for INR | The model (it can only quote what `build_quote` returned) |
| Paid | Verified Razorpay / Stripe webhook, amount and quote id checked | The client saying "I paid" |
| Access | Leadsie webhook | Chat |
| Live | A listed approver pressing Approve in Slack; approval expires and is bound to a hash of the exact draft | The model. Campaigns are created PAUSED; there is no "launch" tool |
| Paused | Kill-switch rules (overspend, no conversions, CPA blowout); pausing needs no approval because it only reduces spend | |
| Handoff | `handoff_to_human` (price negotiation, upset client, legal); the model stops replying | |

Tools outside their stage return a tool error with guidance, so the model corrects itself
instead of crashing. Client messages are treated as untrusted; the client's real state is
passed to the model as a mid-conversation system message from the agency's own systems.

## Run it locally (no keys, no spend)

```bash
cd social-ads-agency
pip install -r requirements.txt
python -m pytest -q tests          # 17 offline tests, full journey with a fake Claude

export ANTHROPIC_API_KEY=...       # or: ant auth login
DRY_RUN=true uvicorn agency.gateway:create_app --factory --port 8000
```

Talk to it:

```bash
curl -s localhost:8000/webhooks/webchat -H 'content-type: application/json' \
  -d '{"session_id":"demo-0001","text":"Hi, I run Glow Salon in Bandra, bridal makeup from 15k. Want more bookings."}'
```

With `DRY_RUN=true`, payments, Leadsie, WhatsApp, Slack and the ad platforms are
simulated: the payment link opens `/dry-run/pay/<quote>` which fires the paid event, and
the onboarding link opens `/dry-run/connect` which fires the access event. Claude calls
are real (they need your API key).

Full stack with Postgres + pgvector + Redis + kill-switch loop:

```bash
cp .env.example .env    # fill in
docker compose up --build
```

## Go-live checklist

1. **Claude**: API key, then try a few real conversations in DRY_RUN and read the transcripts.
2. **WhatsApp Cloud API**: app, phone number, webhook to `/webhooks/whatsapp` with the verify
   token, app secret set. Messages the system sends on its own (payment received, campaign live)
   fall outside WhatsApp's 24-hour reply window when the client has been quiet, so register
   **message templates** for those and send them as templates.
3. **Voice**: point your voice AI platform's custom-LLM webhook at `/webhooks/voice` with
   `X-Voice-Secret`. Map its payload to `{call_id, caller, transcript}`.
4. **Razorpay / Stripe**: keys, then webhooks to `/webhooks/razorpay` (`payment_link.paid`)
   and `/webhooks/stripe` (`checkout.session.completed`) with their signing secrets.
5. **Leadsie**: set `LEADSIE_CONNECT_URL` and the webhook to `/webhooks/leadsie`. Check the
   client id query parameter and the webhook field names on your plan against
   `integrations/leadsie.py` (`CLIENT_PARAM`, `parse_webhook`) and adjust if they differ.
6. **Meta**: a system user token with `ads_management`, your Page id. Test on one internal
   ad account first. Creatives are link ads; add image upload (`/adimages`) for your own visuals.
7. **TikTok**: the code creates the campaign shell (disabled). Ad groups need TikTok location
   ids and ads need uploaded video, so the strategist finishes those in Ads Manager during
   review, or extend `TikTokAds.create_paused`.
8. **Slack**: app with `chat:write` and Interactivity pointed at `/slack/actions`; set the
   signing secret, approval channel and `SLACK_APPROVER_IDS`.
9. **Kill-switch**: the compose file runs it every 15 minutes; or call `POST /jobs/killswitch`
   with `X-Job-Token` from an n8n Cron node or a Temporal schedule.
10. Flip `DRY_RUN=false`.

## Change the business

- Prices, packages, tax: `RATE_CARD` and `TAX` in `agency/workers/pricing.py`.
- Kill-switch thresholds: `Guardrails` in `agency/models.py`.
- Voice and rules of the account manager: `SYSTEM_TEMPLATE` in `agency/orchestrator/brain.py`.
- What each stage is for: `_GUIDANCE` in `agency/orchestrator/state.py`.
- A new specialist: subclass `Specialist` in `agency/agents/`, add a tool in
  `orchestrator/tools.py`, list its stages in `TOOL_STAGES`.

## Notes

- Session state lives in Redis (lock per client, so a double message never races);
  Postgres keeps clients, quotes, campaigns, approvals and an append-only event log
  (`db/schema.sql`). Without `REDIS_URL` / `DATABASE_URL` everything runs in memory.
- Brand Brain embeddings use Voyage AI when `VOYAGE_API_KEY` is set, otherwise a local
  hashing embedder that is fine for testing but weak for real recall.
- The website scanner only fetches public http(s) addresses, checks every redirect hop
  and caps the download size, because the URL comes from a chat message.
- Proposals are HTML (PDF too if WeasyPrint is installed) at unguessable `/files/<id>` URLs.
