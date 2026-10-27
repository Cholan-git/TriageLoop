// Core helpers shared by every view: API client, preferences, formatting,
// toasts and dialogs. No patient data is ever written to browser storage.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export const icon = (name, cls = "") =>
  `<svg class="icon ${cls}" aria-hidden="true" focusable="false"><use href="#i-${name}"/></svg>`;

export const RES_ICON = { CT: "scan", BEDS: "bed-double", LAB: "flask-conical" };
export const LEVEL_LABEL = { urgent: "URGENT REVIEW", reassess: "REASSESS SOON", stable: "STABLE", closed: "CLOSED" };
export const STATUS_LABEL = { waiting: "Waiting", in_treatment: "In treatment", admitted: "Admitted", discharged: "Discharged" };

export function minutes(n) {
  if (n === null || n === undefined) return "—";
  if (n < 60) return `${n} min`;
  const h = Math.floor(n / 60), m = n % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

export function localTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? "—" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function newKey() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  const a = new Uint8Array(16);
  crypto.getRandomValues(a);
  return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
}

// ---- API ---------------------------------------------------------------------
export class ApiError extends Error {
  constructor(message, status, code, details) {
    super(message);
    this.status = status; this.code = code; this.details = details || {};
  }
}

const CSRF = () => document.querySelector('meta[name="csrf-token"]').content;

export async function api(path, { method = "GET", body, idempotent = false, timeout = 20000 } = {}) {
  const headers = { Accept: "application/json" };
  const opts = { method, headers, credentials: "same-origin" };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-CSRF-Token"] = CSRF();
    opts.body = JSON.stringify(body ?? {});
    if (idempotent) headers["Idempotency-Key"] = typeof idempotent === "string" ? idempotent : newKey();
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  opts.signal = ctrl.signal;
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new ApiError(e.name === "AbortError" ? "The server took too long to respond." :
      "Cannot reach the TriageLoop server. Check that it is running.", 0, "network");
  } finally {
    clearTimeout(timer);
  }
  if (res.status === 401) {
    window.location.assign("/login");
    throw new ApiError("Your session has ended. Please sign in again.", 401, "unauthenticated");
  }
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON response */ }
  if (!res.ok) {
    const e = (data && data.error) || {};
    throw new ApiError(e.message || `Request failed (${res.status})`, res.status, e.code || "error", e.details);
  }
  return data;
}

// ---- preferences (non-sensitive UI state only) --------------------------------
const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* ignore */ } },
};

export const prefs = {
  theme() { const t = store.get("tl.theme"); return ["light", "dark", "system"].includes(t) ? t : "light"; },
  setTheme(t) { store.set("tl.theme", t); },
  sidebar() { return store.get("tl.sidebar") === "collapsed" ? "collapsed" : "expanded"; },
  setSidebar(v) { store.set("tl.sidebar", v); },
  panels() {
    try {
      const v = JSON.parse(store.get("tl.panels") || "{}");
      return v && typeof v === "object" && !Array.isArray(v) ? v : {};
    } catch { return {}; }
  },
  setPanel(id, collapsed) {
    const p = prefs.panels();
    if (collapsed) p[id] = true; else delete p[id];
    store.set("tl.panels", JSON.stringify(p));
  },
};

// ---- toasts ------------------------------------------------------------------------
export function toast(message, kind = "success", ms = 4500) {
  const box = $("#toasts");
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.setAttribute("role", kind === "error" ? "alert" : "status");
  el.innerHTML = `${icon(kind === "error" ? "circle-alert" : kind === "info" ? "info" : "circle-check")}
    <span>${esc(message)}</span>
    <button class="icon-btn" type="button" aria-label="Dismiss notification">${icon("x", "icon-sm")}</button>`;
  el.querySelector("button").addEventListener("click", () => el.remove());
  box.appendChild(el);
  while (box.children.length > 4) box.firstChild.remove();
  if (ms) setTimeout(() => el.remove(), ms);
}

export function showError(e) {
  toast(e instanceof ApiError ? e.message : "Something went wrong. Please try again.", "error", 8000);
}

// ---- dialogs ---------------------------------------------------------------------
// openDialog renders a form; onSubmit(formData, form) may throw ApiError to keep it open.
export function openDialog({ title, body, submitLabel = "Save", submitClass = "primary", onSubmit, wide = false }) {
  const dlg = $("#dialog");
  dlg.innerHTML = `
    <form method="dialog" novalidate>
      <div class="modal-head"><h2 id="dialog-title">${esc(title)}</h2>
        <button class="icon-btn" type="button" data-close aria-label="Close dialog">${icon("x")}</button></div>
      <div class="modal-body">
        <div class="form-error hidden" role="alert" data-error></div>
        ${body}
      </div>
      <div class="modal-foot">
        <button class="btn" type="button" data-close>Cancel</button>
        ${onSubmit ? `<button class="btn ${submitClass}" type="submit" data-submit>${esc(submitLabel)}</button>` : ""}
      </div>
    </form>`;
  dlg.style.width = "";
  if (wide) dlg.classList.add("wide"); else dlg.classList.remove("wide");
  const form = dlg.querySelector("form");
  const errBox = form.querySelector("[data-error]");
  const submit = form.querySelector("[data-submit]");
  $$("[data-close]", form).forEach((b) => b.addEventListener("click", () => dlg.close()));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!onSubmit || submit.disabled) return;
    errBox.classList.add("hidden");
    $$("[aria-invalid]", form).forEach((el) => el.removeAttribute("aria-invalid"));
    submit.disabled = true; submit.setAttribute("aria-busy", "true");
    try {
      const keep = await onSubmit(new FormData(form), form);
      if (keep !== true) dlg.close();
    } catch (e) {
      errBox.textContent = e instanceof ApiError ? e.message : "Something went wrong. Please try again.";
      errBox.classList.remove("hidden");
      if (e instanceof ApiError && e.status === 0) { /* network: leave as is */ }
      errBox.focus?.();
    } finally {
      submit.disabled = false; submit.removeAttribute("aria-busy");
    }
  });
  dlg.showModal();
  const first = form.querySelector(".modal-body input, .modal-body select, .modal-body textarea");
  (first || submit || form.querySelector("[data-close]")).focus();
  return dlg;
}

export function confirmDialog({ title, message, confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    let ok = false;
    const dlg = openDialog({
      title, body: `<p>${esc(message)}</p>`, submitLabel: confirmLabel,
      submitClass: danger ? "danger" : "primary",
      onSubmit: async () => { ok = true; },
    });
    dlg.addEventListener("close", () => resolve(ok), { once: true });
  });
}

// Busy state for a button while an async action runs; ignores repeated clicks.
export async function withBusy(btn, fn) {
  if (btn && btn.getAttribute("aria-busy") === "true") return;
  if (btn) { btn.setAttribute("aria-busy", "true"); btn.disabled = true; }
  try { return await fn(); }
  finally { if (btn) { btn.removeAttribute("aria-busy"); btn.disabled = false; } }
}
