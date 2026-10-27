import { $, esc, icon, minutes, RES_ICON } from "../core.js";
import { applyWidths, statusClass } from "../components.js";

export function mount(el) {
  el.innerHTML = `
    <div class="page-head"><div><h1>Resources</h1>
      <p class="muted small">Capacity, current use and queues for CT, Beds and Lab. Utilisation = units in use ÷ configured capacity.</p></div></div>
    <div class="impact" id="r-impact"></div>
    <div class="res-detail" id="r-cards"></div>
    <section class="card"><div class="card-head"><h2>${icon("chart-line")} Bottlenecks</h2></div>
      <div class="table-wrap"><table class="data"><thead><tr><th>Resource</th><th>Status</th>
        <th class="num">Waiting</th><th class="num">Flagged waiting</th><th class="num">Longest estimate</th></tr></thead>
        <tbody id="r-bottlenecks"></tbody></table></div></section>`;
}

export function update(app) {
  const s = app.state;
  $("#r-impact").innerHTML = `<b>${icon("info", "icon-sm")} Resource impact</b><span>${esc(s.impact)}</span>`;
  const cards = $("#r-cards");
  cards.innerHTML = s.resources.map((r) => `
    <section class="card" aria-labelledby="res-${r.code}">
      <div class="card-head"><h2 id="res-${r.code}"><span class="ico">${icon(RES_ICON[r.code], "icon-lg")}</span>${esc(r.label)}</h2>
        <span class="rstat ${statusClass(r.status)}">${esc(r.status)}</span></div>
      ${r.outage ? `<div class="notice warn">${icon("power-off")}<span>Offline until ${minutes(r.outage.remaining)} from now${
        r.outage.reason ? ` · ${esc(r.outage.reason)}` : ""}. Simulated outage; no real equipment is controlled.</span></div>` : ""}
      <div class="stat-row">
        <div class="stat"><b>${r.capacity}</b><span>Capacity</span></div>
        <div class="stat"><b>${r.in_use}</b><span>In use</span></div>
        <div class="stat"><b>${r.free}</b><span>Free</span></div>
        <div class="stat"><b>${r.waiting}</b><span>Waiting</span></div>
      </div>
      <div class="bar-row"><div class="bar ${statusClass(r.status)}" role="img" aria-label="${r.utilisation}% utilisation">
        <div data-width="${r.utilisation}"></div></div>${r.utilisation}%</div>
      <h3 class="small muted">In progress</h3>
      ${r.active_jobs.length ? `<ul class="mini-list">${r.active_jobs.map((j) => `<li><a href="#/patients/${esc(j.patient)}">${esc(j.patient)}</a>
        <span>${minutes(j.remaining)} remaining</span></li>`).join("")}</ul>` : `<p class="muted small">Nothing in progress.</p>`}
      <h3 class="small muted">Queue (estimated start)</h3>
      ${r.queue.length ? `<ul class="mini-list">${r.queue.map((q) => `<li><a href="#/patients/${esc(q.patient)}">${esc(q.patient)}</a>
        <span>${q.estimate === null ? "cannot be scheduled" : q.estimate > 0 ? `~${minutes(q.estimate)}` : "next"}</span></li>`).join("")}</ul>`
        : `<p class="muted small">No one waiting.</p>`}
      <div class="btns">
        ${app.can("sim.operate") ? `<button class="btn danger" type="button" data-action="outage" data-res="${r.code}">${icon("power-off", "icon-sm")}
          ${r.status === "Offline" ? "Extend outage" : "Simulate outage"}</button>` : ""}
        ${app.can("resource.manage") && r.status === "Offline" ? `<button class="btn" type="button" data-action="recover" data-res="${r.code}">
          ${icon("refresh-cw", "icon-sm")} End outage now</button>` : ""}
        ${app.can("settings.write") ? `<a class="btn ghost" href="#/settings">Change capacity</a>` : ""}
      </div>
    </section>`).join("");
  applyWidths(cards);
  $("#r-bottlenecks").innerHTML = s.analytics.bottlenecks.map((b) => `<tr><td>${esc(b.label)}</td>
    <td><span class="rstat ${statusClass(b.status)}">${esc(b.status)}</span></td><td class="num">${b.waiting}</td>
    <td class="num">${b.flagged}</td><td class="num">${b.max_wait_estimate ? minutes(b.max_wait_estimate) : "—"}</td></tr>`).join("");
}
