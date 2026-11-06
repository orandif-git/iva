// The screens of the customer app. Each takes the shared context X and returns the nodes for the page.
import { ago, clear, errText, fmtDateTime, fmtNum, h, istDay, mount, qs } from "../lib.js";
import { barChart, chips, confirmSheet, empty, field, ic, kpi, legend, loadPicture, note, onSheetClose, openSheet, closeSheet, pictureFrame, pill, sec, seg, toast, toggle } from "./ui.js";
import { recorderScreens } from "./recorders.js";

const TZ = "Asia/Kolkata";
export const STATE = { streaming: ["Live", "ok"], reconnecting: ["Reconnecting", "warn"], offline: ["Offline", "crit"], unknown: ["Waiting", ""] };
const camState = (c) => (c.enabled ? STATE[c.stream_state] || STATE.unknown : ["Paused", ""]);
const hourLabel = (iso) => new Date(iso).toLocaleTimeString("en-IN", { timeZone: TZ, hour: "numeric", hour12: true }).replace(" ", "").toLowerCase();
const dayLabel = (iso) => new Date(iso).toLocaleDateString("en-IN", { timeZone: TZ, day: "numeric", month: "short" });
const todayTitle = () => new Date().toLocaleDateString("en-IN", { timeZone: TZ, weekday: "long", day: "numeric", month: "long" });
export const title = (t, sub, right) => h("div", { class: "pagehead" }, h("div", {}, h("h1", { class: "lt" }, t), sub ? h("p", { class: "sub" }, sub) : null), right || null);
const link = (text, href) => h("a", { class: "link", href }, text);
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

// ------------------------------------------------------------------------------------------------------ Today
async function today(X) {
  const { api } = X;
  const [sum, f, cams, recs, alerts] = await Promise.all([
    api.get("/v1/footfall/summary" + qs({ from: istDay(), to: istDay(1), tz: TZ })),
    api.get("/v1/footfall" + qs({ from: istDay(), to: istDay(1), interval: "hour", tz: TZ })),
    api.get("/v1/cameras"), api.get("/v1/recorders"), api.get("/v1/alerts?limit=4&unacknowledged=true")]);
  const out = [title("Today", todayTitle())];
  if (!recs.length) {
    out.push(empty("server", "Connect your first recorder", "It takes about ten minutes. You type a few details into your recorder and we do the rest. Nobody needs to visit your site.",
      X.isAdmin ? h("a", { class: "btn", href: "#/recorders/new" }, "Connect a recorder") : h("p", {}, "Ask your administrator to connect a recorder.")));
    return out;
  }
  const live = cams.filter((c) => c.enabled && c.stream_state === "streaming").length, on = cams.filter((c) => c.enabled).length;
  out.push(h("div", { class: "kpis" }, kpi("People in", fmtNum(sum.people_in)), kpi("People out", fmtNum(sum.people_out)), kpi("Inside now", fmtNum(sum.net), "in minus out"), kpi("Cameras live", `${live} of ${on}`)));
  if (sum.peak_hour) out.push(h("p", { class: "sub" }, `Busiest hour so far: ${hourLabel(sum.peak_hour.bucket)} with ${fmtNum(sum.peak_hour.people_in)} people in.`));
  const items = f.items.map((p) => ({ label: hourLabel(p.bucket), i: p.people_in, o: p.people_out }));
  out.push(h("div", { class: "card" }, h("div", { class: "pagehead" }, h("h3", {}, "People by hour"), legend()), items.some((x) => x.i || x.o) ? barChart(items) : h("p", { class: "sub" }, "Nobody counted yet today.")));
  if (cams.length) {
    out.push(sec("Cameras", link("See all", "#/cameras")), h("div", { class: "rail" }, cams.slice(0, 6).map((c) => camTile(X, c))));
  } else if (X.isAdmin) out.push(empty("cam", "Choose your cameras", "Your recorder is connected. Pick which of its cameras to watch.", h("a", { class: "btn", href: "#/recorders/" + recs[0].id }, "Choose cameras")));
  if (alerts.length) out.push(sec("Needs attention", link("All alerts", "#/alerts")), h("div", { class: "card" }, alerts.map((a) => alertRow(X, a))));
  return out;
}

