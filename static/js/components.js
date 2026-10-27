// Shared renderers and action dialogs. Every number shown here comes from the
// server snapshot; the browser never recomputes scores, flags or metrics.
import {
  api, ApiError, esc, icon, minutes, openDialog, RES_ICON, LEVEL_LABEL, STATUS_LABEL, toast,
} from "./core.js";

// ---- patient row (dashboard + queue) -----------------------------------------------
export function reasonsList(p, limit = 4) {
  if (p.level === "closed") return `<ul class="reasons"><li class="reason note">${esc(STATUS_LABEL[p.status])}</li></ul>`;
  if (!p.reasons.length) {
    return `<ul class="reasons"><li class="reason ok">No flags</li>
      <li class="reason note">Vitals within thresholds · Routine monitoring</li></ul>`;
  }
  const shown = p.reasons.slice(0, limit);
  const more = p.reasons.length - shown.length;
  return `<ul class="reasons">${shown.map((r) =>
    `<li class="reason ${r.kind}">${esc(r.text)}</li>`).join("")}${more > 0
    ? `<li class="reason note">+${more} more · see details</li>` : ""}</ul>`;
}

export function patientRow(p, { canWrite }) {
  const status = p.status !== "waiting" ? `<span class="pill status">${esc(STATUS_LABEL[p.status])}</span>` : "";
  return `
  <article class="prow ${p.level}" aria-label="${esc(p.pid)}, ${esc(LEVEL_LABEL[p.level])}">
    <div>
      <div class="ptitle"><span class="sev" aria-hidden="true"></span>
        <a href="#/patients/${esc(p.pid)}">${esc(p.pid)} · ESI Level ${p.esi} · ${esc(p.complaint)}</a>
        ${status}<span class="pill ${p.level}">${LEVEL_LABEL[p.level]}</span></div>
      ${reasonsList(p)}
    </div>
    <div class="news">NEWS2: ${p.news2 ?? "—"}</div>
    <div class="waitbox">
      <small>${icon("clock", "icon-sm")} Wait time</small>
      <div class="w">${minutes(p.wait)}</div>
      <div class="btns">
        ${canWrite && p.level !== "closed"
          ? `<button class="btn primary" type="button" data-action="review" data-pid="${esc(p.pid)}">Review patient</button>` : ""}
        <a class="btn" href="#/patients/${esc(p.pid)}">View details</a>
      </div>
    </div>
  </article>`;
}

// ---- explanation ------------------------------------------------------------------
export function whyList(p) {
  if (!p.reasons.length) return `<p class="muted">No active flags for ${esc(p.pid)}.</p>`;
  return `<ul class="why-list">${p.reasons.map((r) => `
    <li class="${r.kind}"><div><strong>${esc(r.text)}</strong><span class="rule-id">${esc(r.rule)}</span>
      <div class="muted">${esc(r.why)}</div></div></li>`).join("")}</ul>`;
}

// ---- resources ------------------------------------------------------------------------
export function statusClass(status) { return status.replace(/\s+/g, "-"); }

export function resourceTile(r) {
  const extra = r.outage ? ` (${r.outage.remaining}m)` : "";
  return `
    <div class="res">
      <span class="ico">${icon(RES_ICON[r.code])}</span>
      <div class="rname">${esc(r.label)}</div>
      <div class="rstat ${statusClass(r.status)}">${esc(r.status)}${extra}</div>
      <div class="rwait">${r.waiting} waiting · ${r.in_use}/${r.capacity} in use</div>
      <div class="bar-row"><div class="bar ${statusClass(r.status)}" role="img"
        aria-label="${r.utilisation}% utilisation"><div data-width="${r.utilisation}"></div></div>${r.utilisation}%</div>
    </div>`;
}

// Bar widths are set through the CSSOM, which the Content-Security-Policy allows.
export function applyWidths(root) {
  root.querySelectorAll("[data-width]").forEach((el) => {
    el.style.width = `${Math.max(0, Math.min(100, Number(el.dataset.width) || 0))}%`;
  });
}

