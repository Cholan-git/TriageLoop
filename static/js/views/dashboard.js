import { $, esc, icon, minutes, prefs } from "../core.js";
import {
  applyWidths, barChart, eventRow, lineChart, patientRow, resourceTile, whyList,
} from "../components.js";

let query = "", filter = "active", selected = null;

const FILTERS = [
  ["active", "Filter: All"], ["urgent", "Urgent review"], ["reassess", "Reassess soon"],
  ["stable", "Stable"], ["waiting", "Waiting"], ["in_treatment", "In treatment"],
];

export function panel(id, title, body, { actions = "", tag = "h2", cls = "" } = {}) {
  const collapsed = !!prefs.panels()[id];
  return `
  <section class="card panel ${cls} ${collapsed ? "collapsed" : ""}" id="panel-${id}" data-panel="${id}">
    <div class="card-head">
      <${tag} id="panel-${id}-title">
        <button class="disclosure" type="button" data-action="toggle-panel" data-panel="${id}"
          aria-expanded="${!collapsed}" aria-controls="panel-${id}-body"
          aria-label="${collapsed ? "Expand" : "Collapse"} section">${icon("chevron-down", "icon-sm")}</button>
        ${title}<span class="count-badge hidden" data-badge="${id}"></span></${tag}>
      <div class="actions">${actions}</div>
    </div>
    <div class="panel-body" id="panel-${id}-body" ${collapsed ? "hidden" : ""}>${body}</div>
  </section>`;
}

export function mount(el, app) {
  el.innerHTML = `
    <h1 class="sr-only">Dashboard</h1>
    <section class="kpis" aria-label="Key figures">
      <div class="card kpi"><div class="kpi-head"><span class="ico">${icon("users")}</span>Patients waiting</div>
        <div class="kpi-val" id="k-waiting">–</div><div class="kpi-sub" id="k-waiting-sub">In queue</div></div>
      <div class="card kpi"><div class="kpi-head"><span class="ico red">${icon("triangle-alert")}</span>Priority flags</div>
        <div class="kpi-val red" id="k-flags">–</div><div class="kpi-sub">Require review</div></div>
      <div class="card kpi"><div class="kpi-head"><span class="ico">${icon("timer")}</span>Average wait time</div>
        <div class="kpi-val" id="k-wait">–</div><div class="kpi-sub">(simulated)</div></div>
      <div class="card kpi"><div class="kpi-head"><span class="ico">${icon("server")}</span>Resources available</div>
        <div class="kpi-val" id="k-res">–</div><div class="kpi-sub">CT / Beds / Lab</div></div>
    </section>

    <div class="notice hidden" id="demo-notice">${icon("info")}<span>You are viewing the <strong>demo scenario</strong>. All patients and events in this run are synthetic.</span></div>

    <div class="grid">
      <div class="col-main">
        <section class="card" id="queue-card" aria-labelledby="queue-title">
          <div class="card-head">
            <h2 id="queue-title">Patient Queue <small>(clinician order)</small></h2>
            <div class="tools">
              <label class="search"><span class="sr-only">Search patients</span>${icon("search")}
                <input id="dash-search" type="search" placeholder="Search patient..." autocomplete="off" value="${esc(query)}"></label>
              <label><span class="sr-only">Filter patients</span>
                <select id="dash-filter">${FILTERS.map(([v, l]) =>
                  `<option value="${v}" ${v === filter ? "selected" : ""}>${l}</option>`).join("")}</select></label>
            </div>
          </div>
          <div class="queue-list" id="dash-queue" aria-live="polite"></div>
        </section>
        ${panel("analytics", `${icon("chart-line")} Operational analytics`, `<div class="charts" id="charts"></div>`)}
        ${panel("architecture", `${icon("workflow")} Architecture &amp; Data Flow`, `
          <div class="flow">
            <div class="node"><b>Event sources</b><span>Vitals, arrivals, resources</span></div><i aria-hidden="true">→</i>
            <div class="node"><b>Append-only event log</b><span>SQLite, edits blocked</span></div><i aria-hidden="true">→</i>
            <div class="node"><b>Rebuild state</b><span>Replay by timestamp</span></div><i aria-hidden="true">→</i>
            <div class="node orange"><b>Flags</b><span>Explainable rules</span></div><i aria-hidden="true">→</i>
            <div class="node green"><b>Dashboard</b><span>Clinician view</span></div>
          </div>
          <div class="flow-down"><i aria-hidden="true">↕</i><div class="node human"><b>Clinician decision</b><span>Reviews are recorded as events</span></div></div>`, { tag: "h3" })}
      </div>
      <div class="col-side">
        ${panel("resources", "Resources", `<div class="res-grid" id="dash-res"></div>
          <div class="impact"><b>${icon("info", "icon-sm")} Resource impact</b><span id="impact"></span></div>`,
          { actions: `<a href="#/resources">View all</a>` })}
        ${panel("alerts", `<span id="why-title">Priority alerts</span>`, `<div id="why"></div>`, { cls: "why" })}
        ${panel("activity", "Recent events", `<div id="recent"></div>`,
          { tag: "h3", actions: `<a href="#/events">View all</a>` })}
      </div>
    </div>`;

  $("#dash-search", el).addEventListener("input", (e) => { query = e.target.value; renderQueue(app); });
  $("#dash-filter", el).addEventListener("change", (e) => { filter = e.target.value; renderQueue(app); });
  el.addEventListener("click", (e) => {
    const chip = e.target.closest("[data-why]");
    if (chip) { selected = chip.dataset.why; renderWhy(app); }
  });
}

