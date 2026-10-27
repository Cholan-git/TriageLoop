import { $, api, ApiError, esc, icon, minutes, LEVEL_LABEL, STATUS_LABEL } from "../core.js";
import { whyList } from "../components.js";

let pid = null, loadedVersion = null, data = null;

export function mount(el, app, params) {
  pid = params[0].toUpperCase();
  loadedVersion = null; data = null;
  el.innerHTML = `<div class="card loading-card" role="status">Loading ${esc(pid)}…</div>`;
}

const TRANSITION_LABEL = { in_treatment: "Move to treatment", admitted: "Admit", discharged: "Discharge" };

function vitalsTable(obs) {
  if (!obs.length) return `<p class="muted">No observations recorded.</p>`;
  const cell = (v) => (v === undefined ? "—" : typeof v === "boolean" ? (v ? "Yes" : "No") : esc(v));
  return `<div class="table-wrap"><table class="data">
    <caption class="sr-only">Observations, newest first</caption>
    <thead><tr><th>Time</th><th class="num">RR</th><th class="num">SpO₂</th><th>O₂</th><th class="num">SBP</th>
      <th class="num">HR</th><th>AVPU</th><th class="num">Temp</th><th class="num">NEWS2</th><th>Source</th></tr></thead>
    <tbody>${obs.map((o) => `<tr><td>${esc(o.time)}</td><td class="num">${cell(o.vitals.rr)}</td>
      <td class="num">${cell(o.vitals.spo2)}</td><td>${cell(o.vitals.on_oxygen)}</td><td class="num">${cell(o.vitals.sbp)}</td>
      <td class="num">${cell(o.vitals.hr)}</td><td>${cell(o.vitals.avpu)}</td><td class="num">${cell(o.vitals.temp)}</td>
      <td class="num"><strong>${o.news2}</strong>${o.missing.length ? '<span class="muted small"> (partial)</span>' : ""}</td>
      <td>${o.source === "simulated" ? '<span class="pill synthetic">simulated</span>' : esc(o.source)}</td></tr>`).join("")}</tbody>
  </table></div>`;
}

function render(app) {
  const el = $("#view");
  const p = data;
  const write = app.can("patient.write") && p.level !== "closed";
  el.innerHTML = `
    <div class="page-head">
      <div><div class="crumbs"><a href="#/queue">Patient Queue</a> / ${esc(p.pid)}</div>
        <h1>${esc(p.pid)} · ESI ${p.esi} · ${esc(p.complaint)}
          <span class="pill ${p.level}">${LEVEL_LABEL[p.level]}</span>
          ${p.synthetic ? '<span class="pill synthetic">synthetic</span>' : ""}</h1></div>
      <div class="btns">
        ${write ? `<button class="btn primary" type="button" data-action="review" data-pid="${esc(p.pid)}">Review patient</button>
          <button class="btn" type="button" data-action="vitals" data-pid="${esc(p.pid)}">${icon("activity", "icon-sm")} Record vitals</button>` : ""}
        ${app.can("patient.write") ? p.allowed_transitions.map((t) => `<button class="btn ${t === "discharged" ? "danger" : ""}" type="button"
          data-action="transition" data-pid="${esc(p.pid)}" data-to="${t}">${TRANSITION_LABEL[t]}</button>`).join("") : ""}
      </div>
    </div>
    <section class="card">
      <div class="facts">
        <div class="stat"><span>Status</span><b>${esc(STATUS_LABEL[p.status])}</b></div>
        <div class="stat"><span>NEWS2 (baseline)</span><b>${p.news2 ?? "—"} <span class="muted small">(${p.baseline ?? "—"})</span></b></div>
        <div class="stat"><span>Arrived</span><b>${esc(p.arrival)}</b></div>
        <div class="stat"><span>Waiting</span><b>${minutes(p.wait)}</b></div>
        <div class="stat"><span>Since assessment</span><b>${minutes(p.since_review)}</b></div>
        <div class="stat"><span>Reviews</span><b>${p.review_count}</b></div>
      </div>
    </section>
    <div class="detail-grid">
      <div class="col-main">
        <section class="card why ${p.level === "urgent" ? "" : "calm"}">
          <div class="card-head"><h2>${p.reasons.length ? `Why is ${esc(p.pid)} flagged?` : "Flags"}</h2>
            <span class="muted small">Rules ${esc(app.state.ruleset)}</span></div>
          ${whyList(p)}
          ${p.urgent_rules.length ? `<p class="small">Urgent because of: <strong>${p.urgent_rules.map(esc).join(", ")}</strong></p>` : ""}
          <div class="note">The system suggests; the clinician decides.</div>
        </section>
        <section class="card"><div class="card-head"><h2>Observations</h2></div>${vitalsTable(p.observations)}</section>
        <section class="card"><div class="card-head"><h2>Resources</h2></div>
          ${p.jobs.length ? `<ul class="mini-list">${p.jobs.map((j) => `<li><span>${esc(j.label)}</span><span>${
            j.state === "active" ? `In progress · ${minutes(j.remaining)} left`
            : j.state === "requested" ? (j.estimate === null ? "Cannot be scheduled" : j.estimate > 0 ? `Queued · est. ${minutes(j.estimate)}` : "Starting")
            : j.state === "done" ? "Completed" : "Cancelled"}${j.interruptions ? ` · interrupted ${j.interruptions}×` : ""}</span></li>`).join("")}</ul>`
            : `<p class="muted">No resources requested.</p>`}
        </section>
      </div>
      <section class="card">
        <div class="card-head"><h2>${icon("history")} Timeline</h2><a href="#/events?patient=${encodeURIComponent(p.pid)}">Open in Event Log</a></div>
        <ol class="timeline">${p.timeline.map((e) => `<li><span class="edot ${e.color}" aria-hidden="true"></span>
          <span class="t">${esc(e.time)}</span><strong>${esc(e.title)}</strong>
          ${e.synthetic && !app.state.run.synthetic ? '<span class="pill synthetic">synthetic</span>' : ""}
          <div class="muted">${esc(e.detail)}${e.actor ? ` · ${esc(e.actor)}` : ""}</div></li>`).join("")}</ol>
      </section>
    </div>`;
}

export async function update(app) {
  if (loadedVersion === app.state.version && data) return;
  const version = app.state.version;
  try {
    data = await api(`/api/patients/${encodeURIComponent(pid)}`);
    loadedVersion = version;
    if (app.route.name === "patient") render(app);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      $("#view").innerHTML = `<section class="card empty">${icon("search")}<strong>Patient ${esc(pid)} not found</strong>
        <span>They may belong to a different simulation run.</span><a class="btn primary" href="#/queue">Back to the queue</a></section>`;
    } else {
      $("#view").innerHTML = `<section class="card empty">${icon("circle-alert")}<strong>Could not load ${esc(pid)}</strong>
        <span>${esc(e.message)}</span><button class="btn" type="button" data-action="reload-view">Try again</button></section>`;
    }
  }
}

export function current() { return data; }