function camTile(X, c) {
  const [t, k] = camState(c);
  const meta = [c.use_footfall ? "People" : null, c.use_anpr ? "Number plates" : null].filter(Boolean).join(" · ");
  return h("a", { class: "cam", href: "#/cameras/" + c.id }, pictureFrame(X.api, c), h("div", { class: "meta" }, h("div", {}, h("b", {}, c.name), h("small", {}, meta || "Not counting")), pill(t, k)));
}

function alertRow(X, a) {
  const kind = a.severity === "critical" ? "crit" : a.severity === "warning" ? "warn" : "ok";
  return h("button", { class: "rowlink", type: "button", onclick: () => alertSheet(X, a) },
    h("span", { class: "ico " + kind }, ic("bell")), h("div", {}, h("b", {}, a.title), h("small", {}, a.body || a.camera_name || "")), h("time", {}, ago(a.created_at)));
}

// ------------------------------------------------------------------------------------------------------ Cameras
async function cameras(X) {
  const cams = await X.api.get("/v1/cameras");
  const out = [title("Cameras", `${cams.length} ${cams.length === 1 ? "camera" : "cameras"}`, X.isAdmin ? h("a", { class: "btn sm sec2", href: "#/recorders" }, ic("plus"), "Add") : null)];
  out.push(cams.length ? h("div", { class: "camgrid" }, cams.map((c) => camTile(X, c)))
    : empty("cam", "No cameras yet", "Connect a recorder, then choose which of its cameras to watch.", X.isAdmin ? h("a", { class: "btn", href: "#/recorders" }, "Go to recorders") : null));
  return out;
}

const round = (v) => Math.round(Math.min(1, Math.max(0, v)) * 1000) / 1000;
const normal = (l) => { const dx = l[1][0] - l[0][0], dy = l[1][1] - l[0][1], n = Math.hypot(dx, dy) || 1; return [-dy / n, dx / n]; };
const dotp = (a, b) => a[0] * b[0] + a[1] * b[1];