function matches(p) {
  if (filter === "active" && p.level === "closed") return false;
  if (["urgent", "reassess", "stable"].includes(filter) && p.level !== filter) return false;
  if (["waiting", "in_treatment"].includes(filter) && p.status !== filter) return false;
  const q = query.trim().toLowerCase();
  return !q || p.pid.toLowerCase().includes(q) || p.complaint.toLowerCase().includes(q);
}

function renderQueue(app) {
  const box = $("#dash-queue");
  if (!box) return;
  const all = app.state.patients;
  const rows = all.filter(matches);
  if (!all.length) {
    box.innerHTML = `<div class="empty">${icon("users")}<strong>No patients in the department</strong>
      <span>Register an arrival, add synthetic arrivals from the simulation controls${app.can("runs.manage") ? ", or load the demo scenario from Settings" : ""}.</span>
      ${app.can("patient.write") ? `<button class="btn primary" type="button" data-action="add-patient">${icon("plus", "icon-sm")} Register arrival</button>` : ""}</div>`;
    return;
  }
  box.innerHTML = rows.length
    ? rows.map((p) => patientRow(p, { canWrite: app.can("patient.write") })).join("")
    : `<div class="empty">${icon("search")}<span>No patients match the current search or filter.</span></div>`;
}

function renderWhy(app) {
  const flagged = app.state.patients.filter((p) => p.level === "urgent" || p.level === "reassess");
  const urgent = flagged.filter((p) => p.level === "urgent");
  const p = flagged.find((x) => x.pid === selected) || urgent[0] || flagged[0];
  const card = $("#panel-alerts");
  const title = $("#why-title");
  card.classList.toggle("calm", !urgent.length);
  if (!p) {
    title.textContent = "No patients flagged";
    $("#why").innerHTML = `<p class="muted">All active patients are within the configured targets.</p>`;
    return;
  }
  title.textContent = `Why is ${p.pid} flagged?`;
  const others = flagged.slice(0, 8);
  $("#why").innerHTML = `
    ${others.length > 1 ? `<div class="chips" role="group" aria-label="Flagged patients">${others.map((o) =>
      `<button class="chip" type="button" data-why="${esc(o.pid)}" aria-pressed="${o.pid === p.pid}">${esc(o.pid)}</button>`).join("")}</div>` : ""}
    <p class="small muted">${esc(p.pid)} · ESI ${p.esi} · ${esc(p.complaint)} · NEWS2 ${p.news2 ?? "—"} · waiting ${minutes(p.wait)}</p>
    ${whyList(p)}
    <div class="note">The system suggests; the clinician decides.</div>
    <div class="btns">
      ${app.can("patient.write") ? `<button class="btn primary" type="button" data-action="review" data-pid="${esc(p.pid)}">Review patient</button>` : ""}
      <a class="btn outline" href="#/patients/${esc(p.pid)}">View details</a>
    </div>
    <p class="hint">Rules ${esc(app.state.ruleset)}</p>`;
}

export function update(app) {
  const s = app.state, k = s.kpis;
  $("#k-waiting").textContent = k.waiting;
  $("#k-waiting-sub").textContent = k.in_treatment ? `In queue · ${k.in_treatment} in treatment` : "In queue";
  $("#k-flags").textContent = k.flags;
  $("#k-wait").textContent = k.avg_wait === null ? "—" : `${k.avg_wait} min`;
  $("#k-res").textContent = `${k.resources_free}/${k.resources_total}`;
  $("#demo-notice").classList.toggle("hidden", !s.run.synthetic);
  renderQueue(app);
  const res = $("#dash-res");
  res.innerHTML = s.resources.map(resourceTile).join("");
  applyWidths(res);
  $("#impact").textContent = s.impact;
  renderWhy(app);
  const badge = document.querySelector('[data-badge="alerts"]');
  badge.textContent = k.flags ? `${k.flags} urgent` : "";
  badge.classList.toggle("hidden", !k.flags);
  const recent = s.recent_events;
  $("#recent").innerHTML = recent.length ? recent.map((e) => eventRow(e, { markSynthetic: !s.run.synthetic })).join("")
    : `<p class="muted small">No events yet.</p>`;
  const a = s.analytics;
  $("#charts").innerHTML = `
    <div class="chart"><h4>Patients waiting over time</h4>${lineChart(
      a.queue_trend.map((p) => ({ x: p.minute, y: p.waiting, label: p.time })), { label: "Patients waiting over time" })}</div>
    <div class="chart"><h4>Current waits (minutes)</h4>${barChart(
      a.wait_distribution.map((d) => ({ label: d.label, value: d.count })), { label: "Distribution of current waiting times" })}</div>
    <div class="chart"><h4>Bottlenecks</h4><ul class="mini-list">${a.bottlenecks.map((b) => `
      <li><span>${esc(b.label)} <span class="muted">· ${esc(b.status)}</span></span>
      <span>${b.waiting} waiting${b.flagged ? ` · ${b.flagged} flagged` : ""}${b.max_wait_estimate ? ` · up to ${minutes(b.max_wait_estimate)}` : ""}</span></li>`).join("")}</ul></div>`;
}
