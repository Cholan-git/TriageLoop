import { $, $$, api, confirmDialog, esc, icon, prefs, showError, toast, withBusy } from "../core.js";

const RES = [["CT", "CT"], ["BEDS", "Beds"], ["LAB", "Lab"]];
let formVersion = null;

export function mount(el, app) {
  formVersion = null;
  const admin = app.can("settings.write");
  const theme = prefs.theme();
  el.innerHTML = `
    <div class="page-head"><h1>Settings</h1></div>
    <div class="settings-grid">
      <div class="stack">
        <section class="card stack" aria-labelledby="s-appearance">
          <h2 id="s-appearance">Appearance</h2>
          <div><div class="field"><span id="theme-label">Theme</span></div>
            <div class="seg" role="radiogroup" aria-labelledby="theme-label">
              ${[["light", "sun", "Light"], ["dark", "moon", "Dark"], ["system", "monitor", "System"]].map(([v, i, l]) =>
                `<label><input type="radio" name="theme" value="${v}" ${theme === v ? "checked" : ""}>${icon(i, "icon-sm")} ${l}</label>`).join("")}
            </div>
            <p class="hint">System follows your operating system's setting. Stored only in this browser.</p></div>
          <div class="btns"><button class="btn" type="button" id="reset-layout">Reset dashboard layout</button></div>
        </section>

        <section class="card stack" aria-labelledby="s-run">
          <h2 id="s-run">Simulation run</h2>
          <p class="muted small">Each run is a separate simulation session. Starting a new run never deletes earlier runs; their events stay in the audit trail.</p>
          <div id="run-current"></div>
          ${app.can("runs.manage") ? `
          <form class="form-row" id="new-run">
            <label class="field grow"><span>New run name</span><input name="name" maxlength="80" required placeholder="e.g. Afternoon shift"></label>
            <button class="btn" type="submit">${icon("plus", "icon-sm")} Start empty run</button>
          </form>
          <div class="notice">${icon("info")}<span>The demo scenario creates a new run filled with <strong>synthetic</strong> patients and events, clearly labelled throughout the app.</span></div>
          <div class="btns"><button class="btn primary" type="button" id="load-demo">Load demo scenario</button></div>` : ""}
          <div id="run-list"></div>
        </section>
      </div>

      <div class="stack">
        <section class="card stack" aria-labelledby="s-ops">
          <h2 id="s-ops">Operational settings</h2>
          ${admin ? "" : `<div class="notice">${icon("info")}<span>Only administrators can change these settings.</span></div>`}
          <form id="ops" class="stack" novalidate>
            <fieldset ${admin ? "" : "disabled"}><legend>Capacity (units)</legend><div class="form-grid">
              ${RES.map(([c, l]) => `<label class="field"><span>${l}</span><input type="number" name="capacity.${c}" min="0" max="50" required></label>`).join("")}</div></fieldset>
            <fieldset ${admin ? "" : "disabled"}><legend>Time per patient (minutes)</legend><div class="form-grid">
              ${RES.map(([c, l]) => `<label class="field"><span>${l}</span><input type="number" name="duration.${c}" min="1" max="600" required></label>`).join("")}</div></fieldset>
            <fieldset ${admin ? "" : "disabled"}><legend>Reassessment target by ESI (minutes)</legend><div class="form-grid">
              ${[1, 2, 3, 4, 5].map((n) => `<label class="field"><span>ESI ${n}</span><input type="number" name="reassess_target.${n}" min="0" max="600" required></label>`).join("")}</div></fieldset>
            <fieldset ${admin ? "" : "disabled"}><legend>Flag thresholds</legend><div class="form-grid">
              <label class="field"><span>NEWS2 rise to flag</span><input type="number" name="news2_rise" min="1" max="20" required></label>
              <label class="field"><span>Rise is urgent at NEWS2 ≥</span><input type="number" name="news2_urgent_rise" min="1" max="20" required></label>
              <label class="field"><span>Urgent at NEWS2 ≥</span><input type="number" name="news2_urgent" min="1" max="20" required></label>
              <label class="field"><span>Vitals stale after (min)</span><input type="number" name="vitals_stale_min" min="5" max="1440" required></label>
            </div></fieldset>
            <fieldset ${admin ? "" : "disabled"}><legend>Simulation</legend>
              <label class="check"><input type="checkbox" name="vitals_drift"> Advance adds simulated vitals changes (seeded, labelled synthetic)</label></fieldset>
            ${admin ? `<div class="btns"><button class="btn primary" type="submit">Save settings</button>
              <button class="btn" type="button" id="ops-reset">Discard changes</button></div>` : ""}
            <p class="hint">Changes are recorded in the event log and apply from the current simulated time.</p>
          </form>
        </section>
        ${app.can("users.manage") ? `
        <section class="card stack" aria-labelledby="s-users">
          <h2 id="s-users">Users</h2>
          <div class="table-wrap"><table class="data"><thead><tr><th>User</th><th>Role</th><th>Created</th></tr></thead><tbody id="users"></tbody></table></div>
          <form id="new-user" class="form-row" novalidate>
            <label class="field"><span>Username</span><input name="username" minlength="3" maxlength="40" required autocomplete="off"></label>
            <label class="field"><span>Temporary password</span><input name="password" type="password" minlength="10" required autocomplete="new-password"></label>
            <label class="field"><span>Role</span><select name="role"><option value="clinician">Clinician</option><option value="admin">Administrator</option></select></label>
            <button class="btn" type="submit">${icon("user-plus", "icon-sm")} Add user</button>
          </form>
        </section>` : ""}
        <section class="card stack" aria-labelledby="s-about">
          <h2 id="s-about">About</h2>
          <p class="small">TriageLoop is a simulation for training and demonstration. NEWS2 follows the RCP 2017 chart (SpO₂ scale 1). Flags are advisory and use ruleset <strong>${esc(app.state.ruleset)}</strong>. It is not a medical device, is not clinically validated, and must not be used for real patient care.</p>
        </section>
      </div>
    </div>`;

  $$('input[name="theme"]', el).forEach((r) => r.addEventListener("change", () => app.setTheme(r.value)));
  $("#reset-layout", el).addEventListener("click", () => {
    try { localStorage.removeItem("tl.panels"); } catch { /* ignore */ }
    app.setSidebar("expanded");
    toast("Dashboard layout reset");
  });
  $("#ops", el).addEventListener("submit", (e) => { e.preventDefault(); saveOps(app, e.submitter); });
  $("#ops-reset", el)?.addEventListener("click", () => { formVersion = null; fillOps(app, true); });
  $("#new-run", el)?.addEventListener("submit", (e) => {
    e.preventDefault();
    const name = new FormData(e.target).get("name");
    withBusy(e.submitter, async () => {
      try {
        const r = await api("/api/runs", { method: "POST", body: { name } });
        e.target.reset(); app.apply(r); toast(r.message); loadRuns(app);
      } catch (err) { showError(err); }
    });
  });
  $("#load-demo", el)?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const ok = await confirmDialog({ title: "Load demo scenario?", confirmLabel: "Load demo",
      message: "This starts a new run containing synthetic demo patients. Your current run is kept and can be reopened from this page." });
    if (!ok) return;
    withBusy(btn, async () => {
      try { const r = await api("/api/runs/demo", { method: "POST" }); app.apply(r); toast(r.message); loadRuns(app); location.hash = "#/dashboard"; }
      catch (err) { showError(err); }
    });
  });
  el.addEventListener("click", (e) => {
    const b = e.target.closest("[data-activate]");
    if (!b) return;
    withBusy(b, async () => {
      try { const r = await api(`/api/runs/${b.dataset.activate}/activate`, { method: "POST" }); app.apply(r); toast(r.message); loadRuns(app); }
      catch (err) { showError(err); }
    });
  });
  $("#new-user", el)?.addEventListener("submit", (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    withBusy(e.submitter, async () => {
      try {
        const r = await api("/api/users", { method: "POST", body: Object.fromEntries(fd) });
        e.target.reset(); toast(r.message); renderUsers(r.items);
      } catch (err) { showError(err); }
    });
  });
  loadRuns(app);
  if (app.can("users.manage")) api("/api/users").then((d) => renderUsers(d.items)).catch(showError);
}

