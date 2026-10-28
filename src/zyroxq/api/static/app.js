// ZyroXQ dashboard. Everything rendered from the API goes through esc(): proof text and A2A notes are untrusted.
const $ = (sel) => document.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const usd = (v) => (v == null ? "—" : `$${Number(v).toFixed(2)}`);
const short = (s, n = 10) => (s && s.length > n + 4 ? `${s.slice(0, n)}…` : s || "");

let selectedCampaign = null;
let selectedDeal = null;
let status = null;

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body));
  return body;
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body ?? {}) });

const STATE_CLASS = {
  COMPLETED: "ok", FIXED_RELEASED: "ok", VERIFIED: "ok", FUNDED: "go",
  AWAITING_APPROVAL: "wait", AWAITING_CHECKOUT: "wait", PROOF_SUBMITTED: "wait", SIGNED: "go", FUNDING: "go", CART_CREATED: "go", AGREED: "go",
  NEGOTIATING: "wait", REJECTED: "bad", BLOCKED: "bad", PAYMENT_FAILED: "bad", PROOF_REJECTED: "bad", DISPUTED: "bad", EXPIRED_REFUNDED: "bad",
};
const stateBadge = (s) => `<span class="state ${STATE_CLASS[s] || ""}">${esc(s.replaceAll("_", " "))}</span>`;

