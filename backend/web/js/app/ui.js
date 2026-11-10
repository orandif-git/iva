// Small UI kit for the customer app: icons, sheets, toast, chart, pictures. Everything is built with h(), never HTML strings.
import { fmtNum, h, clear, mount } from "../lib.js";

const P = {
  today: '<path d="M3.5 11.2L12 4l8.5 7.2V19a1.5 1.5 0 01-1.5 1.5h-4.2v-5.8H9.2v5.8H5A1.5 1.5 0 013.5 19z"/>',
  cam: '<rect x="2.5" y="6.5" width="13" height="11" rx="2.5"/><path d="M15.5 11l6-3.2v8.4l-6-3.2"/>',
  activity: '<path d="M5.5 20v-7M12 20V5M18.5 20v-10"/>',
  bell: '<path d="M6 17.5V11a6 6 0 0112 0v6.5l1.7 2H4.3z"/><path d="M10 22h4"/>',
  more: '<circle cx="5.5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="18.5" cy="12" r="1.3"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>', plus: '<path d="M12 5v14M5 12h14"/>', chev: '<path d="M9 6l6 6-6 6"/>', x: '<path d="M6 6l12 12M18 6L6 18"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>', car: '<path d="M3.5 16v-4.5L6 6.5h12l2.5 5V16"/><path d="M3 16h18v3H3z"/><circle cx="7.5" cy="19" r="1.4"/><circle cx="16.5" cy="19" r="1.4"/>',
  server: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
  doc: '<path d="M6.5 3h7l4.5 4.5V20a1 1 0 01-1 1H6.5a1 1 0 01-1-1V4a1 1 0 011-1z"/><path d="M9 13h6M9 17h6M9 9h2"/>',
  user: '<circle cx="12" cy="8" r="3.6"/><path d="M5 20.5a7 7 0 0114 0"/>', moon: '<path d="M20 14.5A8.5 8.5 0 119.5 4a7 7 0 0010.5 10.5z"/>',
  nowifi: '<path d="M2.5 9a14 14 0 0113-3.6M5.5 12.5a9.5 9.5 0 017-2.9M8.8 16a5 5 0 013.7-1.2"/><path d="M3 3l18 18"/>',
  share: '<path d="M12 3.5v11M8.2 7.3L12 3.5l3.8 3.8M5.5 12v7a1.5 1.5 0 001.5 1.5h10a1.5 1.5 0 001.5-1.5v-7"/>',
  key: '<circle cx="8" cy="12" r="3.5"/><path d="M11.5 12H21M17 12v3M20 12v2.2"/>',
  copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2.5"/><path d="M15.5 8.5V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7.5a2 2 0 002 2h2.5"/>',
  down: '<path d="M12 4v11M7.5 10.5L12 15l4.5-4.5M5 20h14"/>', phone: '<rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="M11 18.5h2"/>',
  lock: '<rect x="5" y="11" width="14" height="9.5" rx="2.5"/><path d="M8.5 11V8a3.5 3.5 0 017 0v3"/>',
  line: '<path d="M4 17l16-10"/><circle cx="4" cy="17" r="1.6"/><circle cx="20" cy="7" r="1.6"/>', swap: '<path d="M7 4L3.5 7.5 7 11M3.5 7.5H20M17 13l3.5 3.5L17 20M20.5 16.5H4"/>',
  edit: '<path d="M4 20h4L19 9a2.1 2.1 0 00-3-3L5 17z"/><path d="M14.5 7.5l3 3"/>', trash: '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l.8 12.5h9.4L17.5 7M10 11v5M14 11v5"/>',
  users: '<circle cx="9" cy="8" r="3.4"/><path d="M2.5 20a6.5 6.5 0 0113 0M16 5.3a3.3 3.3 0 010 6.4M18 14.2a6 6 0 013.5 5.8"/>',
  card: '<rect x="2.5" y="5" width="19" height="14" rx="2.5"/><path d="M2.5 10h19M6 15h4"/>',
  chat: '<path d="M4 5.5h16a1 1 0 011 1V16a1 1 0 01-1 1h-8l-4.5 3.5V17H4a1 1 0 01-1-1V6.5a1 1 0 011-1z"/>',
  wrench: '<path d="M14.5 6a4 4 0 005 5l-8.7 8.7a2.1 2.1 0 01-3-3L16.5 8M14.5 6a4 4 0 014-4l-2.5 2.5L18 6.5 20.5 4"/>',
  log: '<path d="M6.5 3h7l4.5 4.5V20a1 1 0 01-1 1H6.5a1 1 0 01-1-1V4a1 1 0 011-1z"/><path d="M9 13h6M9 17h6M9 9h2"/>',
  shield: '<path d="M12 3l7.5 3v5.5c0 4.6-3 8-7.5 9.5-4.5-1.5-7.5-4.9-7.5-9.5V6z"/><path d="M9 12l2.2 2.2L15.5 10"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="3"/>',
  cloud: '<path d="M7 18.5a4.5 4.5 0 01-.6-8.96A6 6 0 0118 9.2a4.7 4.7 0 01-.5 9.3z"/>',
  tag: '<path d="M3.5 12.5V5A1.5 1.5 0 015 3.5h7.5l8 8a1.5 1.5 0 010 2.1l-6.4 6.4a1.5 1.5 0 01-2.1 0z"/><circle cx="8" cy="8" r="1.3"/>',
  link: '<path d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1"/>',
  spark: '<path d="M10 4l1.8 5.2L17 11l-5.2 1.8L10 18l-1.8-5.2L3 11l5.2-1.8zM18 3l.8 2.2L21 6l-2.2.8L18 9l-.8-2.2L15 6l2.2-.8z"/>',
  rows: '<path d="M4 6h16M4 12h16M4 18h16"/>', panel: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M9.5 4v16"/>', dash: '<rect x="3.5" y="3.5" width="7" height="8" rx="1.8"/><rect x="13.5" y="3.5" width="7" height="5" rx="1.8"/><rect x="13.5" y="11.5" width="7" height="9" rx="1.8"/><rect x="3.5" y="14.5" width="7" height="6" rx="1.8"/>',
  pause: '<path d="M9 5v14M15 5v14"/>', play: '<path d="M8 5l11 7-11 7z"/>',
};
const SVGNS = "http://www.w3.org/2000/svg";
/** An icon. The path markup above is fixed text in this file, parsed as SVG: no server or customer text ever goes through it. */
export function ic(name, cls = "") {
  const doc = new DOMParser().parseFromString(`<svg xmlns="${SVGNS}">${P[name] || ""}</svg>`, "image/svg+xml");
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("class", "i " + cls);
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  for (const n of [...doc.documentElement.childNodes]) svg.append(document.importNode(n, true));
  return svg;
}