async function cameraPage(X, id) {
  const { api } = X;
  const [cams, zones] = await Promise.all([api.get("/v1/cameras"), api.get(`/v1/cameras/${id}/zones`)]);
  const cam = cams.find((c) => c.id === id);
  if (!cam) return [note("That camera was not found.", "err"), h("a", { class: "btn gray", href: "#/cameras" }, "Back to cameras")];
  const [t, k] = camState(cam);
  const out = [h("a", { class: "backlink", href: "#/cameras" }, ic("chev"), "Cameras"), title(cam.name, null, pill(t, k))];
  const feats = [cam.use_footfall ? "Counting people" : null, cam.use_anpr ? "Reading number plates" : null].filter(Boolean).join(" · ");
  out.push(h("p", { class: "sub" }, `${feats || "Not counting"} · last picture ${ago(cam.last_frame_at)}`));
  if (!X.isAdmin) { out.push(pictureFrame(api, cam)); return out; }

  // The editor: the camera's real picture with a draggable counting line (and a plate area) drawn over it.
  const ed = { line: [[0.15, 0.6], [0.85, 0.6]], flip: 1, poly: [], mode: null, dirty: false };
  const zl = zones.find((z) => z.kind === "count_line"), zp = zones.find((z) => z.kind === "anpr_zone");
  if (zl) { ed.line = zl.points; ed.flip = zl.in_direction && dotp(zl.in_direction, normal(zl.points)) < 0 ? -1 : 1; }
  if (zp) ed.poly = zp.points;
  const frame = h("div", { class: "scene editor" });
  const holder = h("div", { class: "pic" });
  const over = h("svg:svg", { class: "over", viewBox: "0 0 1 1", preserveAspectRatio: "none" });
  const help = h("p", { class: "sub" });
  const dirty = () => { ed.dirty = true; saveBtn.disabled = false; };
  const redraw = () => {
    clear(over);
    if (cam.use_footfall && ed.line.length === 2) {
      const [a, b] = ed.line, n = normal(ed.line).map((v) => v * ed.flip * 0.1), m = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
      over.append(h("svg:line", { class: "zline", x1: a[0], y1: a[1], x2: b[0], y2: b[1] }),
        h("svg:path", { d: `M${m[0]} ${m[1]} L${m[0] + n[0]} ${m[1] + n[1]}`, stroke: "#fff", "stroke-width": ".006", fill: "none" }),
        h("svg:text", { x: m[0] + n[0] * 1.4, y: m[1] + n[1] * 1.4, fill: "#fff", "font-size": ".035", "text-anchor": "middle" }, "IN"));
      ed.line.forEach((p, i) => over.append(h("svg:circle", { class: "handle", "data-h": i, cx: p[0], cy: p[1], r: 0.03, fill: "#FFD23F", stroke: "#0A1218", "stroke-width": ".006" })));
    }
    if (cam.use_anpr && ed.poly.length) over.append(h("svg:polygon", { points: ed.poly.map((p) => p.join(",")).join(" "), fill: "rgba(53,198,244,.18)", stroke: "#35C6F4", "stroke-width": ".006" }),
      ...ed.poly.map((p) => h("svg:circle", { cx: p[0], cy: p[1], r: 0.015, fill: "#35C6F4" })));
    help.textContent = ed.mode === "poly" ? "Tap each corner of the area where number plates appear. Press Done when finished." : cam.use_footfall ? "Drag the two yellow dots to put the line across the doorway. People crossing in the direction of the arrow count as IN." : "";
  };
  let drag = null;
  const pt = (e) => { const r = over.getBoundingClientRect(); return [round((e.clientX - r.left) / r.width), round((e.clientY - r.top) / r.height)]; };
  over.addEventListener("pointerdown", (e) => {
    if (ed.mode === "poly") { ed.poly.push(pt(e)); dirty(); redraw(); return; }
    const hd = e.target.closest?.(".handle");
    if (hd) { drag = Number(hd.getAttribute("data-h")); over.setPointerCapture(e.pointerId); e.preventDefault(); }
  });
  over.addEventListener("pointermove", (e) => { if (drag === null) return; ed.line[drag] = pt(e); dirty(); redraw(); });
  const endDrag = () => { drag = null; };
  over.addEventListener("pointerup", endDrag); over.addEventListener("pointercancel", endDrag);
  const showPicture = (fresh) => {
    mount(holder, h("div", { class: "none" }, "Getting a picture…"));
    loadPicture(api, cam.id, { fresh }).then((url) => mount(holder, h("img", { src: url, alt: `Latest picture from ${cam.name}`, draggable: "false", onload: (e) => { frame.style.aspectRatio = `${e.target.naturalWidth} / ${e.target.naturalHeight}`; } })), (e) => mount(holder, h("div", { class: "none" }, errText(e))));
  };
  mount(frame, holder, over);
  showPicture(false);
  redraw();

  const saveBtn = h("button", { class: "btn", type: "button", disabled: true }, "Save");
  saveBtn.onclick = async () => {
    const zs = [];
    if (cam.use_footfall && ed.line.length === 2) zs.push({ kind: "count_line", points: ed.line, in_direction: normal(ed.line).map((v) => round(v * ed.flip)) });
    if (cam.use_anpr && ed.poly.length >= 3) zs.push({ kind: "anpr_zone", points: ed.poly });
    if (!zs.length) return toast("Draw the counting line or plate area first.", true);
    saveBtn.disabled = true;
    try { await api.put(`/v1/cameras/${id}/zones`, { zones: zs }); ed.dirty = false; toast("Saved. The camera uses it within a minute."); } catch (e) { toast(errText(e), true); saveBtn.disabled = false; }
  };
  const tools = h("div", { class: "acts" },
    cam.use_footfall ? h("button", { class: "btn gray sm", type: "button", onclick: () => { ed.flip *= -1; dirty(); redraw(); } }, ic("swap"), "Swap in / out") : null,
    cam.use_anpr ? h("button", { class: "btn gray sm", type: "button", onclick: (ev) => { ed.mode = ed.mode === "poly" ? null : "poly"; if (ed.mode) ed.poly = []; ev.currentTarget.lastChild.textContent = ed.mode ? "Done" : "Draw plate area"; redraw(); } }, ic("line"), "Draw plate area") : null,
    h("button", { class: "btn gray sm", type: "button", onclick: () => showPicture(true) }, "New picture"), saveBtn);
  out.push(h("div", { class: "card" }, h("h3", {}, cam.use_footfall && cam.use_anpr ? "Counting line and plate area" : cam.use_footfall ? "Counting line" : "Plate area"), frame, help, tools));

  // Settings
  const quality = h("select", { onchange: async () => { try { await api.patch("/v1/cameras/" + id, { stream: quality.value }); toast("Picture quality changed. It applies at the next reconnect."); } catch (e) { toast(errText(e), true); } } },
    h("option", { value: "sub", selected: cam.stream === "sub" }, "Light (counting people)"), h("option", { value: "main", selected: cam.stream === "main" }, "Full quality (number plates)"));
  const pause = h("button", { class: "btn gray", type: "button", onclick: async () => { try { await api.patch("/v1/cameras/" + id, { enabled: !cam.enabled }); toast(cam.enabled ? "Paused." : "Resumed."); X.refresh(); } catch (e) { toast(errText(e), true); } } }, ic(cam.enabled ? "pause" : "play"), cam.enabled ? "Pause" : "Resume");
  const remove = h("button", { class: "btn danger", type: "button", onclick: () => confirmSheet("Remove this camera?", "Its past counts and plate reads stay in your reports. The channel goes back to your recorder's list and you can add it again later.", "Remove camera", async () => { await api.del("/v1/cameras/" + id); X.go("#/cameras"); }) }, ic("trash"), "Remove");
  out.push(h("div", { class: "card" }, h("h3", {}, "Settings"), field("Picture quality", quality, "Full quality is needed to read number plates and uses more of our servers."), h("div", { class: "acts" }, pause, remove)));
  return out;
}