// ---- header ---------------------------------------------------------------
async function loadStatus() {
  status = await api("/api/status");
  const b = [];
  b.push(`<span class="badge ${status.settlement_mode === "simulated" ? "sim" : "on"}">HSP: ${esc(status.settlement_mode)}</span>`);
  b.push(`<span class="badge ${status.escrow.simulated ? "sim" : "on"}">Escrow: ${status.escrow.simulated ? "simulated" : "on-chain"} · chain ${status.escrow.chain_id}</span>`);
  b.push(`<span class="badge ${status.llm ? "on" : ""}">LLM: ${esc(status.llm || "rules only")}</span>`);
  b.push(`<span class="badge">${esc(status.store)}</span>`);
  b.push(`<span class="badge">Approval > ${usd(status.approval_threshold_usd)}</span>`);
  if (status.clock_offset_hours) b.push(`<span class="badge sim">Clock +${status.clock_offset_hours.toFixed(0)} h</span>`);
  $("#badges").innerHTML = b.join("");
  const w = $("#warnings");
  w.hidden = !status.warnings.length;
  w.innerHTML = `<b>Demo configuration</b><ul>${status.warnings.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
}

// ---- sidebar ----------------------------------------------------------------
async function loadInfluencers() {
  const list = await api("/api/influencers");
  $("#influencers").innerHTML = list.map((i) => `
    <li><b>${esc(i.name)}</b> <span class="muted small">${esc(i.niche)} · ${Number(i.subscribers).toLocaleString()} subs</span><br>
    <a class="small" href="${esc(i.agent_url)}.well-known/agent-card.json" target="_blank" rel="noopener">Agent Card</a>
    <span class="muted small">· inbox ${i.inbox}</span></li>`).join("");
}

async function loadCampaigns() {
  const list = await api("/api/campaigns");
  if (!list.length) return;
  $("#campaigns").innerHTML = list.map((c) => `
    <li class="clickable ${c.spec.id === selectedCampaign ? "active" : ""}" data-id="${esc(c.spec.id)}">
      <b>${esc(c.spec.advertiser)}</b> ${stateBadge(c.status.toUpperCase())}<br>
      <span class="muted small">${esc(c.spec.id)} · budget ${usd(c.spec.budget)}</span></li>`).join("");
}

$("#campaigns").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-id]");
  if (!li) return;
  selectedCampaign = li.dataset.id;
  selectedDeal = null;
  refresh();
});

$("#campaign-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = Object.fromEntries(f.entries());
  body.max_creators = Number(body.max_creators);
  $("#form-error").hidden = true;
  try {
    const c = await post("/api/campaigns", body);
    selectedCampaign = c.spec.id;
    selectedDeal = null;
    refresh();
  } catch (err) {
    $("#form-error").textContent = err.message;
    $("#form-error").hidden = false;
  }
});

$("#draft-btn").addEventListener("click", async () => {
  const form = $("#campaign-form");
  try {
    const draft = await post("/api/campaigns/parse-brief", { brief: form.brief.value });
    for (const [k, v] of Object.entries(draft)) if (form[k] && k !== "brief") form[k].value = v;
  } catch (err) {
    $("#form-error").textContent = err.message;
    $("#form-error").hidden = false;
  }
});

// ---- campaign view -------------------------------------------------------------
async function loadCampaign() {
  if (!selectedCampaign) return;
  const [c, deals, events] = await Promise.all([
    api(`/api/campaigns/${selectedCampaign}`),
    api(`/api/campaigns/${selectedCampaign}/deals`),
    api(`/api/campaigns/${selectedCampaign}/events`),
  ]);
  $("#empty").hidden = true;
  $("#campaign-view").hidden = false;
  $("#cv-title").innerHTML = `${esc(c.spec.advertiser)} ${esc(c.spec.brand_handle)} ${stateBadge(c.status.toUpperCase())}`;
  $("#cv-sub").textContent = `${c.spec.id} · ${c.spec.niche} · budget $${c.spec.budget} · cap $${c.spec.cap_per_creator}/creator · max fixed $${c.spec.max_fixed} · max CPM $${c.spec.max_cpm}${c.error ? " · " + c.error : ""}`;

  const ap = $("#approval");
  if (c.pending_approval) {
    ap.hidden = false;
    ap.innerHTML = `<span><b>Human approval required</b> — ${c.pending_approval.deals.length} Payment Mandates totalling <b>${usd(c.pending_approval.total)}</b> exceed the ${usd(c.pending_approval.threshold)} threshold. Nothing is signed until you decide.</span>
      <button class="primary" data-decide="true">Approve &amp; sign</button><button data-decide="false">Decline</button>`;
  } else ap.hidden = true;

  const sum = (pred) => deals.flatMap((d) => d.payouts).filter(pred).reduce((a, p) => a + Number(p.amount), 0);
  const locked = deals.filter((d) => d.funding_tx).reduce((a, d) => a + Number(d.terms.cap), 0);
  const paid = sum((p) => p.kind !== "refund");
  const refunded = sum((p) => p.kind === "refund");
  $("#stats").innerHTML = [
    ["Deals agreed", deals.filter((d) => d.terms).length + " / " + deals.length],
    ["Locked via HSP", usd(locked)],
    ["Paid to creators", usd(paid)],
    ["Refunded", usd(refunded)],
    ["In escrow", usd(locked - paid - refunded)],
  ].map(([k, v]) => `<div class="stat"><div class="v">${esc(v)}</div><div class="k">${esc(k)}</div></div>`).join("");

  $("#deals").innerHTML = deals.map((d) => {
    const t = d.terms || {};
    const paidOut = d.payouts.filter((p) => p.kind !== "refund").reduce((a, p) => a + Number(p.amount), 0);
    return `<tr data-deal="${esc(d.id)}" class="${d.id === selectedDeal ? "sel" : ""}">
      <td><b>${esc(d.influencer_id)}</b><br><span class="muted small">${esc(d.id)}</span></td>
      <td>${stateBadge(d.state)}</td><td>${usd(t.fixed_fee)}</td><td>${usd(t.cpm)}</td><td>${usd(t.cap)}</td><td>${usd(paidOut)}</td>
      <td>${proofActions(d)}</td></tr>`;
  }).join("") || `<tr><td colspan="7" class="muted">Advertiser Agent is discovering influencer agents…</td></tr>`;

  $("#events").innerHTML = events.slice().reverse().map((e) => `
    <li><span class="t">${esc(new Date(e.at).toLocaleTimeString())}</span>
    <span class="a" title="${esc(e.actor)}">${esc(e.actor)}</span>
    <span class="d"><b>${esc(e.kind.replaceAll("_", " "))}</b> ${e.deal_id ? `<code>${esc(e.deal_id)}</code>` : ""} ${esc(describe(e.detail))}</span></li>`).join("");

  if (selectedDeal) await loadDeal(selectedDeal);
}

function describe(detail) {
  return Object.entries(detail || {})
    .filter(([, v]) => v !== null && v !== "" && !(Array.isArray(v) && !v.length))
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`)
    .join(" · ");
}

function proofActions(d) {
  if (d.state === "FUNDED" || d.state === "PROOF_REJECTED") {
    return `<div class="actions">
      <button class="small" data-post="valid" data-id="${esc(d.id)}">Post video</button>
      <button class="small" data-post="copied" data-id="${esc(d.id)}" title="Submit someone else's video">Copied URL</button>
      <button class="small" data-post="injection" data-id="${esc(d.id)}" title="Description tries to instruct the AI">Injection</button>
      <button class="small" data-post="no_disclosure" data-id="${esc(d.id)}">No #ad</button>
      <button class="small" data-real="${esc(d.id)}">Real URL…</button></div>`;
  }
  if (d.state === "VERIFIED") {
    return `<div class="actions"><button class="small" data-takedown="${esc(d.id)}">Delete post</button>
      <button class="small" data-dispute="${esc(d.id)}">Dispute</button></div>`;
  }
  if (d.state === "AWAITING_CHECKOUT" && d.payment_url) return `<a href="${esc(d.payment_url)}" target="_blank" rel="noopener">HSP checkout</a>`;
  return d.proof ? `<code>${esc(d.proof.video_id)}</code>` : "";
}