export const buzz = (p = 8) => { try { navigator.vibrate?.(p); } catch { /* not supported, for example on iPhone */ } };

export const logoPair = (cls = "") => [
  h("img", { class: "lg-l " + cls, alt: "Jas iVision", src: "/img/logo-compact.svg" }),
  h("img", { class: "lg-d " + cls, alt: "Jas iVision", src: "/img/logo-compact-on-dark.svg" })];
export const lockupPair = (cls = "") => [
  h("img", { class: "lg-l " + cls, alt: "Jas iVision. Intelligent Vision. Automated Insights.", src: "/img/logo.svg" }),
  h("img", { class: "lg-d " + cls, alt: "", src: "/img/logo-on-dark.svg" })];

// ----------------------------------------------------------------------------------------------- toast and sheets
let toastEl, toastTimer;
export function mountOverlays(root) {
  toastEl = h("div", { class: "toast", role: "status", "aria-live": "polite" });
  const sheetBody = h("div", { class: "sb2" }), sheetTitle = h("h2", {});
  const layer = h("div", { class: "layer", "aria-hidden": "true" },
    h("div", { class: "scrim", onclick: () => closeSheet() }),
    h("div", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-labelledby": "sheet-title" }, h("div", { class: "grab" }),
      h("div", { class: "sh" }, Object.assign(sheetTitle, { id: "sheet-title" }), h("button", { class: "xbtn", type: "button", "aria-label": "Close", onclick: () => closeSheet() }, ic("x", ""))), sheetBody));
  root.append(layer, toastEl);
  openSheet.parts = { layer, sheetBody, sheetTitle };
  addEventListener("keydown", (e) => { if (e.key === "Escape" && layer.classList.contains("on")) closeSheet(); });
}
let sheetClose = null, lastFocus = null;
export function openSheet(title, ...body) {
  const { layer, sheetBody, sheetTitle } = openSheet.parts;
  if (sheetClose) { const f = sheetClose; sheetClose = null; f(); }                // a sheet that is replaced cleans up like one that is closed
  layer.querySelector(".sheet").classList.remove("wide");
  lastFocus = document.activeElement;
  sheetTitle.textContent = title;
  mount(sheetBody, body);
  layer.classList.add("on");
  layer.setAttribute("aria-hidden", "false");
  buzz(6);
  setTimeout(() => sheetBody.querySelector("input,select,button,a")?.focus?.({ preventScroll: true }), 120);
  return { body: sheetBody, close: closeSheet };
}
export function closeSheet() {
  const { layer, sheetBody } = openSheet.parts;
  layer.classList.remove("on");
  layer.setAttribute("aria-hidden", "true");
  setTimeout(() => { if (!layer.classList.contains("on")) clear(sheetBody); }, 400);
  try { lastFocus?.focus?.({ preventScroll: true }); } catch { /* element is gone */ }
  sheetClose?.();
  sheetClose = null;
}
export const onSheetClose = (fn) => { sheetClose = fn; };
export function toast(msg, err = false) {
  if (!toastEl) return;
  toastEl.textContent = msg;
  toastEl.classList.add("on");
  buzz(err ? [18, 50, 18] : [12, 45, 12]);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove("on"), err ? 5000 : 2600);
}
/** A confirmation sheet with a plain-words warning. */
export function confirmSheet(title, text, label, run, { danger = true } = {}) {
  const err = h("div");
  const btn = h("button", { class: "btn " + (danger ? "danger" : ""), type: "button" }, label);
  btn.onclick = async () => {
    if (btn.disabled) return;
    btn.disabled = true;
    try { await run(); closeSheet(); } catch (e) { clear(err); err.append(note(e?.message || "Something went wrong.", "err")); btn.disabled = false; }
  };
  openSheet(title, h("p", {}, text), err, h("div", { class: "acts" }, btn, h("button", { class: "btn gray", type: "button", onclick: () => closeSheet() }, "Cancel")));
}