// -------------------------------------------------------------------------------------------------------- Activity
async function activity(X) {
  const { api } = X;
  const cams = await api.get("/v1/cameras");
  const st = X.mem.activity ||= { seg: "people", range: "today", cam: "", q: "" };
  const body = h("div");
  const draw = () => (st.seg === "people" ? people(X, st, cams, body) : vehicles(X, st, cams, body));
  const head = [title("Activity"), seg([["people", "People"], ["vehicles", "Vehicles"]], st.seg, (v) => { st.seg = v; draw(); })];
  draw();
  return [...head, h("div", { class: "stack" }, body)];
}

async function people(X, st, cams, body) {
  const days = st.range === "today" ? 1 : Number(st.range), from = istDay(1 - days), to = istDay(1), interval = days === 1 ? "hour" : "day";
  const pick = cams.filter((c) => c.use_footfall);
  const results = h("div", { class: "stack" }, h("div", { class: "skel blk" }));
  mount(body, h("div", { class: "stack" }, chips([["today", "Today"], ["7", "7 days"], ["30", "30 days"]], st.range, (v) => { st.range = v; people(X, st, cams, body); }),
    pick.length > 1 ? chips([["", "All cameras"], ...pick.map((c) => [c.id, c.name])], st.cam, (v) => { st.cam = v; people(X, st, cams, body); }) : null, results));
  try {
    const [f, s] = await Promise.all([X.api.get("/v1/footfall" + qs({ from, to, interval, camera_id: st.cam, tz: TZ })), X.api.get("/v1/footfall/summary" + qs({ from, to, camera_id: st.cam, tz: TZ }))]);
    const items = f.items.map((p) => ({ label: interval === "hour" ? hourLabel(p.bucket) : dayLabel(p.bucket), i: p.people_in, o: p.people_out }));
    mount(results, h("div", { class: "kpis" }, kpi("People in", fmtNum(s.people_in)), kpi("People out", fmtNum(s.people_out)), kpi("Net", fmtNum(s.net)), kpi("Busiest hour", s.peak_hour ? hourLabel(s.peak_hour.bucket) : "\u2014")),
      h("div", { class: "card" }, h("div", { class: "pagehead" }, h("h3", {}, "Over time"), legend()), items.some((x) => x.i || x.o) ? barChart(items) : h("p", { class: "sub" }, "No one counted in this period.")));
  } catch (e) { mount(results, note(errText(e), "err")); }
}

