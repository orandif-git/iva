// Jas iVision Control Center: the console Jas iVision's own staff use to set up and look after every customer.
// One page of script, one registry of screens, every number comes from the admin API (/admin/v1). There are no inline styles: the page's security policy forbids them.
import { Api } from "/js/lib.js";

const api = new Api({ refreshUrl: "/admin/v1/auth/refresh", key: "gi_staff_rt", storage: sessionStorage, onSignedOut: () => location.replace("/") });
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const TZ = "Asia/Kolkata";

/* ------------------------------------------------------------------------------------------------ small helpers */
const todayIST = () => new Date(new Date().toLocaleString("en-US", { timeZone: TZ }));
const daysUntil = (iso) => { if (!iso) return null; const [y, m, d] = iso.split("-").map(Number); const t = todayIST(); return Math.round((Date.UTC(y, m - 1, d) - Date.UTC(t.getFullYear(), t.getMonth(), t.getDate())) / 864e5); };
const fmtDate = (iso) => iso ? new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "No end date";
const fmtTime = (iso) => { if (!iso) return "Never"; const d = new Date(iso); const same = d.toDateString() === new Date().toDateString(); return same ? d.toLocaleTimeString("en-IN", { timeZone: TZ, hour: "numeric", minute: "2-digit", hour12: true }) : d.toLocaleDateString("en-GB", { timeZone: TZ, day: "numeric", month: "short" }); };
const plusDays = (n) => { const d = todayIST(); d.setDate(d.getDate() + n); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const langName = (l) => ({ en: "English", ta: "Tamil", "en+ta": "English and Tamil" }[l] || l);
const humanize = (k) => k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const errText = (e) => e?.message || "Something went wrong. Try again.";
const toast = (m, bad = false) => { const t = document.createElement("div"); t.className = "toast" + (bad ? " bad" : ""); t.textContent = m; t.setAttribute("role", bad ? "alert" : "status"); document.body.appendChild(t); setTimeout(() => t.remove(), bad ? 5000 : 2400); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const IC = {
  users: '<path d="M16 19v-1.5a3.5 3.5 0 0 0-3.5-3.5h-5A3.5 3.5 0 0 0 4 17.5V19M10 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM20 19v-1.4a3.5 3.5 0 0 0-2.6-3.4M15.5 4.2a3.5 3.5 0 0 1 0 6.6"/>',
  chat: '<path d="M20 12a8 8 0 0 1-11.6 7.1L4 20l1-3.9A8 8 0 1 1 20 12z"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  grid: '<rect x="4" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
  filter: '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
  plus: '<path d="M12 5v14M5 12h14"/>', clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4-4"/>', cam: '<path d="M3.5 8.5A1.5 1.5 0 0 1 5 7h9a1.5 1.5 0 0 1 1.5 1.5v7A1.5 1.5 0 0 1 14 17H5a1.5 1.5 0 0 1-1.5-1.5zM15.5 11l5-3v8l-5-3"/>',
  rec: '<rect x="3.5" y="9" width="17" height="7" rx="2"/><path d="M7 12.5h.01M10 12.5h.01M17 12.5h.01M8 19h8"/>',
  send: '<path d="M21 4L10.5 14.5M21 4l-6.5 17-4-6.5L4 10.5z"/>', bell: '<path d="M6 16.5V11a6 6 0 1 1 12 0v5.5l1.5 2h-15zM10 20.5a2 2 0 0 0 4 0"/>',
  shield: '<path d="M12 3.5l7 2.8v5.4c0 4.2-2.8 7.4-7 8.8-4.2-1.4-7-4.6-7-8.8V6.3z"/><path d="M9 12l2.2 2.2L15.2 10"/>',
  log: '<path d="M6 4h9l3 3v13H6zM9 11h6M9 15h6"/>', store: '<path d="M4 9l1.5-4.5h13L20 9M4 9v10.5h16V9M4 9a2.7 2.7 0 0 0 5.3 0 2.7 2.7 0 0 0 5.4 0A2.7 2.7 0 0 0 20 9M10 19.5v-5h4v5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>', back: '<path d="M15 5l-7 7 7 7"/>', chev: '<path d="M9 6l6 6-6 6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6L7 7M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/>', play: '<path d="M8 5.5v13l11-6.5z"/>', lock: '<rect x="5" y="10.5" width="14" height="9" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
  trash: '<path d="M5 7h14M10 7V4.5h4V7M7 7l1 12.5h8L17 7"/>', pause: '<path d="M9 5.5v13M15 5.5v13"/>', pulse: '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
  wa: '<path d="M12 3.5a8.5 8.5 0 0 0-7.3 12.8L3.5 20.5l4.3-1.1A8.5 8.5 0 1 0 12 3.5z"/><path d="M9 8.5c0 3.5 3 6.5 6.5 6.5l1-1.6-2-1-1 .8c-1-.4-2-1.4-2.4-2.4l.8-1-1-2z"/>',
  photo: '<rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="M4 16l4.5-4 4 3.5 3-2.5L20 16"/>', area: '<path d="M5 8l5-3.5 9 4-2 9-8 2.5z"/>',
  camera2: '<path d="M4 8h3l1.5-2h7L17 8h3v10H4zM12 16.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z"/>', out: '<path d="M9 5H5v14h4M15 8l4 4-4 4M19 12H9"/>'
};
const ico = (n, s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${IC[n]}</svg>`;
const sw = (id, on, dis, act) => `<label class="sw"><input type="checkbox" id="${id}" ${on ? "checked" : ""} ${dis ? "disabled" : ""} ${act ? `data-act="${act}"` : ""}><i></i><span class="sr">Switch</span></label>`;

/* ------------------------------------------------------------------------------------------------ what the events are (the catalogue comes from the server; this only adds how the wizard treats each one) */
const REC_LEVEL = new Set(["recorder_offline", "connection_unstable", "clock_wrong", "back_to_normal", "recorder_restarted", "hardware_fault", "storage_full", "disk_wear", "retention_low", "disk_fault", "recording_stopped"]);
const HIDDEN = new Set(["daily_summary", "weekly_summary", "service_ended"]);                    // set in "Summaries and end date", not in the event wizard
const AREA_KIND = { people_in_area: "area_count", queue_long: "queue_area", phone_use: "staff_area" };
const CARRIES = { view_changed: "image", picture_poor: "image", crowding: "image", after_hours: "image", people_in_area: "image", queue_long: "image", phone_use: "image", blocked_plate: "clip", vehicle_overstay: "image", low_confidence_plate: "image", daily_summary: "direct", weekly_summary: "direct" };
const MEDIA = { none: ["Text only", "mute"], image: ["Picture", "ai"], clip: ["Picture + video", "brand"], direct: ["Direct text", "mute"] };
const carries = (t) => CARRIES[t] || "none";
const PARAM = { people: ["More than", "people"], picture_minutes: ["Picture at most every", "min"], grace_minutes: ["Wait after closing", "min"], drops: ["Drops in a day", "times"], minutes_out: ["Clock is out by", "min"], temperature: ["Too hot above", "°C"],
  percent: ["Health left below", "%"], days: ["Days of video wanted", "days"], hours: ["For", "hours"], max_minutes: ["Stays longer than", "min"] };
const AREA_FIELDS = { area_count: [["limit", "More than", "people", 5], ["minutes", "For", "min", 2]], queue_area: [["length", "Queue longer than", "people", 6], ["wait_minutes", "Or wait longer than", "min", 10], ["minutes", "For", "min", 2]], staff_area: [["seconds", "For more than", "sec", 20]] };
const SHORT = ["customer", "camera", "time", "details", "area", "people", "plate", "minutes", "recorder", "image_url", "clip_url"];

/* ------------------------------------------------------------------------------------------------ state */
const S = { route: "overview", me: null, role: "", cid: null, section: "people", step: "shop", q: "", filter: "all", view: "list", logChip: "all", setup: "tpl", tq: "", pop: false, estep: "event",
  customers: [], form: null, ev: null, cur: null, data: {}, plans: [] };
const can = (k) => ({ newCustomer: ["owner", "admin", "onboarding"], expiry: ["owner", "admin"], del: ["owner"], pause: ["owner", "admin", "support"], templates: ["owner", "admin"], team: ["owner", "admin"], wa: ["owner"], events: ["owner", "admin", "onboarding"] }[k] || []).includes(S.role);
const ROLES = { owner: ["Owner", "Everything, including the WhatsApp connection and console users"], admin: ["Admin", "Everything except the WhatsApp key and making Owners"], onboarding: ["Onboarding", "Adds new customers and sets them up"], support: ["Support", "Looks after live customers: pause, fix people and hours"] };
const PERMS = [["See all customers and the message log", "oaos"], ["Add a new customer and set it up", "oao-"], ["Edit people, hours and cameras", "oaos"], ["Add and change events", "oao-"], ["Pause or resume a customer", "oa-s"], ["Change the end date", "oa--"], ["Delete a customer", "o---"],
  ["WhatsApp templates", "oa--"], ["Console users", "oa--"], ["WhatsApp connection and key", "o---"]];

/* ------------------------------------------------------------------------------------------------ customers */
const mapCustomer = (t) => ({ id: t.id, name: t.name, place: t.city || "", cams: t.cameras_in_use, recs: t.recorders_total, recsOn: t.recorders_online, lang: t.msg_language, people: t.people || "", last: t.last_message_at, endsOn: t.ends_on,
  days: daysUntil(t.ends_on), status: t.status, reason: t.suspended_reason || "" });
const ended = (c) => c.status === "suspended" && c.reason.startsWith("Service ended");
const issueOf = (c) => c.recs > 0 && c.recsOn < c.recs ? `${c.recs - c.recsOn === c.recs ? "Recorder" : "A recorder"} offline` : "";
const statusOf = (c) => c.status === "suspended" ? (ended(c) ? ["Inactive", "crit"] : ["Paused", "mute"]) : c.days !== null && c.days < 0 ? ["Inactive", "crit"] : c.days !== null && c.days <= 7 ? ["Ending soon", "warn"] : issueOf(c) ? ["Needs a look", "warn"] : ["Active", "ok"];
const STATUSES = ["Active", "Needs a look", "Ending soon", "Paused", "Inactive"];
const cust = (id) => S.customers.find((c) => c.id === id);
async function loadCustomers() { const r = await api.get("/admin/v1/tenants?limit=200"); S.customers = r.items.map(mapCustomer); }

/* ------------------------------------------------------------------------------------------------ shell */
const AREAS = [["overview", "Overview", "grid"], ["customers", "Customers", "users"], ["messages", "Messages", "chat"], ["setup", "Setup", "gear"]];
const areaOf = () => ["customers", "customer", "new", "event"].includes(S.route) ? "customers" : S.route;
function shell() {
  $("#areas").innerHTML = AREAS.map((a) => `<button class="area" type="button" data-go="${a[0]}" ${areaOf() === a[0] ? 'aria-current="page"' : ""}>${ico(a[2], 19)}<span>${a[1]}</span></button>`).join("");
  const rails = { overview: [["overview", "pulse", "Overview"]], customers: [["customers", "list", "All customers"], ["new", "plus", "New customer", !can("newCustomer")]], messages: [["messages", "log", "Message log"]], setup: [["setup", "gear", "Setup"]] }[areaOf()];
  $("#rail").innerHTML = rails.map((r) => `<button class="rb" type="button" title="${r[2]}${r[3] ? " (not for your role)" : ""}" data-go="${r[0]}" ${r[3] ? "disabled" : ""} ${S.route === r[0] || (r[0] === "customers" && ["customer", "event"].includes(S.route)) ? 'aria-current="page"' : ""} aria-label="${r[2]}">${ico(r[1], 19)}</button>`).join("");
  $("#theme").innerHTML = ico(document.documentElement.dataset.theme === "dark" ? "sun" : "moon", 19);
  $("#signout").innerHTML = ico("out", 18);
  $("#av").textContent = (S.me?.name || "?").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  $("#role").textContent = ROLES[S.role]?.[0] || "";
  side();
}
const NSTEPS = [["shop", "Shop", "store"], ["people", "Who gets messages", "users"], ["hours", "Business hours", "clock"], ["cams", "Recorder", "cam"], ["summary", "Summaries and end date", "send"]];
const ESTEPS = [["event", "Event", "bell"], ["camera", "Camera or recorder", "cam"], ["type", "Event type", "list"], ["area", "Mark the area", "area"], ["settings", "Settings", "gear"]];
const needsArea = () => !!(S.ev && S.ev.key && AREA_KIND[S.ev.key]);
const cname = () => cust(S.cid)?.name || "";
function side() {
  const el = $("#side"); let h = "";
  if (S.route === "new") h = `<h3>New customer</h3>` + NSTEPS.map((s, i) => `<button class="si" type="button" data-step="${s[0]}" aria-current="${S.step === s[0]}">${ico(s[2])}${s[1]}<span class="stp ${S.form.done[s[0]] ? "done" : ""}">${S.form.done[s[0]] ? "✓" : i + 1}</span></button>`).join("");
  else if (S.route === "event") h = `<button class="si" type="button" data-go="customer">${ico("back")}${esc(cname())}</button><h3 class="pt8">New event</h3>` + ESTEPS.map((s, i) => { const skip = s[0] === "area" && S.ev.key && !needsArea(); return `<button class="si ${skip ? "dim" : ""}" type="button" data-estep="${s[0]}" aria-current="${S.estep === s[0]}">${ico(s[2])}${s[1]}<span class="stp ${S.ev.done[s[0]] ? "done" : ""}">${skip ? "Skipped" : S.ev.done[s[0]] ? "✓" : i + 1}</span></button>`; }).join("");
  else if (S.route === "customer") h = `<button class="si" type="button" data-go="customers">${ico("back")}All customers</button><h3 class="pt8">${esc(cname())}</h3>` + [["people", "users", "Who gets messages"], ["hours", "clock", "Business hours"], ["cams", "cam", "Recorder and cameras"], ["events", "bell", "Events"], ["summary", "send", "Summaries and end date"], ["log", "log", "Messages sent"]].map((s) => `<button class="si" type="button" data-sec="${s[0]}" aria-current="${S.section === s[0]}">${ico(s[1])}${s[2]}</button>`).join("");
  else if (S.route === "setup") h = `<h3>Setup</h3>` + [["tpl", "chat", "WhatsApp templates", can("templates")], ["team", "shield", "Console users", can("team")], ["wa", "wa", "WhatsApp connection", can("wa")], ["audit", "log", "Activity log", true]].map((s) => `<button class="si ${s[3] ? "" : "dim"}" type="button" data-setup="${s[0]}" aria-current="${S.setup === s[0]}">${ico(s[1])}${s[2]}${s[3] ? "" : `<span class="stp">${ico("lock", 12)}</span>`}</button>`).join("");
  if (h) h += `<div class="foot"><span>Control Center 1.0</span></div>`;
  el.hidden = !h; el.innerHTML = h;
}

/* ------------------------------------------------------------------------------------------------ the picture used for drawing areas */
const SCENE = `<svg viewBox="0 0 640 360" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><defs><linearGradient id="fl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8E8794"/><stop offset="1" stop-color="#6F6874"/></linearGradient><linearGradient id="wl" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#E9EAEE"/><stop offset="1" stop-color="#C9CCD4"/></linearGradient></defs><rect width="640" height="360" fill="url(#wl)"/><polygon points="0,150 640,120 640,360 0,360" fill="url(#fl)"/><rect x="40" y="40" width="190" height="150" fill="#8FB8E6" opacity=".85"/><rect x="40" y="40" width="190" height="150" fill="none" stroke="#2B2F36" stroke-width="6"/><path d="M135 40v150M40 115h190" stroke="#2B2F36" stroke-width="4"/><rect x="420" y="95" width="190" height="95" fill="#D9DCE3"/><ellipse cx="330" cy="250" rx="52" ry="10" fill="#000" opacity=".18"/><rect x="316" y="152" width="28" height="86" rx="10" fill="#22252B"/><circle cx="330" cy="140" r="14" fill="#D8B494"/></svg>`;
function videoFrame(title, pts, drawable, snap) {
  const poly = (pts && pts.length) || drawable ? `<svg class="draw" ${drawable ? 'id="draw"' : ""} viewBox="0 0 640 360" preserveAspectRatio="none">${pts && pts.length ? `<polygon points="${pts.map((p) => p.join(",")).join(" ")}" fill="rgba(0,111,255,.28)" stroke="#2F8BFF" stroke-width="3" stroke-linejoin="round"/>${pts.map((p) => `<circle cx="${p[0]}" cy="${p[1]}" r="6" fill="#fff" stroke="#2F8BFF" stroke-width="3"/>`).join("")}` : ""}</svg>` : "";
  return `<div class="vframe"><div class="vhead">${ico("camera2", 22)}<span>${esc(title)}</span><span class="lk">Live</span><span class="sp"></span>${ico("photo", 20)}${ico("gear", 20)}</div><div class="vpic">${snap ? `<img class="snap" src="${snap}" alt="Camera picture">` : SCENE}${poly}</div></div>`;
}

/* ------------------------------------------------------------------------------------------------ Overview */
async function pageOverview() {
  const [ov, att, msgs, sum] = await Promise.all([api.get("/admin/v1/overview"), api.get("/admin/v1/overview/attention"), api.get("/admin/v1/messaging/notifications?limit=200"), api.get("/admin/v1/messaging/summary")]);
  const cs = S.customers, cnt = (s) => cs.filter((c) => statusOf(c)[0] === s).length;
  const recs = Object.values(ov.recorders || {}).reduce((a, b) => a + b, 0);
  const all24 = Object.values(sum.last_24_hours || {}).reduce((a, ch) => { for (const [k, v] of Object.entries(ch)) { a.total += v; if (k === "failed") a.failed += v; } return a; }, { total: 0, failed: 0 });
  const needs = att.items.filter((i) => ["recorder_offline", "recorder_never_connected"].includes(i.kind));
  const ending = cs.filter((c) => c.days !== null && c.days <= 30 && c.days >= -30).sort((a, b) => a.days - b.days);
  const hours = Array.from({ length: 24 }, () => ({ n: 0, bad: false })), now = Date.now();
  for (const m of msgs) { const age = Math.floor((now - new Date(m.created_at)) / 36e5); if (age >= 0 && age < 24) { const b = hours[23 - age]; b.n++; if (m.status === "failed") b.bad = true; } }
  const max = Math.max(1, ...hours.map((h) => h.n));
  return `<div class="rise"><div class="title"><h1>Overview</h1><span class="sub">Everything across all customers, right now</span></div>
  <div class="stats"><div class="st"><b>${cs.length}</b><span>Customers</span></div><div class="st"><b>${cnt("Active")}</b><span>Active</span></div><div class="st"><b class="t-warn">${cnt("Needs a look")}</b><span>Need a look</span></div><div class="st"><b>${ov.cameras_in_use}</b><span>Cameras</span></div><div class="st"><b>${recs}</b><span>Recorders</span></div><div class="st"><b>${all24.total}</b><span>Messages in 24 hours</span></div><div class="st"><b class="${all24.failed ? "t-crit" : ""}">${all24.failed}</b><span>Failed to send</span></div></div>
  <div class="cc"><div>
   <div class="pn"><h3>Needs a look</h3>${needs.length ? `<table class="tbl"><thead><tr><th class="dotc"></th><th>Customer</th><th>What is wrong</th></tr></thead><tbody>${needs.map((i) => `<tr><td class="dotc"><span class="dot ${i.severity === "critical" ? "crit" : "warn"}"></span></td><td class="main-c">${esc((i.subtitle || "").split(" · ")[0])}</td><td>${esc(i.title)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">Nothing needs a look.</div>`}</div>
   <div class="pn"><h3>Ending soon <span class="sub light">next 30 days, plus ended in the last 30</span></h3>${ending.length ? `<table class="tbl"><thead><tr><th class="dotc"></th><th>Customer</th><th>Ends on</th><th>Days left</th><th></th></tr></thead><tbody>${ending.map((c) => `<tr><td class="dotc"><span class="dot ${c.days < 0 ? "crit" : c.days <= 7 ? "warn" : ""}"></span></td><td class="main-c"><button class="lnk dark" type="button" data-cust="${c.id}">${esc(c.name)}</button></td><td class="num">${fmtDate(c.endsOn)}</td><td class="num ${c.days < 0 ? "t-crit" : c.days <= 7 ? "t-warn" : ""}">${c.days < 0 ? "Ended" : c.days}</td><td><button class="btn sm" type="button" data-extendc="${c.id}" ${can("expiry") ? "" : "disabled"}>Extend</button></td></tr>`).join("")}</tbody></table>` : `<div class="empty">No end dates in the next 30 days.</div>`}</div>
  </div><div>
   <div class="pn"><h3>Latest messages <button class="lnk" type="button" data-go="messages">See all</button></h3>${msgs.length ? `<table class="tbl"><tbody>${msgs.slice(0, 6).map((m) => `<tr><td class="dotc"><span class="dot ${m.status === "failed" ? "crit" : "ok"}"></span></td><td class="main-c">${esc(m.title || humanize((m.event || "message").split(".").pop()))}<br><span class="sub small">${esc(m.tenant)}</span></td><td class="num sub">${fmtTime(m.created_at)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">No messages yet.</div>`}</div>
   <div class="pn"><h3>Messages in the last 24 hours</h3><div class="pad"><div class="bars" role="img" aria-label="Messages per hour, the last 24 hours">${hours.map((b) => `<i class="${b.bad ? "bad" : ""}" data-h="${Math.max(4, Math.round(b.n / max * 88))}"></i>`).join("")}</div><div class="axis"><span>24 h ago</span><span>18 h</span><span>12 h</span><span>6 h</span><span>Now</span></div></div></div>
  </div></div></div>`;
}

/* ------------------------------------------------------------------------------------------------ Customers */
function pageCustomers() {
  const q = S.q.toLowerCase();
  const list = S.customers.filter((c) => (!q || (c.name + c.place).toLowerCase().includes(q)) && (S.filter === "all" || statusOf(c)[0] === S.filter));
  const body = !list.length ? `<div class="empty">${S.customers.length ? "No customers match. Clear the search or the filter." : "No customers yet. Add the first one."}</div>` : S.view === "list" ?
    `<div class="scroll"><table class="tbl"><thead><tr><th class="dotc"></th><th class="ico">Type</th><th>Name</th><th>Status</th><th>Place</th><th>Cameras</th><th>Messages go to</th><th>Language</th><th>Last message</th><th>Ends on</th></tr></thead><tbody>${list.map((c) => { const st = statusOf(c); return `<tr class="click" data-cust="${c.id}"><td class="dotc"><span class="dot ${st[1] === "mute" ? "" : st[1]}"></span></td><td class="ico"><span class="glyph">${ico("store", 20)}</span></td><td class="main-c">${esc(c.name)}</td><td class="t-${st[1]}">${st[0]}</td><td>${esc(c.place)}</td><td class="num">${c.cams}</td><td>${esc(c.people || "—")}</td><td>${langName(c.lang)}</td><td class="num">${fmtTime(c.last)}</td><td class="num">${fmtDate(c.endsOn)}</td></tr>`; }).join("")}</tbody></table></div>` :
    `<div class="grid">${list.map((c) => { const st = statusOf(c); return `<button class="tile" type="button" data-cust="${c.id}"><div class="pic"><span class="cc2">${c.cams} cameras</span>${ico("store", 30)}</div><div class="tb"><b>${esc(c.name)}</b><span class="t-${st[1]}">${st[0]}</span><span> · ${esc(c.place)}</span></div></button>`; }).join("")}</div>`;
  return `<div class="rise"><div class="bar"><label class="search">${ico("search")}<span class="sr">Search customers</span><input id="q" placeholder="Search" value="${esc(S.q)}"></label>${S.filter !== "all" ? `<span class="tag brand">${S.filter}</span>` : ""}<span class="sp"></span>
   <div class="tools rel"><button class="tool" type="button" data-view="list" aria-pressed="${S.view === "list"}" aria-label="List">${ico("list")}</button><button class="tool" type="button" data-view="grid" aria-pressed="${S.view === "grid"}" aria-label="Grid">${ico("grid")}</button><button class="tool" type="button" data-pop aria-pressed="${S.pop || S.filter !== "all"}" aria-label="Filter">${ico("filter")}</button>
   ${S.pop ? `<div class="pop">${[["all", "All"]].concat(STATUSES.map((s) => [s, s])).map((s) => `<button type="button" data-filter="${s[0]}" aria-pressed="${S.filter === s[0]}">${s[1]}<span class="sub">${s[0] === "all" ? S.customers.length : S.customers.filter((c) => statusOf(c)[0] === s[0]).length}</span></button>`).join("")}</div>` : ""}</div>
   <button class="btn pri" type="button" data-go="new" ${can("newCustomer") ? "" : "disabled title='Owner, Admin or Onboarding only'"}>${ico("plus")}New customer</button></div>${body}</div>`;
}

/* ------------------------------------------------------------------------------------------------ form sections (shared by New customer and a customer's own page) */
function secShop(f) { return `<div class="f"><label for="f-name">Shop name</label><input class="in" id="f-name" data-bind="name" value="${esc(f.name)}" placeholder="For example, Lakshmi Jewellers" autocomplete="off">
  <label for="f-place">Place</label><input class="in" id="f-place" data-bind="place" value="${esc(f.place)}" placeholder="Town or city" autocomplete="off">
  <span class="lb">Message language<small>Used for every message to this shop</small></span><div class="seg" role="group" aria-label="Language">${[["en", "English"], ["ta", "Tamil"], ["en+ta", "English and Tamil"]].map((l) => `<button type="button" data-lang="${l[0]}" aria-pressed="${f.lang === l[0]}">${l[1]}</button>`).join("")}</div></div>`; }
function secPeople(f) { return `<div class="stack"><div class="note">Only the people listed here get messages. By adding a mobile number you confirm this person has agreed to get WhatsApp messages.</div>
  <div class="pr sub"><span>Name</span><span>Mobile</span><span>Alerts</span><span>Summary</span><span></span></div>
  ${f.people.map((p, i) => `<div class="pr"><input class="in" aria-label="Name" data-p="${i}.n" value="${esc(p.n)}" placeholder="Name"><input class="in" aria-label="Mobile number" data-p="${i}.m" value="${esc(p.m)}" placeholder="+91 98765 43210" inputmode="tel"><label class="chk"><input type="checkbox" data-pc="${i}.a" ${p.a ? "checked" : ""}>Alerts</label><label class="chk"><input type="checkbox" data-pc="${i}.s" ${p.s ? "checked" : ""}>Summary</label><button class="ibtn" type="button" data-delp="${i}" aria-label="Remove person" ${f.people.length < 2 && !f._id ? "disabled" : ""}>${ico("trash")}</button></div>`).join("")}
  <div><button class="btn sm" type="button" data-addp>${ico("plus", 15)}Add a person</button></div></div>`; }
function secHours(f) { return `<div class="stack"><div class="f f-narrow"><span class="lb">Opening hours<small>After-hours and counting events follow these</small></span><div class="seg" role="group" aria-label="Hours type">${[["always", "Always open"], ["set", "Set the hours"]].map((m) => `<button type="button" data-hmode="${m[0]}" aria-pressed="${f.hmode === m[0]}">${m[1]}</button>`).join("")}</div></div>
  ${f.hmode === "always" ? `<div class="note">The shop counts as open at all times, so there are no after-hours events.</div>` : `<div class="hours">${f.hours.map((h, i) => `<div class="hrow"><b>${DAYS[i]}</b>${sw("hd" + i, h.open, false, "hd:" + i)}${h.open ? `<input class="in time" type="time" aria-label="${DAYS[i]} opens" data-ht="${i}.from" value="${h.from}"><span class="sub mid">to</span><input class="in time" type="time" aria-label="${DAYS[i]} closes" data-ht="${i}.to" value="${h.to}">` : `<span class="closed">Closed</span>`}</div>`).join("")}</div>
  <div><button class="lnk" type="button" data-copyh>Copy Monday's hours to every open day</button></div><small class="sub">Type the time or use the clock. All times are India time.</small>`}</div>`; }
function secRecorderNew(f) { return `<div class="stack"><div class="note">Add the recorder now. After you create the customer you get the details to type into the recorder. Cameras are chosen on the customer's page once the recorder has connected.</div>
  <div class="f f-narrow"><span class="lb">Recorder type</span><div class="seg" role="group" aria-label="Recorder type">${[["dahua", "Dahua"], ["none", "Add later"], ["hik", "Hikvision (soon)"]].map((m) => `<button type="button" data-rtype="${m[0]}" aria-pressed="${f.rtype === m[0]}" ${m[0] === "hik" ? "disabled" : ""}>${m[1]}</button>`).join("")}</div>
  ${f.rtype === "dahua" ? `<label for="f-rname">Recorder name<small>For example, Main shop</small></label><input class="in" id="f-rname" data-bind="rname" value="${esc(f.rname)}" placeholder="Optional" autocomplete="off">` : ""}</div></div>`; }
function secSummary(f, c) { return `<div class="f"><span class="lb">Daily summary<small>One message each morning</small></span><div class="row">${sw("f-daily", f.daily, false, "daily")}<label class="sr" for="f-dat">Daily time</label><input class="in time" id="f-dat" type="time" data-bind="dailyAt" value="${f.dailyAt}"></div>
  <span class="lb">Weekly summary<small>Sent once a week, on the day set for all shops</small></span><div class="row">${sw("f-weekly", f.weekly, false, "weekly")}</div>
  <label for="f-exp">Service ends on<small>Everything stops the day after and the shop is told once by WhatsApp</small></label><div class="stack"><input class="in time wide" id="f-exp" type="date" data-bind="expiry" value="${f.expiry}" ${can("expiry") || !c ? "" : "disabled"}>${can("expiry") || !c ? "" : `<span class="sub">${ico("lock", 13)} Owner or Admin can change the end date</span>`}</div></div>`; }

/* ------------------------------------------------------------------------------------------------ New customer */
const blankForm = () => ({ _id: null, name: "", place: "", lang: "en+ta", people: [{ id: null, n: "", m: "", a: true, s: true }], hmode: "set", hours: DAYS.map((d, i) => ({ open: i < 6, from: "09:00", to: "21:00" })), rtype: "dahua", rname: "", daily: true, dailyAt: "08:00", weekly: true, expiry: plusDays(365), done: {} });
function pageNew() {
  const f = S.form, i = NSTEPS.findIndex((s) => s[0] === S.step), last = i === NSTEPS.length - 1;
  const body = { shop: secShop, people: secPeople, hours: secHours, cams: secRecorderNew, summary: secSummary }[S.step](f);
  return `<div class="rise"><div class="title"><h1>${NSTEPS[i][1]}</h1><span class="tag brand">Step ${i + 1} of ${NSTEPS.length}</span></div><div class="form">${body}
   <div class="actions">${last ? `<button class="btn pri" type="button" data-create>Create customer</button>` : `<button class="btn pri" type="button" data-next>Next</button>`}${i ? `<button class="btn" type="button" data-prev>Back</button>` : ""}<button class="btn bare" type="button" data-go="customers">Cancel</button></div></div></div>`;
}
async function createCustomer() {
  const f = S.form;
  if (!f.name.trim()) { S.step = "shop"; render(); return toast("Give the shop a name first.", true); }
  const people = f.people.filter((p) => p.n.trim() || p.m.trim());
  if (people.some((p) => !p.n.trim() || !p.m.trim())) { S.step = "people"; render(); return toast("Each person needs a name and a mobile number.", true); }
  const btn = document.querySelector("[data-create]"); if (btn) btn.disabled = true;
  let t;
  try {
    if (!S.plans.length) S.plans = (await api.get("/admin/v1/plans")).items || (await api.get("/admin/v1/plans"));
    const plan = S.plans.find((p) => p.anpr && !p.alerts_chosen) || S.plans.find((p) => p.anpr) || S.plans[0];       // there is no plan to choose: every customer gets everything
    t = await api.post("/admin/v1/tenants", { name: f.name.trim(), city: f.place.trim() || null, plan_id: plan.id, camera_limit: 64, ends_on: f.expiry || null, msg_language: f.lang });
  } catch (e) { if (btn) btn.disabled = false; return toast(errText(e), true); }
  const base = `/admin/v1/tenants/${t.id}/shop`, problems = []; let creds = null;
  const ids = [];
  for (const p of people) { try { const r = await api.post(base + "/people", { name: p.n.trim(), phone: p.m.trim() }); ids.push([r.id, p]); } catch (e) { problems.push(`${p.n}: ${errText(e)}`); } }
  try { await api.put(base + "/hours", f.hmode === "always" ? { always_open: true, days: [] } : { always_open: false, days: f.hours.map((h) => ({ open: h.open, from: h.open ? h.from : null, to: h.open ? h.to : null })) }); } catch (e) { problems.push("Hours: " + errText(e)); }
  const sumIds = ids.filter((x) => x[1].s).map((x) => x[0]);
  for (const [type, on] of [["daily_summary", f.daily], ["weekly_summary", f.weekly]]) { try { await api.put(base + "/events/" + type, { enabled: on, params: type === "daily_summary" ? { hour: +f.dailyAt.slice(0, 2) } : {}, recipient_ids: sumIds }); } catch (e) { problems.push("Summary: " + errText(e)); } }
  if (f.rtype === "dahua") { try { creds = await api.post(`/admin/v1/tenants/${t.id}/recorders`, { brand: "dahua", name: f.rname.trim() || null, reason: "Added when the customer was set up" }); } catch (e) { problems.push("Recorder: " + errText(e)); } }
  await loadCustomers(); S.cid = t.id; S.form = null; S.section = "events";
  modal(`${mh("Setup complete!")}<p class="sub nm center">${esc(f.name)} is ready.${problems.length ? "" : " Next, add the events this shop should hear about."}</p>
    ${problems.length ? `<div class="note warn">${problems.map(esc).join("<br>")}<br>Open the customer to fix these.</div>` : ""}
    ${creds ? `<div class="creds"><div class="sec-h">Type these into the recorder</div>${credRows(creds)}</div>` : ""}
    <button class="btn pri wide" type="button" data-finish>Manage customer</button>`);
}
const credRows = (c) => Object.entries(c).filter(([k, v]) => v && typeof v !== "object" && !["recorder_id", "protocol"].includes(k)).map(([k, v]) => `<div class="kv"><span>${humanize(k)}</span><span class="sc">${esc(v)}</span></div>`).join("");

/* ------------------------------------------------------------------------------------------------ one customer */
async function loadCurrent(id) {
  const base = `/admin/v1/tenants/${id}`, shop = base + "/shop";
  const [people, hours, events, cams, recs] = await Promise.all([api.get(shop + "/people"), api.get(shop + "/hours"), api.get(shop + "/events"), api.get(shop + "/cameras"), api.get(`/admin/v1/recorders?tenant_id=${id}`)]);
  const c = cust(id);
  const rules = events.filter((e) => e.saved);
  const alertRules = rules.filter((e) => e.enabled && !["daily_summary", "weekly_summary"].includes(e.type)), sumRules = rules.filter((e) => ["daily_summary", "weekly_summary"].includes(e.type));
  const inAlerts = (pid) => !alertRules.length || alertRules.some((e) => e.recipient_ids.includes(pid)), inSummary = (pid) => !sumRules.length || sumRules.some((e) => e.recipient_ids.includes(pid));
  const daily = events.find((e) => e.type === "daily_summary"), weekly = events.find((e) => e.type === "weekly_summary");
  const f = { _id: id, name: c.name, place: c.place, lang: c.lang, people: people.map((p) => ({ id: p.id, n: p.name, m: p.phone || "", a: inAlerts(p.id), s: inSummary(p.id) })),
    hmode: hours.set ? "set" : "always", hours: hours.days.map((d) => ({ open: d.open, from: d.from, to: d.to })), daily: daily ? daily.enabled : true, dailyAt: String(daily?.params?.hour ?? 8).padStart(2, "0") + ":00", weekly: weekly ? weekly.enabled : true, expiry: c.endsOn || "", done: {} };
  S.cur = { id, f, people, events, cams, recs, channels: {} };
}
async function pageCustomer() {
  if (!S.cur || S.cur.id !== S.cid) await loadCurrent(S.cid);
  const c = cust(S.cid), st = statusOf(c), f = S.cur.f;
  const titles = { people: "Who gets messages", hours: "Business hours", cams: "Recorder and cameras", events: "Events", summary: "Summaries and end date", log: "Messages sent" };
  let body;
  if (S.section === "events") body = secEvents();
  else if (S.section === "cams") body = secCams();
  else if (S.section === "log") { const log = await api.get(`/admin/v1/messaging/notifications?tenant_id=${S.cid}&limit=30`); body = logTable(log, false); }
  else body = { people: secPeople, hours: secHours, summary: secSummary }[S.section](f, c) + `<div class="actions"><button class="btn pri" type="button" data-save>Save changes</button></div>`;
  return `<div class="rise"><div class="title"><h1>${esc(c.name)}</h1><span class="t-${st[1]} bold"><span class="dot ${st[1] === "mute" ? "" : st[1]}"></span> ${st[0]}</span><span class="sub">${esc(c.place)} · ends ${fmtDate(c.endsOn)}</span><span class="sp"></span>
   <button class="btn sm" type="button" data-testmsg>${ico("send", 15)}Send test message</button><button class="btn sm" type="button" data-pausec ${can("pause") ? "" : "disabled title='Not for your role'"}>${ico("pause", 15)}${c.status === "suspended" ? "Resume" : "Pause"}</button><button class="btn sm" type="button" data-extend ${can("expiry") ? "" : "disabled title='Owner or Admin only'"}>${ico("clock", 15)}Change end date</button><button class="btn sm danger" type="button" data-delc ${can("del") ? "" : "disabled title='Owner only'"}>${ico("trash", 15)}Delete</button></div>
   <h2 class="h2">${titles[S.section]}</h2><div class="form">${body}</div></div>`;
}
function secEvents() {
  const list = S.cur.events.filter((e) => e.saved && !HIDDEN.has(e.type)), names = (ids) => ids.map((i) => S.cur.people.find((p) => p.id === i)?.name).filter(Boolean).join(", ") || "Nobody yet";
  return `<div class="stack"><div class="bar flush"><span class="sub">What this shop is told about. Only admins can change these.</span><span class="sp"></span><button class="btn pri" type="button" data-addev ${can("events") ? "" : "disabled title='Not for your role'"}>${ico("plus")}Add event</button></div>
  ${list.length ? `<div class="scroll"><table class="tbl"><thead><tr><th class="dotc"></th><th>Event</th><th>Event type</th><th>Sends</th><th>Goes to</th><th>On</th></tr></thead><tbody>${list.map((e) => { const m = MEDIA[carries(e.type)]; return `<tr><td class="dotc"><span class="dot ${e.enabled ? "ok" : ""}"></span></td><td class="main-c">${esc(e.label || e.title)}</td><td>${esc(e.title)}</td><td><span class="tag ${m[1]}">${m[0]}</span></td><td>${esc(names(e.recipient_ids))}</td><td>${sw("evon-" + e.type, e.enabled, !can("events"), "evon:" + e.type)}</td></tr>`; }).join("")}</tbody></table></div>` : `<div class="empty">No events yet. Add the first one.</div>`}</div>`;
}
function secCams() {
  const { recs, cams, channels } = S.cur;
  return `<div class="stack"><div class="bar flush"><span class="sub">The recorder sends its cameras once it has connected to our server.</span><span class="sp"></span><button class="btn pri" type="button" data-addrec ${can("events") ? "" : "disabled title='Not for your role'"}>${ico("plus")}Add recorder</button></div>
  ${recs.length ? `<div class="scroll"><table class="tbl"><thead><tr><th class="dotc"></th><th>Recorder</th><th>Brand</th><th>Status</th><th>Last seen</th><th></th></tr></thead><tbody>${recs.map((r) => `<tr><td class="dotc"><span class="dot ${r.status === "online" ? "ok" : r.status === "offline" ? "crit" : ""}"></span></td><td class="main-c">${esc(r.name || r.device_id)}</td><td>${esc(humanize(r.brand || ""))}</td><td class="${r.status === "online" ? "t-ok" : r.status === "offline" ? "t-crit" : "t-mute"}">${r.status === "never" ? "Not connected yet" : humanize(r.status)}</td><td class="num">${fmtTime(r.last_heartbeat_at)}</td><td><button class="btn sm" type="button" data-findcams="${r.id}">Find cameras</button> <button class="btn sm" type="button" data-showconn="${r.id}">Details</button></td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">No recorder yet. Add one, then type its details into the recorder.</div>`}
  ${Object.entries(channels).map(([rid, list]) => `<div class="sec-h">Cameras found on ${esc(recs.find((r) => r.id === rid)?.name || recs.find((r) => r.id === rid)?.device_id || "the recorder")}</div>${list.length ? `<div class="cams">${list.map((ch, i) => `<div class="cam"><div class="pic" data-pic="${rid}|${ch.channel_no}">${ico("cam", 24)}<span class="picmsg">Loading picture…</span></div><div class="cb">${ch.enabled_as_camera ? `<span class="tag ok">Already added</span>` : `<label class="chk"><input type="checkbox" data-ch="${rid}.${i}" ${ch.on ? "checked" : ""}>Watch this camera</label><input class="in" aria-label="Camera name" data-chn="${rid}.${i}" value="${esc(ch.name)}">`}</div></div>`).join("")}</div>${list.some((x) => !x.enabled_as_camera) ? `<div><button class="btn pri" type="button" data-addcams="${rid}">Add the ticked cameras</button></div>` : ""}` : `<div class="note">The recorder has not sent its cameras yet. Check its details, then try again in a minute.</div>`}`).join("")}
  <div class="sec-h">Cameras</div>${cams.length ? `<div class="scroll"><table class="tbl"><thead><tr><th class="dotc"></th><th>Name</th><th>Counts people</th><th>Reads plates</th><th>Watching</th><th></th></tr></thead><tbody>${cams.map((c) => `<tr><td class="dotc"><span class="dot ${c.enabled ? "ok" : ""}"></span></td><td class="main-c"><input class="in bare-in" aria-label="Camera name" data-cam-name="${c.id}" value="${esc(c.name)}" ${can("events") ? "" : "disabled"}></td><td>${sw("camfoot-" + c.id, c.use_footfall, !can("events"), "camfoot:" + c.id)}</td><td>${sw("camplate-" + c.id, c.use_anpr, !can("events"), "camplate:" + c.id)}</td><td>${sw("camon-" + c.id, c.enabled, !can("events"), "camon:" + c.id)}</td><td><button class="btn sm" type="button" data-campic="${c.id}">See picture</button></td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">No cameras added yet.</div>`}</div>`;
}
function logTable(rows, withCustomer) {
  const chips = [["all", "All"], ["Sent", "Sent"], ["Failed", "Failed"], ["Waiting", "Waiting"]];
  const kind = (m) => ["failed"].includes(m.status) ? "Failed" : ["queued"].includes(m.status) ? "Waiting" : ["suppressed", "cancelled"].includes(m.status) ? "Held back" : "Sent";
  const shown = withCustomer ? rows.filter((m) => S.logChip === "all" || kind(m) === S.logChip) : rows;
  return `${withCustomer ? `<div class="bar"><div class="seg" role="group" aria-label="Filter">${chips.map((c) => `<button type="button" data-lchip="${c[0]}" aria-pressed="${S.logChip === c[0]}">${c[1]}</button>`).join("")}</div></div>` : ""}
  ${shown.length ? `<div class="scroll"><table class="tbl"><thead><tr><th class="dotc"></th><th>Time</th>${withCustomer ? "<th>Customer</th>" : ""}<th>Message</th><th>To</th><th>Result</th><th></th></tr></thead><tbody>${shown.map((m) => `<tr><td class="dotc"><span class="dot ${m.status === "failed" ? "crit" : ["queued"].includes(m.status) ? "warn" : "ok"}"></span></td><td class="num">${fmtTime(m.created_at)}</td>${withCustomer ? `<td class="main-c">${esc(m.tenant)}</td>` : ""}<td>${esc(m.title || humanize((m.event || "message").split(".").pop()))}</td><td>${esc(m.recipient || "")}</td><td class="${m.status === "failed" ? "t-crit" : "t-ok"}" title="${esc(m.error || "")}">${kind(m)}</td><td>${m.status === "failed" && can("templates") ? `<button class="btn sm" type="button" data-resend="${m.id}">Send again</button>` : ""}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">No messages yet.</div>`}`;
}
async function saveSection() {
  const f = S.cur.f, base = `/admin/v1/tenants/${S.cid}/shop`;
  try {
    if (S.section === "people") {
      const have = new Map(S.cur.people.map((p) => [p.id, p]));
      for (const p of f.people) {
        if (!p.n.trim() && !p.m.trim()) continue;
        if (!p.n.trim() || !p.m.trim()) throw new Error("Each person needs a name and a mobile number.");
        if (p.id) { have.delete(p.id); const o = S.cur.people.find((x) => x.id === p.id); if (o.name !== p.n.trim() || (o.phone || "") !== p.m.trim()) await api.patch(`${base}/people/${p.id}`, { name: p.n.trim(), phone: p.m.trim() }); }
        else { const r = await api.post(base + "/people", { name: p.n.trim(), phone: p.m.trim() }); p.id = r.id; }
      }
      for (const id of have.keys()) await api.del(`${base}/people/${id}`);
      await syncRecipients(f);
    } else if (S.section === "hours") {
      await api.put(base + "/hours", f.hmode === "always" ? { always_open: true, days: [] } : { always_open: false, days: f.hours.map((h) => ({ open: h.open, from: h.open ? h.from : null, to: h.open ? h.to : null })) });
    } else if (S.section === "summary") {
      const ids = f.people.filter((p) => p.id && p.s).map((p) => p.id);
      await api.put(base + "/events/daily_summary", { enabled: f.daily, params: { hour: +f.dailyAt.slice(0, 2) }, recipient_ids: ids });
      await api.put(base + "/events/weekly_summary", { enabled: f.weekly, params: {}, recipient_ids: ids });
      if (can("expiry") && f.expiry !== (cust(S.cid).endsOn || "")) { await api.patch(`/admin/v1/tenants/${S.cid}`, { ends_on: f.expiry || null }); await loadCustomers(); }
    }
    await loadCurrent(S.cid); toast("Saved"); render();
  } catch (e) { toast(errText(e), true); }
}
async function syncRecipients(f) {
  const base = `/admin/v1/tenants/${S.cid}/shop`, ev = await api.get(base + "/events");
  const alertIds = f.people.filter((p) => p.id && p.a).map((p) => p.id), sumIds = f.people.filter((p) => p.id && p.s).map((p) => p.id);
  for (const e of ev.filter((x) => x.saved)) {
    const ids = ["daily_summary", "weekly_summary"].includes(e.type) ? sumIds : alertIds;
    await api.put(`${base}/events/${e.type}`, { label: e.label, enabled: e.enabled, params: e.params, recipient_ids: ids, quiet_start: e.quiet_start, quiet_end: e.quiet_end });
  }
}

/* ------------------------------------------------------------------------------------------------ the event wizard */
const newEv = () => ({ name: "", to: S.cur.people.map((p) => p.id), target: null, key: null, level: null, pts: [], v: {}, notice: false, clip: false, snap: null, done: {} });
async function eventCatalog() { return S.cur.events.filter((e) => !HIDDEN.has(e.type)); }
function pageEvent() {
  const e = S.ev, i = ESTEPS.findIndex((s) => s[0] === S.estep); let body = "";
  if (S.estep === "event") body = `<div class="f"><label for="e-name">Event name<small>Shown in your list. You can leave the suggestion.</small></label><input class="in" id="e-name" data-ebind="name" value="${esc(e.name)}" placeholder="For example, Queue at the counter" autocomplete="off">
    <span class="lb">Send to<small>Only these people get this event</small></span><div class="stack">${S.cur.people.length ? S.cur.people.map((p) => `<label class="chk dark"><input type="checkbox" data-eto="${p.id}" ${e.to.includes(p.id) ? "checked" : ""}>${esc(p.name)} <span class="sub">${esc(p.phone || "")}</span></label>`).join("") : `<div class="note warn">This shop has nobody to message yet. Add people first.</div>`}</div></div>`;
  else if (S.estep === "camera") body = `<div class="note">Pick one camera for a picture event, or the recorder for a system event such as "Recorder offline".</div><div class="pick scroll"><table class="tbl"><thead><tr><th class="dotc"></th><th></th><th>Name</th><th>Kind</th><th>Status</th></tr></thead><tbody>${S.cur.recs.map((r) => ["rec", r.id, r.name || r.device_id, r.status === "online"]).concat(S.cur.cams.map((c) => ["cam", c.id, c.name, c.stream_state === "live" || c.enabled])).map((t) => `<tr data-etarget="${t[0]}|${t[1]}"><td class="dotc"><span class="dot ${t[3] ? "ok" : ""}"></span></td><td class="radio"><input type="radio" name="tg" aria-label="${esc(t[2])}" ${e.target?.id === t[1] ? "checked" : ""}></td><td class="main-c">${esc(t[2])}</td><td>${t[0] === "rec" ? "Recorder (whole system)" : "Camera"}</td><td class="${t[3] ? "t-ok" : "t-mute"}">${t[3] ? "Online" : "Not connected"}</td></tr>`).join("")}</tbody></table></div>${!S.cur.recs.length && !S.cur.cams.length ? `<div class="note warn">Add a recorder and cameras first (Recorder and cameras).</div>` : ""}`;
  else if (S.estep === "type") { const opts = S.cur.events.filter((x) => !HIDDEN.has(x.type) && (REC_LEVEL.has(x.type) ? "rec" : "cam") === e.level), groups = [...new Set(opts.map((x) => x.group))];
    body = e.level ? `<div class="pick scroll"><table class="tbl"><tbody>${groups.map((g) => `<tr><td colspan="4" class="sec-h grp">${g}</td></tr>` + opts.filter((x) => x.group === g).map((x) => `<tr data-etype="${x.type}"><td class="radio"><input type="radio" name="ty" aria-label="${esc(x.title)}" ${e.key === x.type ? "checked" : ""}></td><td class="main-c">${esc(x.title)}${x.ai ? ' <span class="tag ai">Smart</span>' : ""}<br><span class="sub small wrap">${esc(x.description)}</span></td><td><span class="tag ${MEDIA[carries(x.type)][1]}">${MEDIA[carries(x.type)][0]}</span></td><td>${AREA_KIND[x.type] ? '<span class="tag">Marks an area</span>' : ""}</td></tr>`).join("")).join("")}</tbody></table></div>` : `<div class="empty">Choose a camera or recorder first.</div>`; }
  else if (S.estep === "area") body = needsArea() ? `${videoFrame((e.target?.name || "") + " · " + cname(), e.pts, true, e.snap)}<div class="vtool"><span class="sub">Click on the picture to add the corners of the area. At least 3 corners.${e.snap ? "" : " The camera's picture is not available yet, so this is a sample view."}</span><span class="grow"></span><button class="btn sm" type="button" data-clearpts>Start again</button></div>` : `<div class="note">This event does not use a marked area, so this step is skipped.</div>`;
  else body = settingsBody(e);
  const order = ESTEPS.map((s) => s[0]).filter((s) => s !== "area" || needsArea() || !e.key), pos = order.indexOf(S.estep), last = pos === order.length - 1;
  const ok = { event: !!S.cur.people.length, camera: !!e.target, type: !!e.key, area: e.pts.length >= 3, settings: true }[S.estep];
  return `<div class="rise"><div class="title"><h1>${ESTEPS[i][1]}</h1><span class="tag brand">Step ${pos + 1} of ${order.length}</span><span class="sub">${esc(cname())}</span></div><div class="form">${body}
   <div class="actions">${last ? `<button class="btn pri" type="button" data-evcreate>Create event</button>` : `<button class="btn pri" type="button" data-evnext ${ok ? "" : "disabled"}>Next</button>`}${pos ? `<button class="btn" type="button" data-evprev>Back</button>` : ""}<button class="btn bare" type="button" data-go="customer">Cancel</button></div></div></div>`;
}
function settingsBody(e) {
  const x = S.cur.events.find((q) => q.type === e.key), kind = AREA_KIND[e.key], rows = [];
  if (kind) for (const [k, label, unit, def] of AREA_FIELDS[kind]) rows.push(`<div class="cfg"><span>${label}</span><input class="in sm" type="number" min="1" aria-label="${label}" data-ev="${k}" value="${e.v[k] ?? def}"><span class="sub">${unit}</span></div>`);
  else for (const [k, s] of Object.entries(x.settings || {})) { if (s.type === "boolean") { rows.push(`<div class="cfg"><span>Include a short video</span><span>${sw("e-clip", e.clip, false, "eclip")}</span><span></span></div>`); continue; } const [label, unit] = PARAM[k] || [humanize(k), ""]; rows.push(`<div class="cfg"><span>${label}</span><input class="in sm" type="number" min="${s.min}" max="${s.max}" aria-label="${label}" data-ev="${k}" value="${e.v[k] ?? s.default}"><span class="sub">${unit}</span></div>`); }
  if (e.key === "phone_use") rows.push(`<label class="chk dark notice"><input type="checkbox" data-enotice ${e.notice ? "checked" : ""}>I have told the staff that this area is watched for phone use.</label>`);
  const m = MEDIA[carries(e.key)];
  return `<div class="stack"><div class="f f-narrow"><span class="lb">${esc(x.title)}<small>${esc(x.description)}</small></span><div class="stack">${rows.join("") || `<div class="sub">No setting needed for this event.</div>`}
    <div class="cfg"><span>Message carries</span><span class="tag ${m[1]}">${m[0]}</span><span></span></div><div class="cfg"><span>Switch on now</span><span>${sw("e-on", e.on !== false, false, "eon")}</span><span></span></div></div></div></div>`;
}
async function loadSnap(e) {
  if (e.snap || e.target?.kind !== "cam") return;
  try { const blob = await api.call("GET", `/admin/v1/tenants/${S.cid}/shop/cameras/${e.target.id}/snapshot`, undefined, { blob: true }); e.snap = URL.createObjectURL(blob); } catch { /* a sample view is shown instead */ }
}
async function createEvent() {
  const e = S.ev, x = S.cur.events.find((q) => q.type === e.key), base = `/admin/v1/tenants/${S.cid}/shop`, kind = AREA_KIND[e.key];
  const btn = document.querySelector("[data-evcreate]"); if (btn) btn.disabled = true;
  try {
    if (kind) {
      if (e.key === "phone_use" && !e.notice) throw new Error("Please confirm that the staff have been told.");
      const existing = await api.get(`${base}/cameras/${e.target.id}/areas`);
      const params = {}; for (const [k, , , def] of AREA_FIELDS[kind]) params[k] = +(e.v[k] ?? def);
      if (kind === "staff_area") params.notice_confirmed = true;
      await api.put(`${base}/cameras/${e.target.id}/areas`, { areas: existing.map((a) => ({ id: a.id, kind: a.kind, name: a.name, points: a.points, params: a.params })).concat([{ kind, name: e.name || x.title, points: e.pts.map((p) => [Math.round(p[0] / 640 * 1000) / 1000, Math.round(p[1] / 360 * 1000) / 1000]), params }]) });
    }
    const params = {}; if (!kind) for (const [k, s] of Object.entries(x.settings || {})) params[k] = s.type === "boolean" ? !!e.clip : +(e.v[k] ?? s.default);
    await api.put(`${base}/events/${e.key}`, { label: e.name || x.title, enabled: e.on !== false, params, recipient_ids: e.to });
  } catch (err) { if (btn) btn.disabled = false; return toast(errText(err), true); }
  await loadCurrent(S.cid);
  modal(`${mh("Setup complete!")}<p class="sub nm center">${esc(e.name || x.title)} is on for ${esc(e.target.name)}.</p>${kind ? videoFrame(e.target.name + " · " + cname(), e.pts, false, e.snap) : ""}<button class="btn pri wide" type="button" data-evdone>Manage events</button>`);
}

/* ------------------------------------------------------------------------------------------------ Messages */
async function pageMessages() { const rows = await api.get("/admin/v1/messaging/notifications?limit=200"); return `<div class="rise">${logTable(rows, true)}</div>`; }

/* ------------------------------------------------------------------------------------------------ Setup */
const lock = (n) => `<div class="empty rise">${ico("lock", 28)}<h2 class="h2 pt8">${n} is for ${n === "WhatsApp connection" ? "the Owner" : "Owner or Admin"}</h2><p>Ask the Owner if you need this changed.</p></div>`;
async function pageSetup() {
  if (S.setup === "tpl") {
    if (!can("templates")) return lock("WhatsApp templates");
    const [cat, wh] = await Promise.all([api.get("/admin/v1/alert-catalog"), api.get("/admin/v1/webhooks")]);
    S.data.wh = wh; S.data.cat = cat.items;
    const q = S.tq.toLowerCase(), byType = Object.fromEntries(wh.items.map((w) => [w.alert_type, w]));
    const rows = cat.items.filter((a) => a.built && (!q || a.title.toLowerCase().includes(q)));
    return `<div class="rise"><div class="bar"><label class="search">${ico("search")}<span class="sr">Search</span><input id="tq" placeholder="Search" value="${esc(S.tq)}"></label><span class="sp"></span><span class="sub">Picture and video links open for <b class="dark">${wh.settings.link_hours} hours</b></span><button class="btn" type="button" data-setupall>Set up all alerts</button></div>
    <div class="scroll"><table class="tbl"><thead><tr><th class="dotc"></th><th class="ico">Type</th><th>Name</th><th>Status</th><th>Template</th><th>Carries</th><th>Values filled in</th><th>Sent</th><th>Failed</th></tr></thead><tbody>${rows.map((a) => { const w = byType[a.type], direct = ["daily_summary", "weekly_summary"].includes(a.type), m = MEDIA[carries(a.type)]; const used = w ? [...new Set([...(w.payload_template || "").matchAll(/\{(\w+)\}/g)].map((x) => x[1]))] : [];
      return `<tr class="click" data-tpl="${a.type}"><td class="dotc"><span class="dot ${direct ? "" : w ? (w.active ? "ok" : "warn") : ""}"></span></td><td class="ico"><span class="glyph">${ico(REC_LEVEL.has(a.type) ? "rec" : "cam", 19)}</span></td><td class="main-c">${esc(a.title)}</td><td class="${direct ? "t-mute" : w ? (w.active ? "t-ok" : "t-warn") : "t-mute"}">${direct ? "Direct send" : w ? (w.active ? "On" : "Switched off") : "Not set up"}</td><td>${direct || !w ? "—" : `<span class="sc">${esc(w.name)}</span>`}</td><td><span class="tag ${m[1]}">${m[0]}</span></td><td>${used.length ? used.map((v) => `<span class="sc">{${v}}</span>`).join(" ") : "—"}</td><td class="num">${w ? w.sent : "—"}</td><td class="num ${w && w.failed ? "t-crit" : ""}">${w ? w.failed : "—"}</td></tr>`; }).join("")}</tbody></table></div></div>`;
  }
  if (S.setup === "team") {
    if (!can("team")) return lock("Console users");
    const st = await api.get("/admin/v1/staff");
    return `<div class="rise"><div class="bar"><span class="sp"></span><button class="btn pri" type="button" data-invite>${ico("plus")}Add a person</button></div>
    <div class="scroll mb22"><table class="tbl"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Last sign-in</th><th></th></tr></thead><tbody>${st.items.map((m) => `<tr><td class="main-c">${esc(m.name)}</td><td>${esc(m.email)}</td><td><span class="tag brand">${ROLES[m.role]?.[0] || humanize(m.role)}</span></td><td class="num">${m.last_login_at ? fmtTime(m.last_login_at) : "Never"}</td><td>${m.id === S.me.id ? "" : `<button class="btn sm" type="button" data-${m.status === "active" ? "disable" : "enable"}="${m.id}">${m.status === "active" ? "Switch off" : "Switch on"}</button> <button class="btn sm" type="button" data-resetlink="${m.id}">New sign-in link</button>`}</td></tr>`).join("")}</tbody></table></div>
    <h2 class="h2">What each role can do</h2><div class="scroll"><table class="tbl matrix"><thead><tr><th></th>${Object.values(ROLES).map((r) => `<th>${r[0]}</th>`).join("")}</tr></thead><tbody>${PERMS.map((p) => `<tr><td>${p[0]}</td>${[0, 1, 2, 3].map((i) => `<td>${"oas".includes(p[1][i]) ? '<span class="tick">✓</span>' : '<span class="cross">—</span>'}</td>`).join("")}</tr>`).join("")}</tbody></table></div></div>`;
  }
  if (S.setup === "wa") {
    if (!can("wa")) return lock("WhatsApp connection");
    const s = await api.get("/admin/v1/summaries"); S.data.sum = s; const c = s.config;
    return `<div class="rise"><div class="title"><h1>WhatsApp connection</h1><span class="${s.key.set && c.url ? "t-ok" : "t-warn"} bold"><span class="dot ${s.key.set && c.url ? "ok" : "warn"}"></span> ${s.key.set && c.url ? "Connected" : "Not finished"}</span></div><div class="form"><div class="f"><label for="w1">Address</label><input class="in wide" id="w1" data-wa="url" value="${esc(c.url || "")}" placeholder="https://"><label for="w2">Key<small>${s.key.set ? `In use, ends ${esc(s.key.hint || "")}. It cannot be shown again.` : "Not set"}</small></label><input class="in wide" id="w2" type="password" data-wakey placeholder="${s.key.set ? "Paste a new key to replace it" : "Paste the key"}" autocomplete="off">
    <span class="lb">What counts as sent<small>Any 2xx answer from the provider</small></span><span class="pt9">Wait ${c.timeout} seconds for the answer, retry ${c.retries} times, ${c.gap} seconds apart</span></div>
    <div class="actions"><button class="btn pri" type="button" data-wasave>Save</button><button class="btn" type="button" data-testwa>${ico("send", 15)}Send a test to my number</button></div></div></div>`;
  }
  const a = await api.get("/admin/v1/audit?limit=40");
  return `<div class="rise">${(a.items || []).length ? `<div class="scroll"><table class="tbl"><thead><tr><th>When</th><th>Who</th><th>What</th></tr></thead><tbody>${a.items.map((r) => `<tr><td class="num">${fmtTime(r.ts)}</td><td>${esc(r.actor_name || "")}</td><td class="main-c">${esc(humanize(r.action || ""))}${r.target_type ? ` · ${esc(r.target_type)}` : ""}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">Nothing recorded yet.</div>`}</div>`;
}
async function openTpl(type) {
  const a = S.data.cat.find((x) => x.type === type), w = S.data.wh.items.find((x) => x.alert_type === type), direct = ["daily_summary", "weekly_summary"].includes(type), m = MEDIA[carries(type)];
  $("#layer").innerHTML = `<aside class="drawer" role="dialog" aria-label="${esc(a.title)}"><div class="row spread"><h2 class="h2">${esc(a.title)}</h2><button class="ibtn" type="button" id="dclose" aria-label="Close">${ico("x")}</button></div>
  <div class="row"><span class="tag ${m[1]}">${m[0]}</span></div>
  ${direct ? `<div class="note">Summaries are sent as direct text, so they have no template.</div>` : w ? `<div><div class="sec-h mb6">Name</div><input class="in full" id="d-name" value="${esc(w.name)}"></div><div><div class="sec-h mb6">Address</div><input class="in full" id="d-url" value="${esc(w.url)}"></div>
  <div><div class="sec-h mb6">Message sent to the provider</div><textarea class="in area" id="d-tpl" spellcheck="false">${esc(w.payload_template || "")}</textarea><div class="chips">${a.shortcodes.map((s) => `<button class="sc pick-sc" type="button" data-sc="${s}">{${s}}</button>`).join(" ")}</div></div>
  <div class="row"><span>On</span>${sw("d-on", w.active, false, "")}</div>
  <div class="row"><button class="btn pri" type="button" id="dsave" data-wid="${w.id}">Save</button><button class="btn" type="button" id="dtest" data-wid="${w.id}">Send a test</button></div>` : `<div class="note">This alert is not connected to the WhatsApp provider yet.</div><div><div class="sec-h mb6">Address</div><input class="in full" id="d-url" value="${esc(S.data.wh.items[0]?.url || "")}" placeholder="https://"></div><div><button class="btn pri" type="button" id="dnew" data-type="${type}">Set up this alert</button></div>`}</aside>`;
}

/* ------------------------------------------------------------------------------------------------ modal, render, events */
function modal(html) { $("#layer").innerHTML = `<div class="scrim" data-scrim><div class="modal" role="dialog" aria-modal="true">${html}</div></div>`; const b = $("#layer .modal input, #layer .modal button"); if (b) b.focus(); }
const closeModal = () => { $("#layer").innerHTML = ""; };
const mh = (t) => `<div class="mh"><span class="spacer"></span><h2>${t}</h2><button class="ibtn" type="button" data-close aria-label="Close">${ico("x")}</button></div>`;
const PAGES = { overview: pageOverview, customers: pageCustomers, new: pageNew, customer: pageCustomer, event: pageEvent, messages: pageMessages, setup: pageSetup };
let picRun = 0;
async function loadPics() {
  const run = ++picRun, jobs = [...document.querySelectorAll("[data-pic],[data-campic-box]")];
  const one = async (el) => {
    if (run !== picRun || !el.isConnected) return;
    const url = el.dataset.pic ? (([rid, ch]) => `/admin/v1/tenants/${S.cid}/shop/recorders/${rid}/channels/${ch}/snapshot`)(el.dataset.pic.split("|")) : `/admin/v1/tenants/${S.cid}/shop/cameras/${el.dataset.campicBox}/snapshot`;
    try { const blob = await api.call("GET", url, undefined, { blob: true }); if (!el.isConnected) return; const img = document.createElement("img"); img.className = "snap"; img.alt = "Camera picture"; img.src = URL.createObjectURL(blob); el.textContent = ""; el.appendChild(img); }
    catch (e) { if (!el.isConnected) return; const m = el.querySelector(".picmsg") || el.appendChild(document.createElement("span")); m.className = "picmsg"; m.textContent = e.status === 409 ? "Camera offline" : e.status === 504 ? "No picture: the camera did not answer in time" : "No picture available right now"; }
  };
  let i = 0; const worker = async () => { while (i < jobs.length && run === picRun) await one(jobs[i++]); };         // two at a time: each picture is taken from the recorder at the shop
  await Promise.all([worker(), worker()]);
}
let token = 0;
async function render(keepScroll) {
  const mine = ++token, p = $("#page"), y = p.scrollTop;
  shell();
  let html;
  try { html = await PAGES[S.route](); } catch (e) { html = `<div class="empty">${esc(errText(e))}<br><button class="btn" type="button" data-retry>Try again</button></div>`; }
  if (mine !== token) return;
  p.innerHTML = html; p.scrollTop = keepScroll ? y : 0;
  p.querySelectorAll(".bars i[data-h]").forEach((b) => { b.style.height = b.dataset.h + "px"; });
  loadPics();
  const d = $("#draw"); if (d) d.addEventListener("click", (ev) => { const r = d.getBoundingClientRect(); S.ev.pts.push([Math.round((ev.clientX - r.left) / r.width * 640), Math.round((ev.clientY - r.top) / r.height * 360)]); render(true); });
}
function go(r, extra) { Object.assign(S, extra || {}); S.route = r; S.pop = false; closeModal(); try { history.replaceState(null, "", "#" + r); } catch { /* fine */ } return render(); }
const ORDER = NSTEPS.map((s) => s[0]);
const eorder = () => ESTEPS.map((s) => s[0]).filter((s) => s !== "area" || needsArea());
let busyAct = false;
document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("[data-go],[data-cust],[data-view],[data-pop],[data-filter],[data-lchip],[data-step],[data-sec],[data-setup],[data-next],[data-prev],[data-create],[data-lang],[data-hmode],[data-copyh],[data-rtype],[data-addp],[data-delp],[data-tpl],[data-close],[data-scrim],[data-finish],[data-testmsg],[data-pausec],[data-extend],[data-extendc],[data-delc],[data-save],[data-resend],[data-invite],[data-testwa],[data-addev],[data-estep],[data-evnext],[data-evprev],[data-evcreate],[data-etarget],[data-etype],[data-clearpts],[data-evdone],[data-retry],[data-addrec],[data-campic],[data-findcams],[data-showconn],[data-addcams],[data-setupall],[data-disable],[data-enable],[data-resetlink],[data-wasave],[data-confirm],#home,#theme,#signout");
  if (S.pop && !ev.target.closest(".tools")) { S.pop = false; render(true); }
  if (!t) return; const d = t.dataset;
  try {
    if ("scrim" in d) { if (ev.target !== t) return; return closeModal(); }
    if (t.id === "home") return go("overview");
    if (t.id === "theme") { const dark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme:dark)").matches); document.documentElement.dataset.theme = dark ? "light" : "dark"; return shell(); }
    if (t.id === "signout") { try { await api.call("POST", "/admin/v1/auth/logout", { refresh_token: api.refreshToken }); } catch { /* signing out anyway */ } api.clear(); return location.replace("/"); }
    if ("close" in d) return closeModal();
    if ("retry" in d) return render();
    if (d.go) { if (d.go === "new") { S.form = blankForm(); S.step = "shop"; } if (d.go === "customers") await loadCustomers(); if (d.go === "customer") S.cur = null; return go(d.go); }
    if (d.cust) { S.cid = d.cust; S.cur = null; S.section = "people"; return go("customer"); }
    if (d.view) { S.view = d.view; return render(true); }
    if ("pop" in d) { S.pop = !S.pop; return render(true); }
    if (d.filter) { S.filter = d.filter; S.pop = false; return render(true); }
    if (d.lchip) { S.logChip = d.lchip; return render(true); }
    if (d.step) { S.step = d.step; return render(); }
    if (d.sec) { S.section = d.sec; if (d.sec !== "log") S.cur = null; return render(); }
    if (d.setup) { S.setup = d.setup; return go("setup"); }
    if ("next" in d) { S.form.done[S.step] = true; S.step = ORDER[ORDER.indexOf(S.step) + 1]; return render(); }
    if ("prev" in d) { S.step = ORDER[ORDER.indexOf(S.step) - 1]; return render(); }
    if ("create" in d) { S.form.done.summary = true; return createCustomer(); }
    if ("finish" in d) return go("customer", { section: "events" });
    if (d.lang) { (S.route === "new" ? S.form : S.cur.f).lang = d.lang; return render(true); }
    if (d.hmode) { (S.route === "new" ? S.form : S.cur.f).hmode = d.hmode; return render(true); }
    if ("copyh" in d) { const f = S.route === "new" ? S.form : S.cur.f, m = f.hours[0]; f.hours.forEach((h) => { if (h.open) { h.from = m.from; h.to = m.to; } }); return render(true); }
    if (d.rtype) { S.form.rtype = d.rtype; return render(true); }
    if ("addp" in d) { (S.route === "new" ? S.form : S.cur.f).people.push({ id: null, n: "", m: "", a: true, s: true }); return render(true); }
    if (d.delp !== undefined) { (S.route === "new" ? S.form : S.cur.f).people.splice(+d.delp, 1); return render(true); }
    if ("save" in d) return saveSection();
    if (d.tpl) return openTpl(d.tpl);
    if ("testmsg" in d) { const saved = S.cur.events.filter((e) => e.saved && !HIDDEN.has(e.type)); if (!S.cur.people.length || !saved.length) return toast("Add a person and an event first.", true); return modal(`${mh("Send a test message")}<label class="bold" for="tt">Event</label><select class="in" id="tt">${saved.map((e) => `<option value="${e.type}">${esc(e.label || e.title)}</option>`).join("")}</select><label class="bold" for="tp">Send to</label><select class="in" id="tp">${S.cur.people.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select><button class="btn pri wide" type="button" data-confirm="test">Send now</button>`); }
    if (d.confirm === "test") { const r = await api.post(`/admin/v1/tenants/${S.cid}/shop/events/${$("#tt").value}/test`, { recipient_id: $("#tp").value }); closeModal(); return toast(r.sent ? `Test message sent to ${r.to}` : `Not sent: ${r.reason || r.status}`, !r.sent); }
    if ("pausec" in d) { const c = cust(S.cid); if (c.status === "suspended") await api.post(`/admin/v1/tenants/${c.id}/reactivate`, { reason: "Resumed from the Control Center" }); else await api.post(`/admin/v1/tenants/${c.id}/suspend`, { reason: "Paused from the Control Center" }); await loadCustomers(); toast(c.status === "suspended" ? "Resumed." : "Paused. No messages will go out."); return render(true); }
    if ("extend" in d || "extendc" in d) { const c = d.extendc ? cust(d.extendc) : cust(S.cid); return modal(`${mh("Change end date")}<label class="bold" for="nd">${esc(c.name)}: new end date</label><input class="in" id="nd" type="date" value="${c.endsOn || plusDays(365)}"><p class="sub nm">On the day after, everything stops and the shop gets one WhatsApp message. A customer who has already ended is switched back on.</p><button class="btn pri wide" type="button" data-confirm="extend" data-id="${c.id}">Save</button>`); }
    if (d.confirm === "extend") { const v = $("#nd").value; if (!v) return toast("Choose a date.", true); const c = cust(d.id); await api.patch(`/admin/v1/tenants/${c.id}`, { ends_on: v }); if (c.status === "suspended" && c.reason.startsWith("Service ended")) await api.post(`/admin/v1/tenants/${c.id}/reactivate`, { reason: "End date changed from the Control Center" }); await loadCustomers(); S.cur = null; closeModal(); toast("End date changed"); return render(true); }
    if ("delc" in d) return modal(`${mh("Delete " + esc(cust(S.cid).name) + "?")}<div class="note warn">This removes the customer, its cameras and all history. It cannot be undone.</div><label class="bold" for="dn">Type the shop name to confirm</label><input class="in" id="dn" placeholder="${esc(cust(S.cid).name)}" autocomplete="off"><div class="row"><button class="btn danger" type="button" data-confirm="delete">Delete forever</button><button class="btn" type="button" data-close>Keep</button></div>`);
    if (d.confirm === "delete") { if ($("#dn").value.trim() !== cust(S.cid).name) return toast("The name does not match.", true); await api.del(`/admin/v1/tenants/${S.cid}?reason=${encodeURIComponent("Deleted from the Control Center")}`); await loadCustomers(); closeModal(); toast("Customer deleted"); return go("customers"); }
    if (d.resend) { await api.post(`/admin/v1/webhooks/deliveries/${d.resend}/resend`); toast("Sent again"); return render(true); }
    if ("invite" in d) return modal(`${mh("Add a person")}<label class="bold" for="in1">Name</label><input class="in" id="in1" autocomplete="off"><label class="bold" for="in2">Email</label><input class="in" id="in2" type="email" autocomplete="off"><label class="bold" for="in3">Role</label><select class="in" id="in3">${S.role === "owner" ? '<option value="owner">Owner</option>' : ""}<option value="admin">Admin</option><option value="onboarding">Onboarding</option><option value="support">Support</option></select><button class="btn pri wide" type="button" data-confirm="invite">Create sign-in link</button>`);
    if (d.confirm === "invite") { const r = await api.post("/admin/v1/staff", { name: $("#in1").value.trim(), email: $("#in2").value.trim(), role: $("#in3").value }); modal(`${mh("Sign-in link")}<p class="sub nm">Send this link to ${esc(r.email)}. It works once and lets them choose a password.</p><input class="in full" readonly value="${esc(r.link)}" id="lk"><button class="btn pri wide" type="button" data-close>Done</button>`); $("#lk").select(); return; }
    if (d.disable || d.enable) { await api.post(`/admin/v1/staff/${d.disable || d.enable}/${d.disable ? "disable" : "enable"}?reason=${encodeURIComponent("Changed from the Control Center")}`); toast("Done"); return render(true); }
    if (d.resetlink) { const r = await api.post(`/admin/v1/staff/${d.resetlink}/reset-link?reason=${encodeURIComponent("New link from the Control Center")}`); modal(`${mh("Sign-in link")}<p class="sub nm">This link works once.</p><input class="in full" readonly value="${esc(r.link)}" id="lk"><button class="btn pri wide" type="button" data-close>Done</button>`); $("#lk").select(); return; }
    if ("testwa" in d) { await api.post("/admin/v1/summaries/test", {}); return toast("Test sent to your number"); }
    if ("wasave" in d) { const url = document.querySelector("[data-wa=url]").value.trim(), key = document.querySelector("[data-wakey]").value.trim(); await api.put("/admin/v1/summaries", { config: { url } }); if (key) await api.put("/admin/v1/summaries/key", { key }); toast("Saved"); return render(true); }
    /* recorder and cameras */
    if ("addrec" in d) return modal(`${mh("Add a recorder")}<label class="bold" for="rn">Recorder name</label><input class="in" id="rn" placeholder="For example, Main shop" autocomplete="off"><p class="sub nm">Dahua recorders connect to our server by themselves. You get the details to type into the recorder next.</p><button class="btn pri wide" type="button" data-confirm="addrec">Add recorder</button>`);
    if (d.confirm === "addrec") { const r = await api.post(`/admin/v1/tenants/${S.cid}/recorders`, { brand: "dahua", name: $("#rn").value.trim() || null, reason: "Added from the Control Center" }); S.cur = null; modal(`${mh("Type these into the recorder")}<div class="creds">${credRows(r)}</div><p class="sub nm">This is the only time the key is shown.</p><button class="btn pri wide" type="button" data-close>Done</button>`); return render(true); }
    if (d.campic) { const c = S.cur.cams.find((x) => x.id === d.campic); modal(`${mh(esc(c.name))}<div class="vframe"><div class="vhead">${ico("camera2", 22)}<span>${esc(c.name)} · ${esc(cname())}</span></div><div class="vpic big" data-campic-box="${c.id}"><span class="picmsg">Loading picture…</span></div></div><button class="btn wide" type="button" data-close>Close</button>`); return loadPics(); }
    if (d.showconn) { const r = await api.get(`/admin/v1/recorders/${d.showconn}/connection`); return modal(`${mh("Recorder details")}<div class="creds">${credRows(r)}</div><button class="btn pri wide" type="button" data-close>Done</button>`); }
    if (d.findcams) { const list = await api.get(`/admin/v1/tenants/${S.cid}/shop/recorders/${d.findcams}/channels`); S.cur.channels = { [d.findcams]: list.map((c) => ({ ...c, on: !c.enabled_as_camera, name: c.name || "Camera " + c.channel_no })) }; return render(true); }
    if (d.addcams) { const rid = d.addcams; let n = 0; for (const ch of S.cur.channels[rid]) { if (ch.enabled_as_camera || !ch.on) continue; await api.post(`/admin/v1/tenants/${S.cid}/shop/cameras`, { recorder_id: rid, channel_no: ch.channel_no, name: ch.name.trim() || "Camera " + ch.channel_no, use_footfall: true }); n++; } delete S.cur.channels[rid]; const keep = S.cur.channels; await loadCurrent(S.cid); S.cur.channels = keep; await loadCustomers(); toast(n ? `${n} camera${n === 1 ? "" : "s"} added` : "Nothing was ticked"); return render(true); }
    /* event wizard */
    if ("addev" in d) { S.ev = newEv(); S.estep = "event"; return go("event"); }
    if (d.estep) { S.estep = d.estep; if (d.estep === "area") await loadSnap(S.ev); return render(); }
    if (d.etarget) { const [kind, id] = d.etarget.split("|"), name = kind === "rec" ? (S.cur.recs.find((r) => r.id === id).name || S.cur.recs.find((r) => r.id === id).device_id) : S.cur.cams.find((c) => c.id === id).name; if (S.ev.level !== kind) { S.ev.key = null; S.ev.v = {}; } S.ev.target = { kind, id, name }; S.ev.level = kind; S.ev.snap = null; return render(true); }
    if (d.etype) { const x = S.cur.events.find((q) => q.type === d.etype); S.ev.key = d.etype; S.ev.v = {}; if (!S.ev.name) S.ev.name = x.title; return render(true); }
    if ("clearpts" in d) { S.ev.pts = []; return render(true); }
    if ("evnext" in d) { S.ev.done[S.estep] = true; const o = eorder(); S.estep = o[o.indexOf(S.estep) + 1]; if (S.estep === "area") await loadSnap(S.ev); return render(); }
    if ("evprev" in d) { const o = eorder(); S.estep = o[o.indexOf(S.estep) - 1]; return render(); }
    if ("evcreate" in d) return createEvent();
    if ("evdone" in d) return go("customer", { section: "events" });
    if ("setupall" in d) return modal(`${mh("Set up all alerts")}<p class="sub nm">One address for every alert that has none yet. Each gets its standard message, which you can change one by one.</p><label class="bold" for="su">Provider address</label><input class="in full" id="su" placeholder="https://"><label class="bold" for="sh">Authorization header (optional)</label><input class="in full" id="sh" type="password" placeholder="Bearer …" autocomplete="off"><button class="btn pri wide" type="button" data-confirm="setupall">Set up</button>`);
    if (d.confirm === "setupall") { const h = $("#sh").value.trim(); const r = await api.post("/admin/v1/webhooks/standard", { url: $("#su").value.trim(), headers: h ? { Authorization: h } : {} }); closeModal(); toast(`${(r.made || []).length} alerts set up`); return render(true); }
  } catch (e) { toast(errText(e), true); }
});
document.addEventListener("click", async (ev) => {
  const g = (s) => ev.target.closest(s);
  if (g("#dclose")) return closeModal();
  const sc = g("[data-sc]"); if (sc) { const ta = $("#d-tpl"); const p = ta.selectionStart; ta.setRangeText(`{${sc.dataset.sc}}`, p, ta.selectionEnd, "end"); ta.focus(); return; }
  try {
    if (g("#dsave")) { const id = g("#dsave").dataset.wid; await api.put(`/admin/v1/webhooks/${id}`, { name: $("#d-name").value.trim(), url: $("#d-url").value.trim(), payload_template: $("#d-tpl").value, active: $("#d-on").checked }); closeModal(); toast("Saved"); return render(true); }
    if (g("#dtest")) { await api.post(`/admin/v1/webhooks/${g("#dtest").dataset.wid}/send-now`, {}); return toast("Test sent"); }
    if (g("#dnew")) { const type = g("#dnew").dataset.type, a = S.data.cat.find((x) => x.type === type); await api.post("/admin/v1/webhooks", { name: a.title, alert_type: type, url: $("#d-url").value.trim() }); closeModal(); toast("Set up"); return render(true); }
  } catch (e) { toast(errText(e), true); }
});
document.addEventListener("input", (ev) => {
  const t = ev.target;
  if (t.id === "q") { S.q = t.value; const p = t.selectionStart; render(true).then(() => { const n = $("#q"); if (n) { n.focus(); n.setSelectionRange(p, p); } }); return; }
  if (t.id === "tq") { S.tq = t.value; const p = t.selectionStart; render(true).then(() => { const n = $("#tq"); if (n) { n.focus(); n.setSelectionRange(p, p); } }); return; }
  if (S.route === "event" && S.ev) { const v = S.ev, d = t.dataset;
    if (d.ebind) v[d.ebind] = t.value; else if (d.eto) { const i = v.to.indexOf(d.eto); if (t.checked && i < 0) v.to.push(d.eto); if (!t.checked && i >= 0) v.to.splice(i, 1); } else if (d.ev) v.v[d.ev] = t.value; else if ("enotice" in d) v.notice = t.checked;
    else if (d.act === "eon") v.on = t.checked; else if (d.act === "eclip") v.clip = t.checked; return; }
  const f = S.route === "new" ? S.form : S.route === "customer" ? S.cur?.f : null; if (!f) return; const d = t.dataset;
  if (d.bind) f[d.bind] = t.value;
  else if (d.p) { const [i, k] = d.p.split("."); f.people[+i][k] = t.value; }
  else if (d.pc) { const [i, k] = d.pc.split("."); f.people[+i][k] = t.checked; }
  else if (d.ht) { const [i, k] = d.ht.split("."); f.hours[+i][k] = t.value; }
  else if (d.ch) { const [rid, i] = d.ch.split("."); S.cur.channels[rid][+i].on = t.checked; }
  else if (d.chn) { const [rid, i] = d.chn.split("."); S.cur.channels[rid][+i].name = t.value; }
  else if (d.act) { const a = d.act; if (a === "daily") f.daily = t.checked; else if (a === "weekly") f.weekly = t.checked; else if (a.startsWith("hd:")) { f.hours[+a.slice(3)].open = t.checked; render(true); } }
});
document.addEventListener("change", async (ev) => {
  const t = ev.target, d = t.dataset;
  try {
    if (d.act?.startsWith("evon:") && S.cur) { const type = d.act.slice(5), e = S.cur.events.find((x) => x.type === type); await api.put(`/admin/v1/tenants/${S.cid}/shop/events/${type}`, { label: e.label, enabled: t.checked, params: e.params, recipient_ids: e.recipient_ids, quiet_start: e.quiet_start, quiet_end: e.quiet_end }); e.enabled = t.checked; toast(t.checked ? "Event is on" : "Event is off"); return render(true); }
    if (d.act?.startsWith("camfoot:") || d.act?.startsWith("camplate:")) { const plate = d.act.startsWith("camplate:"), id = d.act.split(":")[1], c = S.cur.cams.find((x) => x.id === id);
      const body = plate ? { use_anpr: t.checked, ...(t.checked ? { stream: "main" } : {}) } : { use_footfall: t.checked };
      await api.patch(`/admin/v1/tenants/${S.cid}/shop/cameras/${id}`, body); toast("Saved"); const keep = S.cur.channels; await loadCurrent(S.cid); S.cur.channels = keep; return render(true); }
    if (d.act?.startsWith("camon:")) { await api.patch(`/admin/v1/tenants/${S.cid}/shop/cameras/${d.act.slice(6)}`, { enabled: t.checked }); toast("Saved"); return loadCurrent(S.cid).then(() => render(true)); }
    if (d.camName) { await api.patch(`/admin/v1/tenants/${S.cid}/shop/cameras/${d.camName}`, { name: t.value.trim() }); toast("Saved"); }
  } catch (e) { toast(errText(e), true); render(true); }
});
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeModal(); });

/* ------------------------------------------------------------------------------------------------ start */
async function boot() {
  if (!api.signedIn()) return location.replace("/");
  try { S.me = await api.get("/admin/v1/me"); } catch (e) { if (e.status === 401) return location.replace("/"); return void ($("#page").innerHTML = `<div class="empty">${esc(errText(e))}<br><button class="btn" type="button" data-retry>Try again</button></div>`); }
  S.role = S.me.role;
  try { await loadCustomers(); } catch (e) { toast(errText(e), true); }
  const h = location.hash.slice(1); if (["overview", "customers", "messages", "setup"].includes(h)) S.route = h;
  render();
}
boot();