export const note = (text, kind = "") => h("div", { class: "note " + kind, role: kind === "err" ? "alert" : null }, text);
export const pill = (text, kind = "") => h("span", { class: "pillx " + kind }, text);
export const field = (label, input, hint) => { const id = "f" + Math.random().toString(36).slice(2, 8); input.id = id; return h("div", { class: "field" }, h("label", { for: id }, label), input, hint ? h("div", { class: "hint" }, hint) : null); };
export const toggle = (checked, onchange, label) => { const i = h("input", { type: "checkbox", checked, role: "switch", "aria-label": label, onchange: () => onchange(i) }); return h("label", { class: "tg" }, i, h("span")); };
export function seg(options, value, onpick) {
  const wrap = h("div", { class: "seg2", role: "group" });
  const draw = (v) => mount(wrap, options.map(([k, l]) => h("button", { type: "button", "aria-pressed": String(k === v), onclick: () => { buzz(5); draw(k); onpick(k); } }, l)));
  draw(value);
  return wrap;
}
export const chips = (options, value, onpick) => {
  const wrap = h("div", { class: "chips" });
  const draw = (v) => mount(wrap, options.map(([k, l]) => h("button", { class: "chip", type: "button", "aria-pressed": String(k === v), onclick: () => { draw(k); onpick(k); } }, l)));
  draw(value);
  return wrap;
};
export const sec = (title, right) => h("div", { class: "sec" }, h("h2", {}, title), right || null);
export const kpi = (label, value, sub) => h("div", { class: "kpi static" }, h("span", {}, label), h("strong", {}, value), sub ? h("small", {}, sub) : null);
export const skeleton = (n = 3) => h("div", { class: "stack", "aria-busy": "true", "aria-label": "Loading" }, Array.from({ length: n }, () => h("div", { class: "skel blk" })));
export function empty(icon, title, text, action) {
  return h("div", { class: "empty card" }, h("div", { class: "big" }, ic(icon)), h("h3", {}, title), h("p", {}, text), action || null);
}