async function vehicles(X, st, cams, body) {
  if (!cams.some((c) => c.use_anpr)) return mount(body, empty("car", "Number plates are not switched on", "Turn on plate reading when you add a camera at a gate.", X.isAdmin ? h("a", { class: "btn", href: "#/recorders" }, "Set up a camera") : null));
  const q = h("input", { type: "search", placeholder: "Search a plate, for example GJ14", maxlength: 20, value: st.q, "aria-label": "Search plates" });
  const list = h("div", { class: "card" }), more = h("div", { class: "center" });
  let cursor = null;
  const load = async (reset) => {
    if (reset) { cursor = null; mount(list, h("div", { class: "skel blk" })); }
    try {
      const r = await X.api.get("/v1/plates" + qs({ q: q.value.trim(), cursor, limit: 30, from: istDay(-6), to: istDay(1) }));
      cursor = r.next_cursor;
      if (reset) clear(list);
      if (reset && !r.items.length) list.append(h("p", { class: "sub" }, q.value ? "No plates match." : "No plates read in the last 7 days."));
      r.items.forEach((p) => list.append(plateRow(X, p, cams)));
      mount(more, cursor ? h("button", { class: "btn gray", type: "button", onclick: () => load(false) }, "Show more") : null);
    } catch (e) { mount(list, note(errText(e), "err")); }
  };
  let tmr; q.oninput = () => { st.q = q.value; clearTimeout(tmr); tmr = setTimeout(() => load(true), 350); };
  mount(body, h("div", { class: "stack" }, h("label", { class: "search" }, ic("search"), q), list, more));
  load(true);
}

function plateRow(X, p, cams) {
  const cam = cams.find((c) => c.id === p.camera_id);
  const flag = p.blocked ? pill("Blocked list", "crit") : p.review_status === "needs_review" ? pill("Needs a check", "warn") : null;
  return h("button", { class: "rowlink", type: "button", onclick: () => plateSheet(X, p, cam) },
    h("span", { class: "ico" + (p.blocked ? " crit" : "") }, ic("car")), h("div", {}, h("span", { class: "plate" + (p.blocked ? " bad" : "") }, p.plate_text || "unreadable"),
      h("small", {}, [cam?.name, p.vehicle_type && cap(p.vehicle_type), p.direction && (p.direction === "in" ? "In" : "Out")].filter(Boolean).join(" · "))),
    h("div", { class: "center" }, h("time", {}, fmtDateTime(p.seen_at)), flag ? h("div", {}, flag) : null));
}
function plateSheet(X, p, cam) {
  const pic = h("div", { class: "scene" }, h("div", { class: "none" }, p.has_snapshot ? "Getting the picture\u2026" : "No picture was saved for this read."));
  let url = null;
  if (p.has_snapshot) X.api.get(`/v1/plates/${p.id}/image`, { blob: true }).then((b) => { url = URL.createObjectURL(b); mount(pic, h("img", { src: url, alt: `Picture taken when ${p.plate_text || "this plate"} was read`, draggable: "false" })); },
    () => mount(pic, h("div", { class: "none" }, "The picture is no longer stored.")));
  openSheet("Number plate", pic, h("div", { class: "center" }, h("span", { class: "plate" + (p.blocked ? " bad" : "") }, p.plate_text || "unreadable")),
    h("dl", { class: "dl" }, h("dt", {}, "Seen"), h("dd", {}, fmtDateTime(p.seen_at)), h("dt", {}, "Camera"), h("dd", {}, cam?.name || "—"), h("dt", {}, "Direction"), h("dd", {}, p.direction ? (p.direction === "in" ? "In" : "Out") : "—"),
      h("dt", {}, "Vehicle"), h("dd", {}, p.vehicle_type ? cap(p.vehicle_type) : "—"), h("dt", {}, "Confidence"), h("dd", {}, p.confidence != null ? p.confidence + "%" : "—"),
      p.dwell_seconds != null ? [h("dt", {}, "Time inside"), h("dd", {}, Math.round(p.dwell_seconds / 60) + " min")] : null),
    p.review_status === "needs_review" ? note("The reader was not sure about this plate.", "warn") : null);
  onSheetClose(() => { if (url) URL.revokeObjectURL(url); });
}