// ---- events ------------------------------------------------------------------------------
export function eventRow(e, { markSynthetic = true } = {}) {
  return `<div class="ev"><span class="edot ${e.color}" aria-hidden="true"></span>
    <span class="etime">${esc(e.time ?? "")}</span>
    <div><div class="etitle">${esc(e.title)}${markSynthetic && e.synthetic ? ' <span class="pill synthetic">synthetic</span>' : ""}</div>
    <div class="esub">${esc(e.detail)}</div></div>
    <span class="etag">${e.entity_type === "patient" ? `<a href="#/patients/${esc(e.tag)}">${esc(e.tag)}</a>` : esc(e.tag)}</span></div>`;
}

// ---- charts (inline SVG, themed through CSS) ------------------------------------------------
export function lineChart(points, { label }) {
  if (points.length < 2) return `<p class="muted small">Not enough history yet.</p>`;
  const W = 320, H = 130, L = 26, B = 20, T = 8, R = 8;
  const maxY = Math.max(1, ...points.map((p) => p.y));
  const minX = points[0].x, maxX = Math.max(points[points.length - 1].x, minX + 1);
  const x = (v) => L + ((v - minX) / (maxX - minX)) * (W - L - R);
  const y = (v) => T + (1 - v / maxY) * (H - T - B);
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(p.x).toFixed(1)},${y(p.y).toFixed(1)}`).join("");
  const area = `${path}L${x(points[points.length - 1].x).toFixed(1)},${y(0)}L${x(points[0].x).toFixed(1)},${y(0)}Z`;
  const ticks = [0, Math.ceil(maxY / 2), maxY];
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">
    ${ticks.map((t) => `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/>
      <text x="${L - 6}" y="${y(t) + 3}" text-anchor="end">${t}</text>`).join("")}
    <path class="area" d="${area}"/><path class="line" d="${path}"/>
    <text x="${L}" y="${H - 4}">${esc(points[0].label)}</text>
    <text x="${W - R}" y="${H - 4}" text-anchor="end">${esc(points[points.length - 1].label)}</text>
  </svg>`;
}

