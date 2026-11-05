// Shared by the customer app and the staff console. Pages are built with h() and text nodes only: nothing from the server or from a
// customer is ever put into the page as HTML, so a name like "<script>" is shown as text and cannot run.
const SVG = "http://www.w3.org/2000/svg";

export function h(tag, attrs, ...kids) {
  const el = tag.startsWith("svg:") ? document.createElementNS(SVG, tag.slice(4)) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "class") el.setAttribute("class", v);
    else if (["value", "checked", "disabled", "selected"].includes(k) && !tag.startsWith("svg:")) el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  add(el, kids);
  return el;
}
function add(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k === null || k === undefined || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
}
export const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };
export const mount = (el, ...kids) => { clear(el); add(el, kids); };

// ---------------------------------------------------------------------------------------------- API
export class ApiError extends Error {
  constructor(status, code, message, details) { super(message); this.status = status; this.code = code; this.details = details; }
}

export class Api {
  /** refreshUrl: where a refresh token is exchanged. key: where it is kept. Staff use sessionStorage (gone when the tab closes). */
  constructor({ refreshUrl, key, storage = localStorage, onSignedOut }) {
    Object.assign(this, { refreshUrl, key, storage, onSignedOut });
    this.access = null;
    this._refreshing = null;
  }
  get refreshToken() { try { return this.storage.getItem(this.key); } catch { return null; } }
  set refreshToken(v) { try { v ? this.storage.setItem(this.key, v) : this.storage.removeItem(this.key); } catch { /* private mode */ } }
  signedIn() { return !!(this.access || this.refreshToken); }
  setTokens(t) { this.access = t.access_token; this.refreshToken = t.refresh_token; }
  clear() { this.access = null; this.refreshToken = null; }

  async refresh() {
    if (this._refreshing) return this._refreshing;           // one refresh at a time: a refresh token works once
    const run = async () => {
      const rt = this.refreshToken;
      if (!rt) return false;
      const r = await fetch(this.refreshUrl, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: rt }) });
      if (!r.ok) { this.clear(); return false; }
      this.setTokens(await r.json());
      return true;
    };
    // The lock also serialises tabs, so two open tabs cannot both spend the same refresh token.
    this._refreshing = (navigator.locks ? navigator.locks.request("gi-refresh-" + this.key, run) : run()).finally(() => { this._refreshing = null; });
    return this._refreshing;
  }

  async call(method, url, body, { blob = false, retry = true } = {}) {
    if (!this.access && this.refreshToken) await this.refresh();
    const headers = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (this.access) headers.Authorization = "Bearer " + this.access;
    let r;
    try { r = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) }); }
    catch { throw new ApiError(0, "network", "Cannot reach the server. Check your internet connection."); }
    if (r.status === 401 && retry) {
      const j = await r.clone().json().catch(() => ({}));
      if (["invalid_token", "missing_token"].includes(j.error?.code) && await this.refresh()) return this.call(method, url, body, { blob, retry: false });
      if (["invalid_token", "missing_token"].includes(j.error?.code)) { this.clear(); this.onSignedOut?.(); }
    }
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new ApiError(r.status, j.error?.code || "error", j.error?.message || "Something went wrong. Try again.", j.error?.details);
    }
    if (r.status === 204) return null;
    return blob ? r.blob() : r.json();
  }
  get(u, o) { return this.call("GET", u, undefined, o); }
  post(u, b) { return this.call("POST", u, b ?? {}); }
  put(u, b) { return this.call("PUT", u, b); }
  patch(u, b) { return this.call("PATCH", u, b); }
  del(u) { return this.call("DELETE", u); }
}

export const qs = (o) => { const p = new URLSearchParams(); for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== null && v !== "") p.set(k, v); const s = p.toString(); return s ? "?" + s : ""; };

// ------------------------------------------------------------------------------------------ small pieces
export function toast(msg, err = false) {
  let box = document.querySelector(".toasts");
  if (!box) { box = h("div", { class: "toasts", role: "status" }); document.body.append(box); }
  const t = h("div", { class: "toast" + (err ? " err" : "") }, msg);
  box.append(t);
  setTimeout(() => t.remove(), err ? 6000 : 3500);
}