// ---------------------------------------------------------------------------------------------------------- Alerts
export function alertSheet(X, a) {
  const done = !!a.acknowledged_at;
  const btn = h("button", { class: "btn", type: "button", disabled: done }, done ? "Already seen" : "Mark as seen");
  btn.onclick = async () => { btn.disabled = true; try { await X.api.post(`/v1/alerts/${a.id}/acknowledge`); a.acknowledged_at = new Date().toISOString(); closeSheet(); toast("Marked as seen."); X.refresh(); } catch (e) { toast(errText(e), true); btn.disabled = false; } };
  openSheet(a.title, h("p", {}, a.body || ""), h("dl", { class: "dl" }, h("dt", {}, "When"), h("dd", {}, fmtDateTime(a.created_at)), h("dt", {}, "Camera"), h("dd", {}, a.camera_name || "—"),
    h("dt", {}, "Messages"), h("dd", {}, `${a.messages_sent} sent${a.messages_failed ? `, ${a.messages_failed} failed` : ""}`)), h("div", { class: "acts" }, btn));
}

async function alerts(X) {
  const { api } = X;
  const [list, types] = await Promise.all([api.get("/v1/alerts?limit=50"), api.get("/v1/alert-types")]);
  const out = [title("Alerts", list.filter((a) => !a.acknowledged_at).length ? `${list.filter((a) => !a.acknowledged_at).length} to look at` : "Nothing needs your attention")];
  out.push(list.length ? h("div", { class: "card" }, list.map((a) => swipeRow(X, a))) : empty("bell", "No alerts yet", "When something needs your attention, such as a blocked vehicle or an offline camera, it shows up here."));
  out.push(h("div", { class: "gh" }, "Alert types"), h("div", { class: "group" }, types.map((t) => h("div", { class: "gi static" },
    h("span", { class: "ico " + (t.severity === "critical" ? "crit" : t.severity === "warning" ? "warn" : "") }, ic("bell")), h("div", {}, h("b", {}, t.title), h("small", {}, t.description)),
    toggle(t.enabled, async (i) => {
      if (!X.isAdmin) { i.checked = t.enabled; return toast("Only an administrator can change alerts.", true); }
      try { const r = await api.put("/v1/alert-types/" + t.type, { enabled: i.checked, params: t.params, channels: t.channels, recipient_ids: t.recipient_ids, quiet_start: t.quiet_start, quiet_end: t.quiet_end }); t.enabled = i.checked; toast(r.warnings?.[0] || (i.checked ? "Alert turned on." : "Alert turned off.")); }
      catch (e) { i.checked = t.enabled; toast(errText(e), true); }
    }, t.title)))));
  out.push(h("p", { class: "sub" }, "Who gets messages for each alert is set up by Jas iVision support for now."));
  return out;
}

/** An alert row you can swipe left to mark as seen, or tap to open. */
function swipeRow(X, a) {
  const kind = a.severity === "critical" ? "crit" : a.severity === "warning" ? "warn" : "ok";
  const row = h("div", { class: "row" + (a.acknowledged_at ? " seen" : "") }, h("span", { class: "ico " + kind }, ic("bell")), h("div", {}, h("b", {}, a.title), h("small", {}, a.body || a.camera_name || "")), h("time", {}, ago(a.created_at)));
  const wrap = h("div", { class: "swipe" }, h("div", { class: "act", "aria-hidden": "true" }, "Seen"), row);
  let x0 = null, dx = 0, moved = false;
  row.addEventListener("pointerdown", (e) => { x0 = e.clientX; dx = 0; moved = false; });
  row.addEventListener("pointermove", (e) => { if (x0 === null) return; dx = Math.min(0, e.clientX - x0); if (Math.abs(dx) > 6) moved = true; row.style.transform = `translateX(${Math.max(dx, -110)}px)`; });
  const end = async () => {
    if (x0 === null) return; x0 = null;
    if (dx < -70 && !a.acknowledged_at) {
      try { await X.api.post(`/v1/alerts/${a.id}/acknowledge`); a.acknowledged_at = new Date().toISOString(); row.classList.add("seen"); toast("Marked as seen."); } catch (e) { toast(errText(e), true); }
    }
    row.style.transform = "";
  };
  row.addEventListener("pointerup", end); row.addEventListener("pointercancel", end);
  row.addEventListener("click", () => { if (!moved) alertSheet(X, a); });
  return wrap;
}

export const screens = { today, cameras, cameraPage, activity, alerts, ...recorderScreens };
export { alertRow, camTile };
