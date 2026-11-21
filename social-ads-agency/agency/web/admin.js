(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
  };
  // Everything shown here can contain client-written text: always escape.
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const when = (t) => { if (!t) return ""; const d = new Date(t); return isNaN(d) ? "" : d.toLocaleString(); };
  const ago = (t) => {
    const s = Math.max(0, (Date.now() - new Date(t).getTime()) / 1000);
    if (s < 60) return "just now"; if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago"; return Math.floor(s / 86400) + "d ago";
  };
  const STAGE_LABEL = {
    discovery: "Discovery", audit: "Audit", proposal: "Proposal", awaiting_payment: "Awaiting payment",
    onboarding: "Onboarding", creative: "Creative", awaiting_approval: "Awaiting approval", live: "Live",
    paused: "Paused", handoff: "Human handoff",
  };
  const STAGE_TONE = { live: "ok", paused: "bad", handoff: "warn", awaiting_approval: "warn", awaiting_payment: "accent" };
  const stagePill = (s) => `<span class="pill ${STAGE_TONE[s] || ""}">${esc(STAGE_LABEL[s] || s)}</span>`;

  let token = store.get("admin.token") || "";
  let tab = "pipeline";
  let openKey = null, dTab = "overview", detail = null, cfg = { dry_run: false };

  async function api(path, opts = {}) {
    const r = await fetch("/api/admin" + path, {
      ...opts,
      headers: { "content-type": "application/json", ...(token ? { authorization: "Bearer " + token } : {}) },
    });
    if (r.status === 401 || r.status === 503) { showLogin(r.status === 401 && token); throw new Error("auth"); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || ("HTTP " + r.status));
    return data;
  }
  function toast(msg) {
    const t = $("toast"); t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 3500);
  }
  function showLogin(bad) { $("login").hidden = false; $("loginErr").hidden = !bad; }
  $("tokenBtn").onclick = () => { token = $("tokenInput").value.trim(); store.set("admin.token", token);
    $("login").hidden = true; refresh(); };
  $("tokenInput").onkeydown = (e) => { if (e.key === "Enter") $("tokenBtn").click(); };

  // ------------------------------------------------------------ theme
  const applyTheme = (t) => { if (t) document.documentElement.dataset.theme = t; };
  applyTheme(store.get("admin.theme"));
  $("themeBtn").onclick = () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark"; applyTheme(next); store.set("admin.theme", next);
  };

  // ------------------------------------------------------------ tabs
  $("tabs").onclick = (e) => {
    const b = e.target.closest("button[data-tab]"); if (!b) return;
    tab = b.dataset.tab;
    document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
    document.querySelectorAll("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== tab));
    refresh();
  };

  // ------------------------------------------------------------ overview + pipeline
  async function loadOverview() {
    const o = await api("/overview");
    cfg = o;
    $("agency").textContent = o.agency; $("logo").textContent = (o.agency || "A")[0]; $("dryBadge").hidden = !o.dry_run;
    const st = o.stats;
    $("stats").innerHTML = [
      ["Clients", st.clients], ["Live campaigns", st.live_campaigns], ["Paused", st.paused_campaigns],
      ["Waiting on you", st.pending_approvals], ["Collected", st.revenue_collected.join(" + ")],
      ["Live daily budget", st.live_daily_budget.join(" + ")],
    ].map(([k, v]) => `<div class="card stat"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("");
    const c = $("approvalCount"); c.hidden = !st.pending_approvals; c.textContent = st.pending_approvals;

    if (tab !== "pipeline") return;
    const by = {}; o.stages.forEach((s) => (by[s] = []));
    o.clients.forEach((cl) => (by[cl.stage] = by[cl.stage] || []).push(cl));
    $("board").innerHTML = o.clients.length ? o.stages.map((s) => `
      <div class="col"><div class="col-head"><span>${esc(STAGE_LABEL[s] || s)}</span><span>${by[s].length}</span></div>
      ${by[s].map((cl) => `
        <div class="card client" data-key="${esc(cl.client_key)}" tabindex="0">
          <b>${esc(cl.name)}</b>
          <div class="faint small">${esc(cl.industry || "")} ${cl.industry ? "&middot;" : ""} ${esc(ago(cl.updated_at))}</div>
          <div class="last">${esc(cl.last_message)}</div>
          <div class="tags"><span class="pill">${esc(cl.channel)}</span>
            ${cl.budget ? `<span class="pill">${esc(cl.budget)}/mo</span>` : ""}
            ${(cl.platforms || []).map((p) => `<span class="pill accent">${esc(p)}</span>`).join("")}</div>
        </div>`).join("")}
      </div>`).join("")
      : `<div class="card empty" style="grid-column:1/-1">No clients yet. Open the <a href="/" target="_blank" rel="noopener">client chat</a>
         and say hello: the agents take it from there.</div>`;
  }
  $("board").addEventListener("click", (e) => { const c = e.target.closest(".client"); if (c) openClient(c.dataset.key); });
  $("board").addEventListener("keydown", (e) => { const c = e.target.closest(".client"); if (c && e.key === "Enter") openClient(c.dataset.key); });

  // ------------------------------------------------------------ approvals
  function adCards(creatives) {
    return `<div class="ads">${creatives.map((c) => `
      <div class="ad"><div class="h">${esc(c.headline)}</div><div class="t">${esc(c.primary_text)}</div>
        <div class="v">${esc(c.call_to_action)} &middot; ${esc(c.angle)}</div>
        <div class="v">Visual: ${esc(c.visual_brief)}</div>
        ${(c.policy_notes || []).length ? `<div class="v" style="color:var(--warn)">Check: ${esc(c.policy_notes.join("; "))}</div>` : ""}
      </div>`).join("")}</div>`;
  }
  async function loadApprovals() {
    const { approvals } = await api("/approvals");
    $("approvals").innerHTML = approvals.length ? approvals.map((a) => {
      const d = a.campaign.draft, g = a.campaign.guardrails;
      return `<div class="card approval">
        <div class="row-head"><div><h3 style="margin:0">${esc(a.client.name)}</h3>
          <div class="muted small">${esc(d.name)}</div></div>
          <span class="pill warn">expires ${esc(when(a.approval.expires_at))}</span></div>
        <div class="approval-grid">
          ${kv("Platform", d.platform)}${kv("Objective", d.objective)}${kv("Daily budget", a.daily_budget)}
          ${kv("Countries / ages", d.countries.join(", ") + " / " + d.age_min + "-" + d.age_max)}
          ${kv("Landing page", d.landing_url)}${kv("Kill-switch", "pause above " + g.max_overspend_ratio + "x budget")}
        </div>
        ${adCards(a.creatives)}
        <div class="btn-row">
          <button class="btn ok" data-approve="${esc(a.approval.id)}">Approve and launch</button>
          <button class="btn danger" data-reject="${esc(a.approval.id)}">Reject</button>
          <button class="btn" data-open="${esc(a.client.client_key)}">Open client</button>
        </div></div>`;
    }).join("") : `<div class="card empty">Nothing waiting. When the Creative Agent's ads are ready, the campaign shows up here.</div>`;
  }
  const kv = (k, v) => `<div class="kv"><div class="k">${esc(k)}</div><div class="v">${esc(v == null || v === "" ? "-" : v)}</div></div>`;
  $("approvals").addEventListener("click", async (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.open) return openClient(b.dataset.open);
    const id = b.dataset.approve || b.dataset.reject; if (!id) return;
    const decision = b.dataset.approve ? "approve" : "reject";
    if (decision === "approve" && !confirm("Go live? This starts spending the client's ad budget.")) return;
    b.disabled = true;
    try { const r = await api("/approvals/" + encodeURIComponent(id), { method: "POST",
        body: JSON.stringify({ decision, reviewer: $("reviewer").value || "admin" }) });
      toast(r.result); refresh();
    } catch (err) { if (err.message !== "auth") toast(err.message); b.disabled = false; }
  });

  // ------------------------------------------------------------ agents
  async function loadAgents() {
    const a = await api("/agents");
    $("modelName").textContent = "Model: " + a.model;
    $("agents").innerHTML = a.agents.map((x) => `
      <div class="card agent"><div class="hd"><b>${esc(x.name)}</b>
        <span class="pill ${x.kind === "llm" ? "accent" : ""}">${x.kind === "llm" ? "Claude" : "Code"}${x.effort ? " &middot; " + esc(x.effort) : ""}</span></div>
        <div class="faint small">${esc(x.layer)}</div><p>${esc(x.what)}</p>
        ${x.tools.length ? `<div>${x.tools.map((t) => `<code>${esc(t)}</code>`).join(" ")}</div>` : ""}</div>`).join("");
    const stages = Object.keys(STAGE_LABEL).filter((s) => s !== "handoff");
    const rows = Object.entries(a.tool_stages);
    $("matrix").innerHTML = `<table class="matrix"><tr><th>Tool</th>${stages.map((s) => `<th>${esc(STAGE_LABEL[s])}</th>`).join("")}</tr>
      ${rows.map(([t, ss]) => `<tr><td><code>${esc(t)}</code></td>${stages.map((s) => ss.includes(s) ? '<td class="y">&#10003;</td>' : '<td class="n">&middot;</td>').join("")}</tr>`).join("")}
      <tr><td colspan="${stages.length + 1}" class="muted">Always available: ${a.always_available.map((t) => `<code>${esc(t)}</code>`).join(" ")}</td></tr></table>`;
    $("integrations").innerHTML = a.integrations.map((i) => `<div class="integ"><span>${esc(i.name)}</span>
      <span class="pill ${i.ok ? "ok" : (cfg.dry_run ? "warn" : "bad")}">${i.ok ? "connected" : (cfg.dry_run ? "simulated" : "not set")}${i.note ? " &middot; " + esc(i.note) : ""}</span></div>`).join("");
  }
  $("killBtn").onclick = async () => {
    try { const { report } = await api("/killswitch", { method: "POST" });
      const r = $("killReport"); r.hidden = false;
      r.textContent = report.length ? JSON.stringify(report, null, 2) : "No live campaigns to check.";
      toast("Kill-switch sweep finished"); refresh();
    } catch (e) { if (e.message !== "auth") toast(e.message); }
  };

  // ------------------------------------------------------------ activity
  const evRow = (e) => `<div class="ev"><span class="faint">${esc(when(e.at))}</span>
    <span><b>${esc(e.kind)}</b><br><span class="faint">${esc(e.client_key)}</span></span>
    <code>${esc(JSON.stringify(e.data))}</code></div>`;
  async function loadActivity() {
    const { events } = await api("/events");
    $("activity").innerHTML = events.length ? events.map(evRow).join("") : `<div class="empty">No activity yet.</div>`;
  }

  // ------------------------------------------------------------ client drawer
  async function openClient(key) {
    openKey = key; $("drawer").hidden = false; document.body.style.overflow = "hidden";
    await loadClient();
  }
  function closeDrawer() { openKey = null; detail = null; $("drawer").hidden = true; document.body.style.overflow = ""; }
  $("dClose").onclick = closeDrawer;
  $("drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") closeDrawer(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && openKey) closeDrawer(); });
  $("dTabs").onclick = (e) => { const b = e.target.closest("button[data-d]"); if (!b) return; dTab = b.dataset.d;
    document.querySelectorAll("#dTabs button").forEach((x) => x.classList.toggle("active", x === b)); renderDrawer(); };

  async function loadClient() {
    if (!openKey) return;
    detail = await api("/clients/" + encodeURIComponent(openKey));
    renderDrawer();
  }

  function renderDrawer() {
    if (!detail) return;
    const st = detail.state, p = st.profile, card = detail.card;
    $("dName").textContent = card.name;
    $("dMeta").innerHTML = `${stagePill(st.stage)} &middot; ${esc(st.channel)} &middot; <code>${esc(st.client_key)}</code>`;
    const acts = [];
    if (cfg.dry_run && st.quote && st.payment_status !== "paid") acts.push(`<button class="btn sm" data-sim="payment">Simulate payment</button>`);
    if (cfg.dry_run && st.payment_status === "paid") {
      if (!st.ad_accounts_connected.meta) acts.push(`<button class="btn sm" data-sim="access_meta">Simulate Meta access</button>`);
      if (!st.ad_accounts_connected.tiktok && (p.platforms || []).includes("tiktok")) acts.push(`<button class="btn sm" data-sim="access_tiktok">Simulate TikTok access</button>`);
    }
    if (st.handoff_reason) acts.push(`<button class="btn sm primary" data-release="1">Hand back to the agent</button>`);
    $("dActions").innerHTML = acts.join("") + (st.handoff_reason ? `<span class="pill warn">Handoff: ${esc(st.handoff_reason)}</span>` : "");

    const body = $("dBody");
    const sec = (title, html) => `<div class="card section"><h3>${esc(title)}</h3>${html}</div>`;
    const list = (xs) => xs && xs.length ? `<ul class="tight">${xs.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : `<div class="faint">none</div>`;

    if (dTab === "overview") {
      const fields = [["Business", p.business_name], ["Contact", p.contact_name], ["Email", p.email], ["Website", p.website],
        ["Industry", p.industry], ["Location", p.location], ["Country", p.country_code], ["Offer", p.offer], ["Goal", p.goal],
        ["Audience", p.target_audience], ["Monthly ad budget", card.budget], ["Platforms", (p.platforms || []).join(", ")],
        ["Target cost per result", p.target_cpa_minor ? (p.target_cpa_minor / 100).toLocaleString() + " " + (p.currency || "") : ""],
        ["Competitors", (p.competitors || []).join(", ")]];
      const progress = [["Audit", !!st.audit], ["Quote", !!st.quote], ["Proposal", !!st.proposal_url],
        ["Payment", st.payment_status === "paid"], ["Ad access", Object.keys(st.ad_accounts_connected).length > 0],
        ["Creatives", st.creatives.length > 0], ["Campaign", st.campaign_ids.length > 0]];
      body.innerHTML = sec("Progress", `<div class="tags" style="display:flex;gap:6px;flex-wrap:wrap">${progress.map(([k, ok]) =>
          `<span class="pill ${ok ? "ok" : ""}">${ok ? "&#10003; " : ""}${esc(k)}</span>`).join("")}</div>`)
        + sec("Profile (kept current by the Discovery Agent)", `<div class="profile">${fields.map(([k, v]) => kv(k, v)).join("")}</div>`);
    } else if (dTab === "conversation") {
      body.innerHTML = sec("Conversation", `<div class="bubbles">${st.history.map((t) =>
          `<div class="b ${t.role}">${esc(t.text)}<span class="ts">${t.role === "user" ? "Client" : "Agency"} &middot; ${esc(when(t.at))}</span></div>`).join("")
          || '<div class="faint">No messages.</div>'}</div>
        <div class="composer"><textarea class="input" id="humanMsg" placeholder="Write to the client as a human strategist"></textarea>
        <button class="btn primary" id="humanSend">Send</button></div>
        <div class="faint small" style="margin-top:6px">Sent on the client's channel. Use this after a handoff; the agent sees it in the history.</div>`);
      const bubbles = body.querySelector(".bubbles"); bubbles.lastElementChild && bubbles.lastElementChild.scrollIntoView({ block: "nearest" });
    } else if (dTab === "audit") {
      const a = st.audit, site = st.site;
      body.innerHTML = (site ? sec("Website scan (Pixel and Site Scanner)", `<div class="profile">${kv("URL", site.url)}${kv("Reachable", site.reachable ? "yes" : "no: " + (site.error || ""))}
          ${kv("HTTPS", site.https ? "yes" : "no")}${kv("Title", site.title)}${kv("Response", site.response_ms ? site.response_ms + " ms" : "")}</div>
          <div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">${Object.entries(site.pixels || {}).map(([k, v]) =>
            `<span class="pill ${v ? "ok" : "bad"}">${v ? "&#10003;" : "&#10007;"} ${esc(k.replace(/_/g, " "))}</span>`).join("")}</div>`) : "")
        + (a ? sec("Competitor Audit Agent", `<p>${esc(a.summary)}</p>
            <div class="profile">${kv("Recommended package", a.recommended_package)}${kv("Recommended platforms", a.recommended_platforms.join(", "))}</div>
            <h3 style="margin-top:14px">Tracking gaps</h3>${list(a.tracking_gaps)}
            <h3>Opportunities</h3>${list(a.opportunities)}
            <h3>Competitors</h3>${a.competitors.map((c) => `<div style="margin-bottom:8px"><b>${esc(c.name)}</b> <span class="muted">${esc(c.positioning)}</span>
              <div class="small">Angles: ${esc(c.likely_ad_angles.join("; "))}</div><div class="small">Weak spots: ${esc(c.weaknesses_to_exploit.join("; "))}</div></div>`).join("")}
            <h3>Sources</h3>${a.sources.length ? `<ul class="tight">${a.sources.map((u) => `<li><a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(u)}</a></li>`).join("")}</ul>` : '<div class="faint">none</div>'}`)
          : sec("Competitor Audit Agent", `<div class="faint">Not run yet. It runs once the Discovery Agent has the business name, industry, offer and goal.</div>`));
    } else if (dTab === "proposal") {
      body.innerHTML = (detail.quote_summary ? sec("Quote (Pricing and Tax Engine)", `<table>${detail.quote_lines.map((l) =>
          `<tr><td>${esc(l.item)}</td><td style="text-align:right">${esc(l.amount)}</td></tr>`).join("")}
          <tr><td>Subtotal</td><td style="text-align:right">${esc(detail.quote_summary.subtotal)}</td></tr>
          <tr><td>${esc(detail.quote_summary.tax)}</td><td></td></tr>
          <tr><th>Total</th><th style="text-align:right">${esc(detail.quote_summary.total)}</th></tr></table>
          <div class="profile" style="margin-top:10px">${kv("Package", st.quote.package)}${kv("Quote id", st.quote.id)}${kv("Valid until", when(st.quote.valid_until))}
          ${kv("Payment", st.payment_status)}</div>
          ${st.payment_link ? `<p class="small">Payment link: <a href="${esc(st.payment_link)}" target="_blank" rel="noopener noreferrer">${esc(st.payment_link)}</a></p>` : ""}`)
          : sec("Quote", `<div class="faint">No quote yet. It needs the monthly ad budget and platforms.</div>`))
        + (st.proposal ? sec("Proposal Writer Agent", `<p><b>${esc(st.proposal.title)}</b></p><p>${esc(st.proposal.executive_summary)}</p>
            ${st.proposal_url ? `<a class="btn" href="${esc(st.proposal_url)}" target="_blank" rel="noopener">Open proposal document</a>` : ""}
            <h3 style="margin-top:14px">KPIs</h3>${list(st.proposal.kpis)}<h3>First 30 days</h3>${list(st.proposal.first_30_days)}`) : "");
    } else if (dTab === "creatives") {
      body.innerHTML = sec("Creative Agent", st.creatives.length ? adCards(st.creatives.slice().reverse()) : `<div class="faint">No ads yet. They are written after payment and ad account access.</div>`);
    } else if (dTab === "campaigns") {
      body.innerHTML = detail.campaigns.length ? detail.campaigns.map((c) => sec(c.draft.name, `
          <div class="profile">${kv("Status", c.status)}${kv("Platform", c.draft.platform)}${kv("Objective", c.draft.objective)}
          ${kv("Daily budget", (c.draft.daily_budget_minor / 100).toLocaleString() + " " + c.draft.currency)}
          ${kv("Platform ids", Object.entries(c.external_ids).map(([k, v]) => k + ": " + v).join(", "))}</div>
          ${c.pause_reasons.length ? `<h3 style="margin-top:12px">Paused by the kill-switch</h3>${list(c.pause_reasons)}` : ""}`)).join("")
        : sec("Campaigns", `<div class="faint">No campaigns yet.</div>`);
    } else if (dTab === "brain") {
      body.innerHTML = sec("Brand Brain (what the Creative Agent writes from)", detail.brand_brain.length
        ? `<table>${detail.brand_brain.map((m) => `<tr><td><span class="pill">${esc(m.kind)}</span></td><td>${esc(m.text)}</td></tr>`).join("")}</table>`
        : `<div class="faint">Nothing stored yet. The agent saves tone, offers, objections and proof points as the client shares them.</div>`);
    } else if (dTab === "log") {
      body.innerHTML = `<div class="card">${detail.events.length ? detail.events.map(evRow).join("") : '<div class="empty">No events.</div>'}</div>`;
    }
  }

  $("drawer").addEventListener("click", async (e) => {
    const b = e.target.closest("button"); if (!b || !openKey) return;
    const key = encodeURIComponent(openKey);
    try {
      if (b.dataset.sim) { b.disabled = true; const r = await api(`/clients/${key}/simulate`, { method: "POST", body: JSON.stringify({ event: b.dataset.sim }) });
        toast("Simulated. Stage is now " + (STAGE_LABEL[r.stage] || r.stage)); }
      else if (b.dataset.release) { const r = await api(`/clients/${key}/release`, { method: "POST" }); toast("Back with the agent: " + STAGE_LABEL[r.stage]); }
      else if (b.id === "humanSend") { const t = $("humanMsg").value.trim(); if (!t) return;
        await api(`/clients/${key}/message`, { method: "POST", body: JSON.stringify({ text: t }) }); toast("Sent to the client"); }
      else return;
      await loadClient(); refresh();
    } catch (err) { if (err.message !== "auth") toast(err.message); b.disabled = false; }
  });

  // ------------------------------------------------------------ refresh loop
  let busy = false;
  async function refresh() {
    if (busy) return; busy = true;
    try {
      await loadOverview();
      if (tab === "approvals") await loadApprovals();
      if (tab === "agents") await loadAgents();
      if (tab === "activity") await loadActivity();
      const typing = document.activeElement && document.activeElement.id === "humanMsg";
      if (openKey && !typing) await loadClient();
    } catch (e) { if (e.message !== "auth") console.warn(e); }
    finally { busy = false; }
  }
  refresh();
  setInterval(refresh, 6000);
})();
