import { $, api, esc, icon, localTime } from "../core.js";

const f = { category: "", patient: "", q: "", scope: "run", page: 1, size: 25 };
let lastVersion = null, timer = null, reqId = 0, synthRun = false;

const CATS = [["", "All events"], ["patient", "Patients"], ["review", "Clinician reviews"],
  ["resource", "Resources"], ["clock", "Clock"], ["config", "Configuration"], ["security", "Security & access"]];

export function mount(el, app, params, query) {
  if (query.patient !== undefined) { f.patient = query.patient; f.page = 1; }
  lastVersion = null;
  const cats = CATS.filter(([v]) => v !== "security" || app.can("audit.global"));
  el.innerHTML = `
    <div class="page-head">
      <div><h1>Event Log <small>(append-only, newest first)</small></h1>
        <p class="muted small">Every change is recorded as an event; nothing is edited or deleted.</p></div>
      ${app.can("audit.export") ? `<a class="btn" href="/api/events/export.csv" download>${icon("download", "icon-sm")} Export CSV</a>` : ""}
    </div>
    <section class="card">
      <div class="form-row" role="search" aria-label="Filter events">
        <label class="field"><span>Category</span><select id="e-cat">${cats.map(([v, l]) =>
          `<option value="${v}" ${v === f.category ? "selected" : ""}>${l}</option>`).join("")}</select></label>
        <label class="field"><span>Patient ID</span><input id="e-patient" value="${esc(f.patient)}" placeholder="e.g. P03" maxlength="20" autocomplete="off"></label>
        <label class="field"><span>Search</span><span class="search">${icon("search")}<input id="e-q" type="search" value="${esc(f.q)}" placeholder="Type, actor, text" maxlength="80"></span></label>
        ${app.can("audit.global") ? `<label class="check"><input type="checkbox" id="e-scope" ${f.scope === "all" ? "checked" : ""}> Include sign-in and admin events</label>` : ""}
      </div>
    </section>
    <section class="card">
      <div class="table-wrap"><table class="data">
        <thead><tr><th class="num">#</th><th>Sim time</th><th>Event</th><th>Detail</th><th>Ref</th><th>Actor</th><th>Recorded</th></tr></thead>
        <tbody id="e-body"><tr><td colspan="7" class="muted">Loading…</td></tr></tbody></table></div>
      <div class="pager" id="e-pager"></div>
    </section>`;
  const reload = () => { f.page = 1; load(); };
  const debounced = () => { clearTimeout(timer); timer = setTimeout(reload, 250); };
  $("#e-cat", el).addEventListener("change", (e) => { f.category = e.target.value; reload(); });
  $("#e-patient", el).addEventListener("input", (e) => { f.patient = e.target.value.trim(); debounced(); });
  $("#e-q", el).addEventListener("input", (e) => { f.q = e.target.value.trim(); debounced(); });
  $("#e-scope", el)?.addEventListener("change", (e) => { f.scope = e.target.checked ? "all" : "run"; reload(); });
  el.addEventListener("click", (e) => {
    const b = e.target.closest("[data-epage]");
    if (b) { f.page = Number(b.dataset.epage); load(); }
  });
}

async function load() {
  const id = ++reqId;
  const qs = new URLSearchParams({ page: f.page, page_size: f.size });
  if (f.category) qs.set("category", f.category);
  if (f.patient) qs.set("patient", f.patient);
  if (f.q) qs.set("q", f.q);
  if (f.scope === "all") qs.set("scope", "all");
  const body = $("#e-body");
  if (!body) return;
  body.setAttribute("aria-busy", "true");
  try {
    const d = await api(`/api/events?${qs}`);
    if (id !== reqId || !$("#e-body")) return;
    body.innerHTML = d.items.length ? d.items.map((e) => `<tr>
      <td class="num">${e.id}</td><td>${esc(e.time ?? "—")}</td>
      <td><span class="edot ${e.color}" aria-hidden="true"></span> <strong>${esc(e.title)}</strong>
        <div class="type">${esc(e.type)}${e.synthetic && !synthRun ? ' · <span class="pill synthetic">synthetic</span>' : ""}</div></td>
      <td>${esc(e.detail)}</td>
      <td>${e.entity_type === "patient" ? `<a href="#/patients/${esc(e.entity_id)}">${esc(e.entity_id)}</a>` : esc(e.entity_id ?? "")}</td>
      <td>${esc(e.actor ?? "system")}</td><td class="muted">${esc(localTime(e.recorded_at))}</td></tr>`).join("")
      : `<tr><td colspan="7"><div class="empty">${icon("scroll-text")}<span>No events match these filters.</span></div></td></tr>`;
    const first = d.total ? (d.page - 1) * d.page_size + 1 : 0;
    $("#e-pager").innerHTML = `<span>${d.total ? `Showing ${first}–${first + d.items.length - 1} of ${d.total}` : "0 events"}</span>
      <div class="btns"><button class="btn" type="button" data-epage="${d.page - 1}" ${d.page <= 1 ? "disabled" : ""}>Newer</button>
      <span class="muted">Page ${d.page} of ${d.pages}</span>
      <button class="btn" type="button" data-epage="${d.page + 1}" ${d.page >= d.pages ? "disabled" : ""}>Older</button></div>`;
  } catch (e) {
    if (id !== reqId) return;
    body.innerHTML = `<tr><td colspan="7"><div class="empty">${icon("circle-alert")}<span>${esc(e.message)}</span>
      <button class="btn" type="button" data-action="reload-view">Try again</button></div></td></tr>`;
  } finally {
    body.removeAttribute("aria-busy");
  }
}

export function update(app) {
  synthRun = app.state.run.synthetic;
  // Refresh when new events arrive, but only on the first page so paging stays stable.
  if (lastVersion === app.state.version) return;
  const first = lastVersion === null;
  lastVersion = app.state.version;
  if (first || f.page === 1) load();
}