export function barChart(items, { label }) {
  const W = 320, H = 130, B = 20, T = 14, L = 6;
  const max = Math.max(1, ...items.map((i) => i.value));
  const bw = (W - L * 2) / items.length;
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">
    <line class="grid-line" x1="0" x2="${W}" y1="${H - B}" y2="${H - B}"/>
    ${items.map((it, i) => {
      const h = (it.value / max) * (H - B - T);
      const bx = L + i * bw + bw * 0.18, w = bw * 0.64, by = H - B - h;
      return `<rect class="barr" x="${bx.toFixed(1)}" y="${by.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" rx="2"/>
        <text class="val" x="${(bx + w / 2).toFixed(1)}" y="${(by - 3).toFixed(1)}" text-anchor="middle">${it.value}</text>
        <text x="${(bx + w / 2).toFixed(1)}" y="${H - 6}" text-anchor="middle">${esc(it.label)}</text>`;
    }).join("")}
  </svg>`;
}

// ---- action dialogs -------------------------------------------------------------------------
const VITAL_FIELDS = `
  <div class="form-grid">
    <label class="field"><span>Resp. rate (/min)</span><input name="rr" type="number" inputmode="numeric" min="0" max="80"></label>
    <label class="field"><span>SpO₂ (%)</span><input name="spo2" type="number" inputmode="numeric" min="50" max="100"></label>
    <label class="field"><span>Systolic BP (mmHg)</span><input name="sbp" type="number" inputmode="numeric" min="40" max="300"></label>
    <label class="field"><span>Heart rate (/min)</span><input name="hr" type="number" inputmode="numeric" min="20" max="250"></label>
    <label class="field"><span>Temperature (°C)</span><input name="temp" type="number" inputmode="decimal" step="0.1" min="30" max="45"></label>
    <label class="field"><span>Consciousness (AVPU)</span>
      <select name="avpu"><option value="">Not recorded</option><option value="A">Alert</option>
      <option value="C">New confusion</option><option value="V">Voice</option><option value="P">Pain</option><option value="U">Unresponsive</option></select></label>
  </div>
  <label class="check"><input type="checkbox" name="on_oxygen"> Receiving supplemental oxygen</label>`;

function readVitals(fd) {
  const v = {};
  for (const k of ["rr", "spo2", "sbp", "hr", "temp"]) {
    const raw = (fd.get(k) || "").toString().trim();
    if (raw !== "") v[k] = Number(raw);
  }
  if (fd.get("avpu")) v.avpu = fd.get("avpu");
  v.on_oxygen = fd.get("on_oxygen") === "on";
  return v;
}

export function reviewDialog(p, apply) {
  const items = p.reasons.length
    ? `<ul class="why-list">${p.reasons.map((r) => `<li class="${r.kind}"><div><strong>${esc(r.text)}</strong>
        <span class="rule-id">${esc(r.rule)}</span></div></li>`).join("")}</ul>`
    : `<p class="muted">No active flags.</p>`;
  const esiOpts = [1, 2, 3, 4, 5].map((n) => `<option value="${n}" ${n === p.esi ? "selected" : ""}>ESI ${n}${n === p.esi ? " (current)" : ""}</option>`).join("");
  openDialog({
    title: `Review ${p.pid} · ${p.complaint}`,
    submitLabel: "Record review",
    body: `
      <p class="muted">NEWS2 ${p.news2 ?? "—"} (baseline ${p.baseline ?? "—"}) · waiting ${minutes(p.wait)} · ${minutes(p.since_review)} since last assessment</p>
      <div><strong class="small">Flags you are acknowledging</strong>${items}</div>
      <label class="field"><span>Clinical note</span><textarea name="note" maxlength="300" placeholder="Reviewed by clinician"></textarea></label>
      <label class="field"><span>ESI level</span><select name="esi">${esiOpts}</select></label>
      <label class="check hidden" data-esi-confirm><input type="checkbox" name="confirm_esi"> I confirm changing the ESI level</label>
      <p class="hint">Recording a review resets this patient's NEWS2 baseline and reassessment timer. The system suggests; the clinician decides.</p>`,
    onSubmit: async (fd, form) => {
      const esi = Number(fd.get("esi"));
      const body = { version: p.version, note: (fd.get("note") || "").toString() };
      if (esi !== p.esi) {
        if (fd.get("confirm_esi") !== "on") {
          form.querySelector('[name="confirm_esi"]').setAttribute("aria-invalid", "true");
          throw new ApiError("Tick the confirmation box to change the ESI level.", 400, "confirm");
        }
        body.esi = esi;
      }
      const r = await api(`/api/patients/${encodeURIComponent(p.pid)}/review`, { method: "POST", body, idempotent: true });
      apply(r); toast(r.message);
    },
  });
  const dlg = document.getElementById("dialog");
  const sel = dlg.querySelector('[name="esi"]');
  sel.addEventListener("change", () => dlg.querySelector("[data-esi-confirm]")
    .classList.toggle("hidden", Number(sel.value) === p.esi));
}

export function vitalsDialog(p, apply) {
  openDialog({
    title: `Record vitals · ${p.pid}`,
    submitLabel: "Save observations",
    body: `${VITAL_FIELDS}<p class="hint">NEWS2 is calculated by the server from these observations. Leave fields blank if not measured.</p>`,
    onSubmit: async (fd) => {
      const r = await api(`/api/patients/${encodeURIComponent(p.pid)}/vitals`,
        { method: "POST", body: { version: p.version, vitals: readVitals(fd) }, idempotent: true });
      apply(r); toast(r.message);
    },
  });
}