// ------------------------------------------------------------------------------------------------ camera pictures
const pics = new Map();                      // camera id -> { url, at }
let inflight = 0; const waiting = [];
const slot = () => new Promise((res) => { const go = () => { inflight++; res(); }; inflight < 2 ? go() : waiting.push(go); });
const release = () => { inflight--; waiting.shift()?.(); };
/** The camera's real picture. Tiles load two at a time so a page of cameras does not hammer the recorders. */
export async function loadPicture(api, id, { fresh = false } = {}) {
  const c = pics.get(id);
  if (c && !fresh && Date.now() - c.at < 60000) return c.url;
  await slot();
  try {
    const blob = await api.get(`/v1/cameras/${id}/snapshot`, { blob: true });
    if (c) URL.revokeObjectURL(c.url);
    const url = URL.createObjectURL(blob);
    pics.set(id, { url, at: Date.now() });
    return url;
  } finally { release(); }
}
export const dropPictures = () => { for (const { url } of pics.values()) URL.revokeObjectURL(url); pics.clear(); };

// How often the pictures on the screen are fetched again. Kept on this device; the shortest allowed is 3 seconds, and 0 turns it off.
const PIC_KEY = "gi_pic_refresh";
export const MIN_REFRESH = 3;
export function picInterval() {
  try { const v = localStorage.getItem(PIC_KEY); if (v === null) return 10; const n = Number(v); return n === 0 ? 0 : Math.max(MIN_REFRESH, Number.isFinite(n) && n > 0 ? n : 10); } catch { return 10; }
}
export function setPicInterval(n) {
  const v = n === 0 ? 0 : Math.min(3600, Math.max(MIN_REFRESH, Math.round(n)));
  try { localStorage.setItem(PIC_KEY, String(v)); } catch { /* private mode */ }
  return v;
}

/** A picture frame showing the camera's own picture, or a plain message when there is none. It fetches a fresh picture every few seconds
 *  while the page is open and visible, and stops by itself when the frame leaves the page. */
export function pictureFrame(api, cam, { tag } = {}) {
  const frame = h("div", { class: "scene" });
  const msg = (t) => mount(frame, h("div", { class: "none" }, t));
  msg(cam.enabled ? "Getting a picture\u2026" : "Paused");
  if (!cam.enabled) return frame;
  let img = null, seen = false, misses = 0;
  const tick = async () => {
    if (!frame.isConnected) { if (seen || ++misses > 5) return; setTimeout(tick, 1000); return; }   // not on the page yet, or the page moved on
    seen = true;
    const sec = picInterval();
    if (img && sec && !document.hidden && navigator.onLine !== false) {
      try { img.src = await loadPicture(api, cam.id, { fresh: true }); } catch { /* keep the picture we have */ }
    }
    setTimeout(tick, Math.max(MIN_REFRESH, sec || 5) * 1000);
  };
  loadPicture(api, cam.id).then((url) => {
    img = h("img", { src: url, alt: `Latest picture from ${cam.name}`, draggable: "false" });
    mount(frame, img, tag ? h("span", { class: "tag" }, tag) : null);
    setTimeout(tick, Math.max(MIN_REFRESH, picInterval() || 5) * 1000);
  }, (e) => msg(e.status === 403 ? "Pictures are shown to administrators" : "No picture yet"));
  return frame;
}