function renderUsers(items) {
  const body = $("#users");
  if (body) body.innerHTML = items.map((u) => `<tr><td>${esc(u.username)}</td>
    <td>${u.role === "admin" ? "Administrator" : "Clinician"}</td><td class="muted">${esc(u.created_at.slice(0, 10))}</td></tr>`).join("");
}

async function loadRuns(app) {
  try {
    const d = await api("/api/runs");
    const list = $("#run-list");
    if (!list) return;
    list.innerHTML = `<table class="data"><thead><tr><th>Run</th><th class="num">Events</th><th></th></tr></thead><tbody>${d.items.map((r) => `
      <tr><td><strong>${esc(r.name)}</strong> <span class="muted">#${r.id}</span>${r.synthetic ? ' <span class="pill synthetic">demo · synthetic</span>' : ""}</td>
      <td class="num">${r.events}</td><td>${r.id === d.active ? '<span class="pill status">Active</span>'
        : app.can("runs.manage") ? `<button class="btn" type="button" data-activate="${r.id}">Open</button>` : ""}</td></tr>`).join("")}</tbody></table>`;
  } catch (e) { showError(e); }
}

function fillOps(app, force = false) {
  const form = $("#ops");
  if (!form) return;
  const dirty = form.dataset.dirty === "1";
  if (dirty && !force) return;               // never overwrite unsaved edits
  if (!force && formVersion === app.state.version) return;
  formVersion = app.state.version;
  const c = app.state.config;
  for (const [key, val] of Object.entries(c)) {
    if (val && typeof val === "object") {
      for (const [k, v] of Object.entries(val)) { const i = form.elements[`${key}.${k}`]; if (i) i.value = v; }
    } else {
      const i = form.elements[key];
      if (i && i.type === "checkbox") i.checked = !!val; else if (i) i.value = val;
    }
  }
  form.dataset.dirty = "0";
  form.oninput = () => { form.dataset.dirty = "1"; };
}

