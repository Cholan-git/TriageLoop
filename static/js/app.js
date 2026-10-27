// TriageLoop application shell: routing, data refresh, sidebar, theme and
// the click handlers for every action button (no inline handlers: strict CSP).
import { $, $$, api, esc, localTime, prefs, showError, toast, withBusy } from "./core.js";
import {
  arrivalDialog, outageDialog, reviewDialog, transitionDialog, vitalsDialog,
} from "./components.js";
import * as dashboard from "./views/dashboard.js";
import * as queue from "./views/queue.js";
import * as patient from "./views/patient.js";
import * as resources from "./views/resources.js";
import * as events from "./views/events.js";
import * as settings from "./views/settings.js";

const boot = JSON.parse($("#boot").textContent);
const POLL_MS = 8000;

const ROUTES = [
  { name: "dashboard", re: /^\/?(dashboard)?$/, view: dashboard, nav: "dashboard", title: "Dashboard" },
  { name: "queue", re: /^\/queue$/, view: queue, nav: "queue", title: "Patient Queue" },
  { name: "patient", re: /^\/patients\/([A-Za-z0-9]{1,12})$/, view: patient, nav: "queue", title: "Patient" },
  { name: "resources", re: /^\/resources$/, view: resources, nav: "resources", title: "Resources" },
  { name: "events", re: /^\/events$/, view: events, nav: "events", title: "Event Log" },
  { name: "settings", re: /^\/settings$/, view: settings, nav: "settings", title: "Settings" },
];

const app = {
  state: null,
  user: boot.user,
  perms: new Set(boot.permissions),
  route: { name: null },
  can(p) { return this.perms.has(p); },
  apply(resp) { if (resp && resp.state) setState(resp.state); },
  setTheme, setSidebar,
};

// ---- state & refresh ----------------------------------------------------------------
let lastSync = null, failing = false, refreshing = false;

function setState(s) {
  app.state = s;
  lastSync = new Date();
  renderShell();
  if (app.route.view && document.getElementById("view").dataset.mounted === app.route.name) {
    Promise.resolve(app.route.view.update(app)).catch(showError);
  }
}

async function refresh(force = false) {
  if (refreshing) return;
  refreshing = true;
  try {
    if (!force && app.state) {
      const v = await api("/api/version");
      if (v.version === app.state.version && v.run === app.state.run.id) { markOnline(); return; }
    }
    setState(await api("/api/state"));
    markOnline();
  } catch (e) {
    if (e.status === 0 || e.status >= 500) markOffline(); else showError(e);
  } finally {
    refreshing = false;
  }
}

function markOnline() {
  lastSync = new Date();
  if (failing) { failing = false; $("#conn-banner").classList.add("hidden"); toast("Connection restored", "info", 2500); }
  $("#sync-text").textContent = `Synced ${localTime(lastSync.toISOString())}`;
}

function markOffline() {
  failing = true;
  $("#conn-banner").classList.remove("hidden");
  $("#sync-text").textContent = lastSync ? `Last synced ${localTime(lastSync.toISOString())}` : "Offline";
}

setInterval(() => { if (!document.hidden) refresh(); }, POLL_MS);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

// ---- shell ----------------------------------------------------------------------------
function renderShell() {
  const s = app.state;
  $("#clock-date").textContent = s.clock.date;
  $("#clock-time").textContent = s.clock.time;
  const pill = $("#sim-pill");
  pill.classList.toggle("demo", s.run.synthetic);
  $("#sim-pill-text").textContent = s.run.synthetic ? "Demo data · synthetic" : "Simulation active";
  pill.title = `${s.run.name} (run #${s.run.id})`;
  $("#mode-text").textContent = s.run.synthetic ? "Demo scenario (synthetic)" : "Simulation mode";
  const n = s.config.scenario === "Surge" ? 10 : 5;
  $("#arrivals-label").textContent = `Add ${n} arrivals`;
  $("#arrivals-btn").setAttribute("aria-label", `Add ${n} synthetic arrivals`);
  $("#arrivals-btn").dataset.tip = `Add ${n} arrivals`;
  $("#scenario-label").textContent = `Scenario: ${s.config.scenario}`;
  $("#scenario-btn").setAttribute("aria-label", `Scenario: ${s.config.scenario}. Switch scenario`);
  $("#scenario-btn").dataset.tip = `Scenario: ${s.config.scenario}`;
  const badge = $("#nav-flags");
  badge.textContent = s.kpis.flags;
  badge.classList.toggle("hidden", !s.kpis.flags);
  badge.classList.toggle("has-flags", !!s.kpis.flags);
  badge.setAttribute("aria-label", `${s.kpis.flags} urgent patients`);
  $$(".controls [data-sim]").forEach((b) => { b.disabled = !app.can("sim.operate"); });
}

