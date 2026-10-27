import { $, esc, icon } from "../core.js";
import { patientRow } from "../components.js";

const PAGE = 10;
const f = { q: "", level: "all", status: "active", esi: "all", res: "all", sort: "priority", page: 1 };

const SORTS = {
  priority: null, // server order: level, ESI, NEWS2, wait
  wait: (a, b) => b.wait - a.wait,
  news2: (a, b) => (b.news2 ?? -1) - (a.news2 ?? -1),
  arrival: (a, b) => a.arrival_minute - b.arrival_minute,
  esi: (a, b) => a.esi - b.esi,
};

function select(id, label, opts, value) {
  return `<label class="field"><span>${label}</span><select id="${id}">${opts.map(([v, l]) =>
    `<option value="${v}" ${v === value ? "selected" : ""}>${l}</option>`).join("")}</select></label>`;
}

export function mount(el, app) {
  el.innerHTML = `
    <div class="page-head">
      <div><h1>Patient Queue</h1><p class="muted small" id="q-summary"></p></div>
      ${app.can("patient.write") ? `<button class="btn primary" type="button" data-action="add-patient">${icon("plus", "icon-sm")} Register arrival</button>` : ""}
    </div>
    <section class="card">
      <div class="form-row" role="search" aria-label="Filter the patient queue">
        <label class="field"><span>Search</span><span class="search">${icon("search")}
          <input id="q-search" type="search" placeholder="ID or complaint" value="${esc(f.q)}" autocomplete="off"></span></label>
        ${select("q-status", "Status", [["active", "Active (waiting + in treatment)"], ["waiting", "Waiting"],
          ["in_treatment", "In treatment"], ["closed", "Admitted / discharged"], ["all", "All"]], f.status)}
        ${select("q-level", "Priority", [["all", "All"], ["urgent", "Urgent review"], ["reassess", "Reassess soon"], ["stable", "Stable"]], f.level)}
        ${select("q-esi", "ESI", [["all", "All"], ...[1, 2, 3, 4, 5].map((n) => [String(n), `ESI ${n}`])], f.esi)}
        ${select("q-res", "Needs", [["all", "Any"], ["CT", "CT"], ["BEDS", "Bed"], ["LAB", "Lab"], ["none", "None"]], f.res)}
        ${select("q-sort", "Sort by", [["priority", "Clinical priority"], ["wait", "Longest wait"],
          ["news2", "Highest NEWS2"], ["arrival", "Arrival time"], ["esi", "ESI level"]], f.sort)}
      </div>
    </section>
    <section class="card"><div class="queue-list tall" id="q-list" aria-live="polite"></div>
      <div class="pager" id="q-pager"></div></section>`;
  const bind = (id, key) => $(id, el).addEventListener(id === "#q-search" ? "input" : "change", (e) => {
    f[key] = e.target.value; f.page = 1; update(app);
  });
  bind("#q-search", "q"); bind("#q-status", "status"); bind("#q-level", "level");
  bind("#q-esi", "esi"); bind("#q-res", "res"); bind("#q-sort", "sort");
  el.addEventListener("click", (e) => {
    const b = e.target.closest("[data-page]");
    if (b) { f.page = Number(b.dataset.page); update(app); $("#q-list").scrollTop = 0; }
  });
}

function keep(p) {
  const closed = p.status === "admitted" || p.status === "discharged";
  if (f.status === "active" && closed) return false;
  if (f.status === "closed" && !closed) return false;
  if (["waiting", "in_treatment"].includes(f.status) && p.status !== f.status) return false;
  if (f.level !== "all" && p.level !== f.level) return false;
  if (f.esi !== "all" && String(p.esi) !== f.esi) return false;
  if (f.res === "none" && p.needs.length) return false;
  if (!["all", "none"].includes(f.res) && !p.needs.includes(f.res)) return false;
  const q = f.q.trim().toLowerCase();
  return !q || p.pid.toLowerCase().includes(q) || p.complaint.toLowerCase().includes(q);
}

export function update(app) {
  const all = app.state.patients;
  let rows = all.filter(keep);
  const cmp = SORTS[f.sort];
  if (cmp) rows = rows.slice().sort((a, b) => cmp(a, b) || a.pid.localeCompare(b.pid));
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  f.page = Math.min(f.page, pages);
  const start = (f.page - 1) * PAGE;
  const view = rows.slice(start, start + PAGE);
  const k = app.state.kpis;
  $("#q-summary").textContent = `${k.waiting} waiting · ${k.in_treatment} in treatment · ${k.flags} urgent · ${k.closed} closed`;
  $("#q-list").innerHTML = view.length
    ? view.map((p) => patientRow(p, { canWrite: app.can("patient.write") })).join("")
    : `<div class="empty">${icon(all.length ? "search" : "users")}<span>${all.length
      ? "No patients match these filters." : "No patients have been registered in this run."}</span></div>`;
  $("#q-pager").innerHTML = rows.length ? `
    <span>Showing ${start + 1}–${start + view.length} of ${rows.length}</span>
    <div class="btns">
      <button class="btn" type="button" data-page="${f.page - 1}" ${f.page <= 1 ? "disabled" : ""}>Previous</button>
      <span class="muted">Page ${f.page} of ${pages}</span>
      <button class="btn" type="button" data-page="${f.page + 1}" ${f.page >= pages ? "disabled" : ""}>Next</button>
    </div>` : "";
}