export function transitionDialog(p, to, apply) {
  const label = STATUS_LABEL[to];
  openDialog({
    title: `${p.pid}: ${STATUS_LABEL[p.status]} → ${label}`,
    submitLabel: `Confirm: ${label}`,
    body: `<p>This changes the patient's status${to === "admitted" || to === "discharged"
      ? " and releases any resources reserved for them" : ""}.</p>
      <label class="field"><span>Note (optional)</span><textarea name="note" maxlength="300"></textarea></label>`,
    onSubmit: async (fd) => {
      const r = await api(`/api/patients/${encodeURIComponent(p.pid)}/transition`,
        { method: "POST", body: { to, version: p.version, note: (fd.get("note") || "").toString() }, idempotent: true });
      apply(r); toast(r.message);
    },
  });
}

export function arrivalDialog(apply) {
  openDialog({
    title: "Register patient arrival",
    submitLabel: "Add to queue",
    body: `
      <div class="form-row">
        <label class="field"><span>ESI level</span><select name="esi" required>
          <option value="">Select…</option>${[1, 2, 3, 4, 5].map((n) => `<option value="${n}">ESI ${n}</option>`).join("")}</select></label>
        <label class="field grow"><span>Presenting complaint</span><input name="complaint" required minlength="2" maxlength="80" autocomplete="off"></label>
      </div>
      <fieldset><legend>Resources needed</legend>
        <div class="form-row">
          <label class="check"><input type="checkbox" name="needs" value="CT"> CT</label>
          <label class="check"><input type="checkbox" name="needs" value="BEDS"> Bed</label>
          <label class="check"><input type="checkbox" name="needs" value="LAB"> Lab</label>
        </div></fieldset>
      <fieldset><legend>Arrival observations</legend>${VITAL_FIELDS}</fieldset>
      <p class="hint">A unique patient id is assigned by the server.</p>`,
    onSubmit: async (fd, form) => {
      const esiEl = form.querySelector('[name="esi"]'), cEl = form.querySelector('[name="complaint"]');
      if (!fd.get("esi")) { esiEl.setAttribute("aria-invalid", "true"); esiEl.focus(); throw new ApiError("Choose an ESI level.", 400, "invalid"); }
      if ((fd.get("complaint") || "").toString().trim().length < 2) {
        cEl.setAttribute("aria-invalid", "true"); cEl.focus();
        throw new ApiError("Enter the presenting complaint.", 400, "invalid");
      }
      const vitals = readVitals(fd);
      const r = await api("/api/patients", { method: "POST", idempotent: true, body: {
        esi: Number(fd.get("esi")), complaint: fd.get("complaint"), needs: fd.getAll("needs"), vitals } });
      apply(r); toast(r.message);
    },
  });
}

export function outageDialog(r, apply) {
  openDialog({
    title: `Simulate ${r.label} outage`,
    submitLabel: "Take offline",
    submitClass: "danger",
    body: `<div class="notice warn">${icon("triangle-alert")}<span>Simulation only. This does not control any real equipment.</span></div>
      <div class="form-row">
        <label class="field"><span>Duration (minutes)</span><input name="minutes" type="number" min="1" max="1440" value="60" required></label>
        <label class="field grow"><span>Reason (optional)</span><input name="reason" maxlength="120"></label>
      </div>
      <p class="hint">${r.status === "Offline" ? `Already offline for ${r.outage.remaining} more min. A longer duration extends the outage; a shorter one leaves it unchanged.` :
        "Scans in progress are interrupted and return to the queue."}</p>`,
    onSubmit: async (fd) => {
      const res = await api(`/api/resources/${r.code}/outage`, { method: "POST", idempotent: true,
        body: { minutes: Number(fd.get("minutes")), reason: fd.get("reason") } });
      apply(res); toast(res.message, res.result && res.result.changed === false ? "info" : "success");
    },
  });
}