// ---- router -----------------------------------------------------------------------------
function parseHash() {
  const raw = decodeURIComponent(location.hash.replace(/^#/, "")) || "/dashboard";
  const [path, qs = ""] = raw.split("?");
  return { path, query: Object.fromEntries(new URLSearchParams(qs)) };
}

function navigate(initial = false) {
  const { path, query } = parseHash();
  let match = null, params = [];
  for (const r of ROUTES) {
    const m = path.match(r.re);
    if (m) { match = r; params = m.slice(1).filter(Boolean); break; }
  }
  const view = $("#view");
  $$("nav a").forEach((a) => {
    if (match && a.dataset.route === match.nav) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  closeDrawer();
  if (!match) {
    app.route = { name: "notfound" };
    view.dataset.mounted = "notfound";
    view.innerHTML = `<section class="card empty"><strong>Page not found</strong>
      <span>There is no page at “${esc(path)}”.</span><a class="btn primary" href="#/dashboard">Go to the dashboard</a></section>`;
    document.title = "Not found · TriageLoop";
    return;
  }
  app.route = { name: match.name, view: match.view, params, query };
  document.title = `${match.name === "patient" ? params[0].toUpperCase() : match.title} · TriageLoop`;
  match.view.mount(view, app, params, query);
  view.dataset.mounted = match.name;
  if (app.state) Promise.resolve(match.view.update(app)).catch(showError);
  window.scrollTo(0, 0);
  // Move focus to the new page for keyboard and screen-reader users, but not on
  // the first load, so Tab still reaches the "Skip to content" link first.
  if (!initial) $("#main").focus({ preventScroll: true });
}
window.addEventListener("hashchange", () => navigate(false));

// ---- theme --------------------------------------------------------------------------------
const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

function effectiveTheme(pref) {
  return pref === "dark" || (pref === "system" && media && media.matches) ? "dark" : "light";
}

function applyTheme() {
  const pref = prefs.theme();
  const eff = effectiveTheme(pref);
  document.documentElement.setAttribute("data-theme", eff);
  document.documentElement.setAttribute("data-theme-pref", pref);
  const btn = $("#theme-btn");
  const next = eff === "dark" ? "light" : "dark";
  btn.setAttribute("aria-label", `Switch to ${next} theme`);
  btn.dataset.tip = `Switch to ${next} theme`;
  btn.setAttribute("aria-pressed", String(eff === "dark"));
}

function setTheme(t) { prefs.setTheme(t); applyTheme(); }

media?.addEventListener?.("change", () => { if (prefs.theme() === "system") applyTheme(); });
$("#theme-btn").addEventListener("click", () => {
  setTheme(effectiveTheme(prefs.theme()) === "dark" ? "light" : "dark");
  $$('input[name="theme"]').forEach((r) => { r.checked = r.value === prefs.theme(); });
});

// ---- sidebar & drawer -------------------------------------------------------------------------
function setSidebar(state) {
  prefs.setSidebar(state);
  document.documentElement.setAttribute("data-sidebar", state);
  const t = $("#sidebar-toggle");
  const expanded = state === "expanded";
  t.setAttribute("aria-expanded", String(expanded));
  t.setAttribute("aria-label", expanded ? "Collapse sidebar" : "Expand sidebar");
  t.dataset.tip = expanded ? "Collapse sidebar" : "Expand sidebar";
}
$("#sidebar-toggle").addEventListener("click", () => setSidebar(prefs.sidebar() === "expanded" ? "collapsed" : "expanded"));

function openDrawer() {
  document.body.classList.add("drawer-open");
  $("#scrim").classList.remove("hidden");
  $("#menu-btn").setAttribute("aria-expanded", "true");
  $("#sidebar nav a")?.focus();
}
function closeDrawer() {
  if (!document.body.classList.contains("drawer-open")) return;
  document.body.classList.remove("drawer-open");
  $("#scrim").classList.add("hidden");
  $("#menu-btn").setAttribute("aria-expanded", "false");
}
$("#menu-btn").addEventListener("click", openDrawer);
$("#drawer-close").addEventListener("click", () => { closeDrawer(); $("#menu-btn").focus(); });
$("#scrim").addEventListener("click", closeDrawer);

// ---- user menu ----------------------------------------------------------------------------------
const userBtn = $("#user-btn"), userMenu = $("#user-menu");
function toggleMenu(open) {
  userMenu.classList.toggle("hidden", !open);
  userBtn.setAttribute("aria-expanded", String(open));
  if (open) userMenu.querySelector("a,button").focus();
}
userBtn.addEventListener("click", () => toggleMenu(userMenu.classList.contains("hidden")));
document.addEventListener("click", (e) => { if (!e.target.closest(".user-menu")) toggleMenu(false); });
userMenu.addEventListener("click", (e) => { if (e.target.closest("a")) toggleMenu(false); });

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!userMenu.classList.contains("hidden")) { toggleMenu(false); userBtn.focus(); }
  if (document.body.classList.contains("drawer-open")) { closeDrawer(); $("#menu-btn").focus(); }
});

// ---- simulation controls --------------------------------------------------------------------------
const SIM = {
  advance: () => api("/api/sim/advance", { method: "POST", body: { minutes: 10 }, idempotent: true }),
  ct_down: () => api("/api/resources/CT/outage", { method: "POST", idempotent: true,
    body: { minutes: 60, reason: "Simulated outage (sidebar control)" } }),
  arrivals: () => api("/api/sim/arrivals", { method: "POST", body: {}, idempotent: true }),
  scenario: () => api("/api/sim/scenario", { method: "POST", idempotent: true,
    body: { scenario: app.state.config.scenario === "Surge" ? "Normal" : "Surge" } }),
};

$$(".controls [data-sim]").forEach((b) => b.addEventListener("click", () => withBusy(b, async () => {
  try {
    const r = await SIM[b.dataset.sim]();
    app.apply(r);
    let msg = r.message;
    if (r.result?.drifted?.length) msg += ` · simulated vitals changed for ${r.result.drifted.map((d) => d.pid).join(", ")}`;
    toast(msg, r.duplicate || r.result?.changed === false ? "info" : "success");
  } catch (e) { showError(e); }
})));

// ---- delegated actions ----------------------------------------------------------------------------
function findPatient(pid) {
  return app.state.patients.find((p) => p.pid === pid) ||
    (patient.current() && patient.current().pid === pid ? patient.current() : null);
}

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-action]");
  if (!el) return;
  const a = el.dataset.action;
  if (a === "toggle-panel") return togglePanel(el);
  if (!app.state) return;
  const p = el.dataset.pid ? findPatient(el.dataset.pid) : null;
  const apply = (r) => app.apply(r);
  if (a === "review" && p) reviewDialog(p, apply);
  else if (a === "vitals" && p) vitalsDialog(p, apply);
  else if (a === "transition" && p) transitionDialog(p, el.dataset.to, apply);
  else if (a === "add-patient") arrivalDialog(apply);
  else if (a === "outage") outageDialog(app.state.resources.find((r) => r.code === el.dataset.res), apply);
  else if (a === "recover") withBusy(el, async () => {
    try { const r = await api(`/api/resources/${el.dataset.res}/recover`, { method: "POST", idempotent: true }); apply(r); toast(r.message); }
    catch (err) { showError(err); }
  });
  else if (a === "reload-view") navigate(false);
});

function togglePanel(btn) {
  const id = btn.dataset.panel;
  const section = document.getElementById(`panel-${id}`);
  const body = document.getElementById(`panel-${id}-body`);
  const expand = btn.getAttribute("aria-expanded") !== "true";
  btn.setAttribute("aria-expanded", String(expand));
  btn.setAttribute("aria-label", `${expand ? "Collapse" : "Expand"} section`);
  body.hidden = !expand;
  section.classList.toggle("collapsed", !expand);
  prefs.setPanel(id, !expand);
}

// ---- start ----------------------------------------------------------------------------------------
setSidebar(prefs.sidebar());
applyTheme();
navigate(true);
refresh(true);