export function modal(title, body, { actions = [], onClose } = {}) {
  const close = () => { bg.remove(); document.removeEventListener("keydown", esc); onClose?.(); };
  const esc = (e) => { if (e.key === "Escape") close(); };
  const bg = h("div", { class: "modalbg", onclick: (e) => { if (e.target === bg) close(); } },
    h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title }, h("h2", {}, title), body,
      actions.length ? h("div", { class: "foot" }, actions.map((a) => h("button", { class: "btn " + (a.kind || ""), type: "button", onclick: () => a.run(close) }, a.label))) : null));
  document.addEventListener("keydown", esc);
  document.body.append(bg);
  return { close };
}

export async function copy(text, label = "Copied") {
  try { await navigator.clipboard.writeText(text); toast(label); }
  catch { toast("Could not copy. Select the text and copy it by hand.", true); }
}

export const chip = (text, kind = "") => h("span", { class: "chip " + kind }, text);
export const note = (text, kind = "") => h("div", { class: "note " + kind, role: kind === "err" ? "alert" : null }, text);
export const field = (label, input, hint) => { const id = "f" + Math.random().toString(36).slice(2, 8); input.id = id; return h("div", { class: "field" }, h("label", { for: id }, label), input, hint ? h("div", { class: "hint" }, hint) : null); };

export function busy(btn, fn) {
  return async (...a) => {
    if (btn.disabled) return;
    btn.disabled = true;
    try { return await fn(...a); } finally { btn.disabled = false; }
  };
}

const IST = "Asia/Kolkata";
export const fmtNum = (n) => (n ?? 0).toLocaleString("en-IN");
export const fmtDateTime = (iso) => iso ? new Date(iso).toLocaleString("en-IN", { timeZone: IST, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true }) : "never";
export function ago(iso) {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + " min ago";
  if (s < 86400) return Math.floor(s / 3600) + " h ago";
  return Math.floor(s / 86400) + " days ago";
}
/** Start of "today" in India as an ISO string with the +05:30 offset, which is what the API wants. */
export function istDay(offsetDays = 0) {
  const now = new Date(Date.now() + 5.5 * 3600e3 + offsetDays * 86400e3);
  return now.toISOString().slice(0, 10) + "T00:00:00+05:30";
}

export function icon(path) {
  return h("svg:svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, h("svg:path", { d: path }));
}
export const BRAND = { name: "Jas iVision", tagline: "Intelligent Vision. Automated Insights." };
export const LOGO = "M3 7l9-4 9 4v10l-9 4-9-4z M12 3v18";

/** Grouped bar chart as SVG. series: [{name, color, values[]}], labels: x labels. */
export function barChart(labels, series, { height = 220 } = {}) {
  const W = 720, H = height, L = 38, B = 24, T = 8, R = 6;
  const max = Math.max(1, ...series.flatMap((s) => s.values));
  const nice = Math.pow(10, Math.floor(Math.log10(max))), top = Math.ceil(max / nice) * nice;
  const svg = h("svg:svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": "Bar chart of people counted" });
  for (let i = 0; i <= 4; i++) {
    const y = T + (H - T - B) * (1 - i / 4);
    svg.append(h("svg:line", { class: "gl", x1: L, x2: W - R, y1: y, y2: y }), h("svg:text", { x: L - 6, y: y + 4, "text-anchor": "end" }, fmtNum(Math.round((top * i) / 4))));
  }
  const slot = (W - L - R) / Math.max(1, labels.length), bw = Math.max(2, Math.min(18, (slot * 0.8) / series.length));
  const every = Math.ceil(labels.length / 12);
  labels.forEach((lab, i) => {
    const x0 = L + slot * i + (slot - bw * series.length) / 2;
    series.forEach((s, j) => {
      const v = s.values[i] || 0, hgt = (H - T - B) * (v / top);
      if (v > 0) svg.append(h("svg:rect", { x: x0 + bw * j, y: H - B - hgt, width: bw - 1, height: hgt, rx: 2, fill: s.color }, h("svg:title", {}, `${lab}: ${s.name} ${v}`)));
    });
    if (i % every === 0) svg.append(h("svg:text", { x: L + slot * i + slot / 2, y: H - 6, "text-anchor": "middle" }, lab));
  });
  return svg;
}

export function copyRow(label, value, { secret = false } = {}) {
  return [h("dt", {}, label), h("dd", {}, value), h("button", { class: "btn sm", type: "button", onclick: () => copy(value, label + " copied"), "aria-label": "Copy " + label }, "Copy")];
}

export function errText(e) {
  if (e?.details?.length) return e.details.map((d) => `${d.field ? d.field + ": " : ""}${d.problem}`).join("; ");
  return e?.message || "Something went wrong.";
}