// ----------------------------------------------------------------------------------------------------- charts
/** Bars for in and out per bucket with a tooltip that follows a finger or the mouse. items: [{label, i, o}] */
export function barChart(items, { mode = "both" } = {}) {
  const W = 640, H = 230, L = 36, B = 24, T = 10, R = 6, n = Math.max(1, items.length), slotW = (W - L - R) / n, bw = Math.min(15, slotW * 0.36);
  const vals = items.map((x) => (mode === "net" ? x.i - x.o : x.i));
  const mx = Math.max(1, ...items.flatMap((x) => [x.i, x.o]));
  const pow = Math.pow(10, Math.floor(Math.log10(mx))), top = Math.max(4, Math.ceil(mx / pow) * pow);
  const y = (v) => H - B - (H - T - B) * (v / top);
  const svg = h("svg:svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "People counted over time" });
  for (let k = 0; k <= 4; k++) {
    const gy = T + (H - T - B) * (1 - k / 4);
    svg.append(h("svg:line", { x1: L, x2: W - R, y1: gy, y2: gy, stroke: "var(--line)" }), h("svg:text", { x: L - 6, y: gy + 4, "text-anchor": "end" }, fmtNum(Math.round((top * k) / 4))));
  }
  const every = Math.ceil(n / 8);
  items.forEach((it, k) => {
    const cx = L + slotW * k + slotW / 2;
    if (mode === "net") { const v = Math.max(0, vals[k]); if (v > 0) svg.append(h("svg:rect", { x: cx - bw / 2, y: y(v), width: bw, height: Math.max(2, H - B - y(v)), rx: 4, fill: "var(--brand)" })); }
    else {
      if (it.i > 0) svg.append(h("svg:rect", { x: cx - bw - 1, y: y(it.i), width: bw, height: Math.max(2, H - B - y(it.i)), rx: 4, fill: "var(--brand-bright)" }));
      if (it.o > 0) svg.append(h("svg:rect", { x: cx + 1, y: y(it.o), width: bw, height: Math.max(2, H - B - y(it.o)), rx: 4, fill: "var(--ai)", opacity: ".75" }));
    }
    if (k % every === 0) svg.append(h("svg:text", { x: cx, y: H - 6, "text-anchor": "middle" }, it.label));
  });
  const cross = h("svg:line", { x1: 0, x2: 0, y1: T, y2: H - B, stroke: "var(--fg2)", "stroke-dasharray": "3 3", opacity: 0 });
  svg.append(cross);
  const tip = h("div", { class: "tip" });
  const wrap = h("div", { class: "chart" }, svg, tip);
  const move = (e) => {
    const r = svg.getBoundingClientRect(), cx = e.touches ? e.touches[0].clientX : e.clientX, x = ((cx - r.left) / r.width) * W, k = Math.floor((x - L) / slotW);
    if (k < 0 || k >= n) return leave();
    const mid = L + slotW * k + slotW / 2;
    cross.setAttribute("x1", mid); cross.setAttribute("x2", mid); cross.setAttribute("opacity", ".7");
    tip.textContent = `${items[k].label}: in ${fmtNum(items[k].i)} · out ${fmtNum(items[k].o)}`;
    tip.style.left = (mid / W) * r.width + "px"; tip.style.top = "10px"; tip.classList.add("on");
  };
  const leave = () => { tip.classList.remove("on"); cross.setAttribute("opacity", "0"); };
  wrap.addEventListener("pointermove", move); wrap.addEventListener("pointerleave", leave);
  wrap.addEventListener("touchmove", move, { passive: true }); wrap.addEventListener("touchend", leave);
  return wrap;
}
export const legend = () => h("div", { class: "legend" }, h("span", {}, h("i", { class: "lgi a" }), "In"), h("span", {}, h("i", { class: "lgi b" }), "Out"));

/** Makes the open sheet wide on a desktop window, for forms with a lot in them. */
export const sheetWide = () => openSheet.parts.layer.querySelector(".sheet").classList.add("wide");

/** A sheet that asks for a short reason (saved in the audit log) before it does something. run(reason) may throw to show an error. */
export function reasonSheet({ title, text, label, danger = false, min = 5, run, extra = null }) {
  const err = h("div"), why = h("input", { type: "text", placeholder: "For example: customer called, locked out", autocomplete: "off", "aria-label": "Reason" });
  const btn = h("button", { class: "btn " + (danger ? "danger" : ""), type: "button", disabled: true }, label);
  why.oninput = () => { btn.disabled = why.value.trim().length < min; };
  btn.onclick = async () => {
    if (btn.disabled) return;
    btn.disabled = true;
    try { await run(why.value.trim()); } catch (e) { clear(err); err.append(note(e?.message || "Something went wrong.", "err")); btn.disabled = false; }
  };
  openSheet(title, text ? h("p", {}, text) : null, extra, field("Reason (saved in the audit log)", why), err, h("div", { class: "acts" }, btn, h("button", { class: "btn gray", type: "button", onclick: () => closeSheet() }, "Cancel")));
}

/** A sheet with a form. fields are nodes; run() may throw to show an error and keep the sheet open. */
export function formSheet(title, fields, label, run, { wide = false } = {}) {
  const err = h("div"), btn = h("button", { class: "btn", type: "button" }, label);
  btn.onclick = async () => {
    if (btn.disabled) return;
    btn.disabled = true;
    try { await run(); } catch (e) { clear(err); err.append(note(e?.message || "Something went wrong.", "err")); btn.disabled = false; }
  };
  openSheet(title, fields, err, h("div", { class: "acts" }, btn, h("button", { class: "btn gray", type: "button", onclick: () => closeSheet() }, "Cancel")));
  if (wide) sheetWide();
}