async function saveOps(app, btn) {
  const form = $("#ops");
  $$("[aria-invalid]", form).forEach((i) => i.removeAttribute("aria-invalid"));
  const bad = $$("input[type=number]", form).find((i) => !i.checkValidity());
  if (bad) { bad.setAttribute("aria-invalid", "true"); bad.focus(); toast(`${bad.closest("label").innerText.trim()}: enter a value between ${bad.min} and ${bad.max}.`, "error"); return; }
  const changes = {};
  for (const i of $$("input", form)) {
    if (i.type === "checkbox") { changes[i.name] = i.checked; continue; }
    const [key, sub] = i.name.split(".");
    if (sub) { (changes[key] ||= {})[sub] = Number(i.value); } else changes[key] = Number(i.value);
  }
  await withBusy(btn, async () => {
    try {
      const r = await api("/api/settings", { method: "PUT", body: { changes } });
      form.dataset.dirty = "0"; formVersion = null;
      app.apply(r); toast(r.message, r.result.changed === false ? "info" : "success");
    } catch (e) { showError(e); }
  });
}

export function update(app) {
  const s = app.state;
  const cur = $("#run-current");
  if (cur) cur.innerHTML = `<div class="stat"><span>Active run</span><b>${esc(s.run.name)} <span class="muted small">#${s.run.id}</span>
    ${s.run.synthetic ? '<span class="pill synthetic">demo · synthetic</span>' : ""}</b></div>`;
  fillOps(app);
}