async function loadDeal(id) {
  const d = await api(`/api/deals/${id}`);
  const el = $("#deal-detail");
  el.hidden = false;
  const t = d.terms;
  const rounds = d.rounds.map((r) => {
    const c = r.reply.counter;
    return `<li>Offer r${r.offer.round}: ${usd(r.offer.fixed_fee)} + ${usd(r.offer.cpm)} CPM → <b>${esc(r.reply.kind)}</b>
      ${c ? `(${usd(c.fixed_fee)} + ${usd(c.cpm)} CPM)` : ""} <span class="muted">${esc(r.reply.reason)} · ${esc(r.reply.decided_by || "")}</span></li>`;
  }).join("");
  const checks = (d.proof?.checks || []).map((c) => `<li class="${c.passed ? "pass" : "fail"}">${c.passed ? "✓" : "✗"} ${esc(c.name)} <span class="muted small">${esc(c.detail)}</span></li>`).join("");
  const payouts = d.payouts.map((p) => `<li>${esc(p.kind)} ${usd(p.amount)} → <code>${esc(short(p.to, 10))}</code> tx <code>${esc(short(p.tx_hash, 14))}</code></li>`).join("");
  el.innerHTML = `<h2>${esc(d.influencer_id)} · ${esc(d.id)} ${stateBadge(d.state)}</h2>
    <div class="detail-grid">
      <div><h3 class="small muted">Negotiation (A2A)</h3><ol class="rounds">${rounds || "<li class='muted'>—</li>"}</ol></div>
      <div><h3 class="small muted">Signed terms</h3>${t ? `
        <div class="small">payee <code>${esc(t.payee)}</code><br>channel <code>${esc(t.channel_id)}</code><br>
        publish by ${esc(new Date(t.publish_by).toLocaleString())}<br>tags ${esc(t.required_tags.join(" "))}<br>
        signature <code>${esc(short(t.influencer_signature, 20))}</code></div>` : "<p class='muted'>Not agreed</p>"}
        ${d.policy_violations.length ? `<p class="error">Policy blocked: ${esc(d.policy_violations.join("; "))}</p>` : ""}</div>
      <div><h3 class="small muted">HSP settlement</h3><div class="small">
        cart mandate <code>${esc(d.cart_mandate_id || "—")}</code><br>payment request <code>${esc(d.payment_request_id || "—")}</code><br>
        status <b>${esc(d.payment_status || "—")}</b><br>funding tx <code>${esc(short(d.funding_tx, 18) || "—")}</code></div></div>
      <div><h3 class="small muted">Proof checks</h3><ul class="checks">${checks || "<li class='muted'>No proof yet</li>"}</ul></div>
      <div><h3 class="small muted">Payouts (verifier-attested)</h3><ul class="checks">${payouts || "<li class='muted'>None yet</li>"}</ul></div>
    </div>`;
}

document.addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  try {
    if (btn?.dataset.decide) {
      await post(`/api/campaigns/${selectedCampaign}/decision`, { approved: btn.dataset.decide === "true" });
    } else if (btn?.dataset.post) {
      e.stopPropagation();
      await post(`/api/demo/deals/${btn.dataset.id}/post`, { variant: btn.dataset.post });
      selectedDeal = btn.dataset.id;
    } else if (btn?.dataset.real) {
      e.stopPropagation();
      const url = prompt("YouTube URL of the published Short:");
      if (url) await post(`/api/deals/${btn.dataset.real}/proof`, { video_url: url });
      selectedDeal = btn.dataset.real;
    } else if (btn?.dataset.takedown) {
      e.stopPropagation();
      await post(`/api/demo/deals/${btn.dataset.takedown}/take-down`);
    } else if (btn?.dataset.dispute) {
      e.stopPropagation();
      const reason = prompt("Why are you disputing this post?");
      if (reason) await post(`/api/deals/${btn.dataset.dispute}/dispute`, { reason });
    } else if (btn?.dataset.advance) {
      await post("/api/demo/advance", { hours: Number(btn.dataset.advance) });
    } else {
      const row = e.target.closest("tr[data-deal]");
      if (!row) return;
      selectedDeal = row.dataset.deal;
    }
    refresh();
  } catch (err) {
    alert(err.message);
  }
});

$("#ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = e.target.question.value.trim();
  if (!q) return;
  $("#answer").textContent = "Thinking…";
  try {
    $("#answer").textContent = (await post("/api/assistant", { question: q })).answer;
  } catch (err) {
    $("#answer").textContent = err.message;
  }
});

async function refresh() {
  try {
    await Promise.all([loadStatus(), loadCampaigns(), loadInfluencers()]);
    await loadCampaign();
  } catch (err) {
    console.error(err);
  }
}

refresh();
setInterval(refresh, 2000);
