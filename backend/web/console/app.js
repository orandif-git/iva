// Jas iVision Control Center: the console Jas iVision's own staff use to set up and look after every customer.
// One page of script, one registry of screens, every number comes from the admin API (/admin/v1). There are no inline styles: the page's security policy forbids them.
import { Api } from "/console/lib.js";

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
  edit: '<path d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4"/>', car: '<path d="M4 15.5V12l2-5h12l2 5v3.5M4 15.5h16M4 15.5v2.5h3v-2.5M17 15.5V18h3v-2.5"/><circle cx="7.5" cy="12.5" r=".6"/><circle cx="16.5" cy="12.5" r=".6"/>', head: '<circle cx="12" cy="7" r="3"/><path d="M5.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6"/><path d="M3.5 9.5v-4h4M20.5 9.5v-4h-4"/>', dl: '<path d="M12 4v11M7.5 10.5L12 15l4.5-4.5M5 19h14"/>', trash: '<path d="M5 7h14M10 7V4.5h4V7M7 7l1 12.5h8L17 7"/>', pause: '<path d="M9 5.5v13M15 5.5v13"/>', pulse: '<path d="M3 12h4l2-6 4 12 2-6h6"/>',
  wa: '<path d="M12 3.5a8.5 8.5 0 0 0-7.3 12.8L3.5 20.5l4.3-1.1A8.5 8.5 0 1 0 12 3.5z"/><path d="M9 8.5c0 3.5 3 6.5 6.5 6.5l1-1.6-2-1-1 .8c-1-.4-2-1.4-2.4-2.4l.8-1-1-2z"/>',
  photo: '<rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="M4 16l4.5-4 4 3.5 3-2.5L20 16"/>', area: '<path d="M5 8l5-3.5 9 4-2 9-8 2.5z"/>',
  camera2: '<path d="M4 8h3l1.5-2h7L17 8h3v10H4zM12 16.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z"/>', out: '<path d="M9 5H5v14h4M15 8l4 4-4 4M19 12H9"/>', dots: '<circle cx="6" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="18" cy="12" r="1.3"/>', screen: '<rect x="3.5" y="5" width="17" height="11" rx="2"/><path d="M9 19.5h6M12 16v3.5"/>'
};
const ico = (n, s = 18) => `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${IC[n]}</svg>`;
const sw = (id, on, dis, act) => `<label class="sw"><input type="checkbox" id="${id}" ${on ? "checked" : ""} ${dis ? "disabled" : ""} ${act ? `data-act="${act}"` : ""}><i></i><span class="sr">Switch</span></label>`;

/* ------------------------------------------------------------------------------------------------ what the events are (the catalogue comes from the server; this only adds how the wizard treats each one) */
const REC_LEVEL = new Set(["recorder_offline", "connection_unstable", "clock_wrong", "back_to_normal", "recorder_restarted", "hardware_fault", "storage_full", "disk_wear", "retention_low", "disk_fault", "recording_stopped"]);
const HIDDEN = new Set(["daily_summary", "weekly_summary", "service_ended"]);                    // set in "Summaries and end date", not in the event wizard
const AREA_KIND = { people_in_area: "area_count", queue_long: "queue_area", phone_use: "staff_area", area_activity: "activity_area" };
const CARRIES = { view_changed: "image", picture_poor: "image", crowding: "image", after_hours: "image", people_in_area: "image", queue_long: "image", phone_use: "image", area_activity: "image", blocked_plate: "clip", vehicle_overstay: "image", low_confidence_plate: "image", daily_summary: "direct", weekly_summary: "direct", __count: "data", __plates: "data" };
const DATA_TYPES = [
  { type: "__count", title: "Count people", group: "On this camera", ai: false, settings: {}, description: "Counts the people passing this camera. Used for the shop's visitor numbers and for crowding, after-hours and counting alerts. Sends no message." },
  { type: "__plates", title: "Read number plates", group: "On this camera", ai: false, settings: {}, description: "Reads the number plates of vehicles on this camera and which way they move, in or out. Used for the vehicle list and the blocked, overstay and review alerts. Sends no message." }];
const NEEDS_COUNT = new Set(["crowding", "after_hours", "counting_stopped"]), NEEDS_PLATES = new Set(["blocked_plate", "vehicle_overstay", "low_confidence_plate"]);
/* Which way is IN for a plate camera: the way a vehicle moves in the picture when it comes in. Saved with the plate area, once per camera. */
const DIRS = { right: [1, 0], left: [-1, 0], up: [0, -1], down: [0, 1] };
const DIR_LABEL = { left: "Moving left", right: "Moving right", up: "Moving away from the camera", down: "Moving towards the camera" };
const DIR_ARROW = { left: "←", right: "→", up: "↑", down: "↓" };
const dirName = (v) => !v || v.length !== 2 ? null : Math.abs(v[0]) >= Math.abs(v[1]) ? (v[0] > 0 ? "right" : "left") : (v[1] > 0 ? "down" : "up");
const plateDirOf = (c) => dirName(((S.cur.zones || {})[c.id] || []).find((q) => q.kind === "anpr_zone")?.in_direction);
const plateCams = () => S.cur.cams.filter((c) => c.use_anpr);
const noDirCams = () => plateCams().filter((c) => !plateDirOf(c));
const dirPicker = (cur, attr) => `<div class="dirs" role="group" aria-label="Which way is IN">${Object.keys(DIRS).map((k) => `<button class="btn sm" type="button" ${attr}="${k}" aria-pressed="${cur === k}">${DIR_ARROW[k]} ${DIR_LABEL[k]}</button>`).join("")}</div>`;
const evMeta = (type) => DATA_TYPES.find((x) => x.type === type) || S.cur.events.find((x) => x.type === type);
const MEDIA = { data: ["No message", "mute"], none: ["Text only", "mute"], image: ["Picture", "ai"], clip: ["Picture + video", "brand"], direct: ["Direct text", "mute"] };
const carries = (t) => CARRIES[t] || "none";
const PARAM = { people: ["More than", "people"], picture_minutes: ["Picture at most every", "min"], grace_minutes: ["Wait after closing", "min"], drops: ["Drops in a day", "times"], minutes_out: ["Clock is out by", "min"], temperature: ["Too hot above", "°C"],
  percent: ["Health left below", "%"], days: ["Days of video wanted", "days"], hours: ["For", "hours"], max_minutes: ["Stays longer than", "min"] };
const AREA_FIELDS = { area_count: [["limit", "At least", "people", 5], ["minutes", "For", "min", 2]], queue_area: [["length", "Queue of at least", "people", 6], ["wait_minutes", "Or wait longer than", "min", 10], ["minutes", "For", "min", 2]], staff_area: [["seconds", "For more than", "sec", 20]], activity_area: [["minutes", "Present for at least", "min", 1]] };
const SHORT = ["customer", "camera", "time", "details", "area", "people", "plate", "minutes", "recorder", "image_url", "clip_url"];

/* ------------------------------------------------------------------------------------------------ state */
const S = { route: "overview", me: null, role: "", cid: null, section: "cams", step: "shop", q: "", filter: "all", view: "list", logChip: "all", logQ: "", logRange: "7", logFrom: "", logTo: "", logData: null, evQ: "", keepDays: null, evOpen: { cam: true, area: true, alert: true, default: false }, setup: "tpl", tq: "", pop: false, estep: "event",
  customers: [], form: null, ev: null, cur: null, viewsByCust: {}, data: {}, plans: [], cnt: { range: "today", cam: "all", data: null }, veh: { range: "7", dir: "all", st: "all", q: "", items: [], total: 0, tz: "" } };
const can = (k) => ({ newCustomer: ["owner", "admin", "onboarding"], expiry: ["owner", "admin"], del: ["owner"], pause: ["owner", "admin", "support"], templates: ["owner", "admin"], keep: ["owner", "admin"], live: ["owner", "admin"], sys: ["owner", "admin"], team: ["owner", "admin"], wa: ["owner"], events: ["owner", "admin", "onboarding"], recs: ["owner", "admin", "onboarding"], editc: ["owner", "admin", "onboarding"] }[k] || []).includes(S.role);
const ROLES = { owner: ["Owner", "Everything, including the WhatsApp connection and console users"], admin: ["Admin", "Everything except the WhatsApp key and making Owners"], onboarding: ["Onboarding", "Adds new customers and sets them up"], support: ["Support", "Looks after live customers: pause, fix people and hours"] };
const PERMS = [["See all customers and the message log", "oaos"], ["Add a new customer and set it up", "oao-"], ["Edit people, hours and cameras", "oaos"], ["Add and change events", "oao-"], ["Pause or resume a customer", "oa-s"], ["Change the end date", "oa--"], ["Delete a customer", "o---"],
  ["WhatsApp templates", "oa--"], ["Console users", "oa--"], ["WhatsApp connection and key", "o---"]];

/* ------------------------------------------------------------------------------------------------ customers */
const mapCustomer = (t) => ({ id: t.id, name: t.name, place: t.city || "", cams: t.cameras_in_use, recs: t.recorders_total, recsOn: t.recorders_online, lang: t.msg_language, ownerName: t.owner_name || "", ownerPhones: t.owner_phones || [], ownerEmail: t.owner_email || "", people: t.people || "", last: t.last_message_at, endsOn: t.ends_on,
  days: daysUntil(t.ends_on), status: t.status, reason: t.suspended_reason || "", reasons: [], waiting: "" });
const ended = (c) => c.status === "suspended" && c.reason.startsWith("Service ended");
const since = (iso) => { if (!iso) return "a while"; const m = Math.max(1, Math.round((Date.now() - new Date(iso)) / 6e4)); return m < 60 ? `${m} minute${m === 1 ? "" : "s"}` : m < 1440 ? `${Math.round(m / 60)} hour${Math.round(m / 60) === 1 ? "" : "s"}` : `${Math.round(m / 1440)} day${Math.round(m / 1440) === 1 ? "" : "s"}`; };
const statusOf = (c) => c.status === "suspended" ? (ended(c) ? ["Inactive", "crit"] : ["Paused", "mute"]) : c.days !== null && c.days < 0 ? ["Inactive", "crit"] : c.days !== null && c.days <= 7 ? ["Ending soon", "warn"] : c.reasons.length ? ["Needs a look", "warn"] : c.waiting ? ["Waiting for recorder", "mute"] : ["Active", "ok"];
const whyOf = (c) => { const st = statusOf(c)[0]; return st === "Needs a look" ? c.reasons.join(". ") : st === "Waiting for recorder" ? `Waiting for ${c.waiting} to connect` : st === "Ending soon" ? `Service ends in ${c.days} day${c.days === 1 ? "" : "s"}` : st === "Inactive" ? (c.status === "suspended" && !c.reason.startsWith("Service ended") ? c.reason : `Service ended on ${fmtDate(c.endsOn)}`) : st === "Paused" ? "Paused. No messages go out." : ""; };
const STATUSES = ["Active", "Needs a look", "Waiting for recorder", "Ending soon", "Paused", "Inactive"];
const peopleShort = (s) => { const a = (s || "").split(", ").filter(Boolean); return !a.length ? "—" : a.length <= 2 ? a.join(", ") : `${a[0]}, ${a[1]} +${a.length - 2} more`; };
const cust = (id) => S.customers.find((c) => c.id === id);
async function loadCustomers() {
  const [r, recs] = await Promise.all([api.get("/admin/v1/tenants?limit=200"), api.get("/admin/v1/recorders?limit=500")]);
  S.customers = r.items.map(mapCustomer);
  for (const rec of recs) {
    const c = cust(rec.tenant_id); if (!c) continue; const nm = rec.name || rec.device_id;
    if (rec.status === "offline") c.reasons.push(`${nm} offline for ${since(rec.last_heartbeat_at)}`);
    else if (rec.status === "never") { if (Date.now() - new Date(rec.created_at) > 864e5) c.reasons.push(`${nm} has never connected`); else c.waiting = nm; }
  }
}

/* ------------------------------------------------------------------------------------------------ shell */
const AREAS = [["overview", "Overview", "grid"], ["customers", "Customers", "users"], ["messages", "Messages", "chat"], ["setup", "Setup", "gear"]];
const PH = matchMedia("(max-width: 760px)");                                   // the phone layout: same pages, laid out for a small screen
const TABS = [["overview", "Overview", "grid"], ["customers", "Customers", "users"], ["messages", "Messages", "chat"], ["health", "Health", "pulse"], ["more", "More", "dots"]];
const tabOf = () => S.route === "setup" ? (S.setup === "sys" ? "health" : "more") : S.route === "more" ? "more" : areaOf();
const tabList = () => TABS.filter((t) => t[0] !== "health" || can("sys"));
const PTITLE = { overview: "Overview", customers: "Customers", messages: "Messages", more: "More", new: "New customer", event: "New event" };
const SETUP_NAME = { tpl: "WhatsApp templates", team: "Console users", keep: "Data kept", wa: "WhatsApp connection", sys: "System health", audit: "Activity log" };
function tabBar() {
  const bar = $("#tabbar"); if (!bar) return;
  const tabs = tabList();
  if (bar.dataset.n !== String(tabs.length)) {
    bar.dataset.n = String(tabs.length); bar.querySelectorAll("button").forEach((b) => b.remove());
    tabs.forEach((t) => bar.insertAdjacentHTML("beforeend", `<button type="button" data-tab="${t[0]}" aria-label="${t[1]}">${ico(t[2], 23)}<span>${t[1]}</span></button>`));
  }
  const cur = tabOf();
  bar.querySelectorAll("button").forEach((b) => b.setAttribute("aria-current", String(b.dataset.tab === cur)));
  $("#pill").style.setProperty("--i", String(Math.max(0, tabs.findIndex((t) => t[0] === cur))));
  const t = S.route === "customer" ? cname() : S.route === "setup" ? SETUP_NAME[S.setup] || "Setup" : PTITLE[S.route] || "";
  $("#ptitle").textContent = t; const inCust = PH.matches && S.route === "customer"; $("#pback").hidden = !inCust; $("#pback").innerHTML = ico("back", 24); $("#home").hidden = inCust;
}
const areaOf = () => ["customers", "customer", "new", "event"].includes(S.route) ? "customers" : S.route;
function shell() {
  $("#areas").innerHTML = AREAS.map((a) => `<button class="area" type="button" data-go="${a[0]}" ${areaOf() === a[0] ? 'aria-current="page"' : ""}>${ico(a[2], 19)}<span>${a[1]}</span></button>`).join("");
  const rails = { overview: [["overview", "pulse", "Overview"]], customers: [["customers", "list", "All customers"], ["new", "plus", "New customer", !can("newCustomer")]], messages: [["messages", "log", "Message log"]], setup: [["setup", "gear", "Setup"]], more: [] }[areaOf()];
  $("#rail").innerHTML = rails.map((r) => `<button class="rb" type="button" title="${r[2]}${r[3] ? " (not for your role)" : ""}" data-go="${r[0]}" ${r[3] ? "disabled" : ""} ${S.route === r[0] || (r[0] === "customers" && ["customer", "event"].includes(S.route)) ? 'aria-current="page"' : ""} aria-label="${r[2]}">${ico(r[1], 19)}</button>`).join("");
  $("#theme").innerHTML = ico(document.documentElement.dataset.theme === "dark" ? "sun" : "moon", 19);
  $("#av").textContent = (S.me?.name || "?").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();
  $("#role").textContent = ROLES[S.role]?.[0] || "";
  side(); tabBar();
}
const NSTEPS = [["shop", "Customer", "store"], ["people", "Who gets messages", "users"], ["hours", "Business hours", "clock"], ["cams", "Recorder", "cam"], ["summary", "Summaries and end date", "send"]];
const ESTEPS = [["event", "Event", "bell"], ["type", "Event type", "list"], ["recorder", "Recorder", "rec"], ["camera", "Camera", "cam"], ["area", "Mark on the picture", "area"], ["settings", "Settings", "gear"]];
const needsArea = () => !!(S.ev && S.ev.key && AREA_KIND[S.ev.key]);
/* What has to be marked on the picture: an area (area events), a counting line (counting people) or the area plates are read in (reading plates) */
const markMode = (k) => !k ? null : AREA_KIND[k] ? "area" : (k === "__count" || NEEDS_COUNT.has(k)) ? "line" : k === "__plates" ? "zone" : null;
const MARK_TITLE = { area: "Mark the area", line: "Mark the counting line", zone: "Mark the plate area" };
const needsCam = (k) => !!(k && (AREA_KIND[k] || NEEDS_COUNT.has(k) || k.startsWith("__")));
const eorder = () => { const o = ["event", "type"]; if (S.ev.key && needsCam(S.ev.key)) { if (S.cur.recs.length > 1) o.push("recorder"); o.push("camera"); } if (markMode(S.ev.key)) o.push("area"); o.push("settings"); return o; };
const cname = () => cust(S.cid)?.name || "";
const custSecs = () => [["cams", "cam", "Recorder and cameras", "Cameras"], ["events", "bell", "Events", "Events"], ...(S.viewsByCust[S.cid]?.people.show ? [["count", "head", "People count", "People count"]] : []), ...(S.viewsByCust[S.cid]?.vehicles.show ? [["vehicles", "car", "Vehicles", "Vehicles"]] : []), ["people", "users", "Who gets messages", "People"], ["hours", "clock", "Business hours", "Hours"], ["health", "pulse", "Health", "Health"], ["log", "log", "Messages sent", "Messages"]];
function side() {
  const el = $("#side"); let h = "";
  if (S.route === "new") h = `<h3>New customer</h3>` + NSTEPS.map((s, i) => `<button class="si" type="button" data-step="${s[0]}" aria-current="${S.step === s[0]}">${ico(s[2])}${s[1]}<span class="stp ${S.form.done[s[0]] ? "done" : ""}">${S.form.done[s[0]] ? "✓" : i + 1}</span></button>`).join("");
  else if (S.route === "event") { const o = eorder(); h = `<button class="si" type="button" data-go="customer">${ico("back")}${esc(cname())}</button><h3 class="pt8">New event</h3>` + ESTEPS.map((s, i) => { const skip = !!S.ev.key && !o.includes(s[0]); return `<button class="si ${skip ? "dim" : ""}" type="button" data-estep="${s[0]}" aria-current="${S.estep === s[0]}">${ico(s[2])}${s[1]}<span class="stp ${S.ev.done[s[0]] ? "done" : ""}">${skip ? "Skipped" : S.ev.done[s[0]] ? "✓" : i + 1}</span></button>`; }).join(""); }
  else if (S.route === "customer") h = `<button class="si" type="button" data-go="customers">${ico("back")}All customers</button><h3 class="pt8">${esc(cname())}</h3>` + custSecs().map((s) => `<button class="si" type="button" data-sec="${s[0]}" aria-current="${S.section === s[0]}">${ico(s[1])}${s[2]}</button>`).join("");
  else if (S.route === "setup") h = `<h3>Setup</h3>` + [["tpl", "chat", "WhatsApp templates", can("templates")], ["team", "shield", "Console users", can("team")], ["keep", "log", "Data kept", can("keep")], ["wa", "wa", "WhatsApp connection", can("wa")], ["sys", "pulse", "System health", can("sys")], ["audit", "log", "Activity log", true]].map((s) => `<button class="si ${s[3] ? "" : "dim"}" type="button" data-setup="${s[0]}" aria-current="${S.setup === s[0]}">${ico(s[1])}${s[2]}${s[3] ? "" : `<span class="stp">${ico("lock", 12)}</span>`}</button>`).join("");
  if (h) h += `<div class="foot"><span>Control Center 1.0</span></div>`;
  el.hidden = !h; el.innerHTML = h;
}

/* ------------------------------------------------------------------------------------------------ the picture used for drawing areas */
const SCENE = `<svg viewBox="0 0 640 360" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><defs><linearGradient id="fl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8E8794"/><stop offset="1" stop-color="#6F6874"/></linearGradient><linearGradient id="wl" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#E9EAEE"/><stop offset="1" stop-color="#C9CCD4"/></linearGradient></defs><rect width="640" height="360" fill="url(#wl)"/><polygon points="0,150 640,120 640,360 0,360" fill="url(#fl)"/><rect x="40" y="40" width="190" height="150" fill="#8FB8E6" opacity=".85"/><rect x="40" y="40" width="190" height="150" fill="none" stroke="#2B2F36" stroke-width="6"/><path d="M135 40v150M40 115h190" stroke="#2B2F36" stroke-width="4"/><rect x="420" y="95" width="190" height="95" fill="#D9DCE3"/><ellipse cx="330" cy="250" rx="52" ry="10" fill="#000" opacity=".18"/><rect x="316" y="152" width="28" height="86" rx="10" fill="#22252B"/><circle cx="330" cy="140" r="14" fill="#D8B494"/></svg>`;
const dot = (p) => `<circle cx="${p[0]}" cy="${p[1]}" r="6" fill="#fff" stroke="#2F8BFF" stroke-width="3"/>`;
function lineSvg(pts, flip) {
  if (!pts || !pts.length) return "";
  if (pts.length < 2) return pts.map(dot).join("");
  const [a, b] = pts, dx = (b[0] - a[0]) / 640, dy = (b[1] - a[1]) / 360;
  let nx = -dy, ny = dx; if (flip) { nx = -nx; ny = -ny; }
  const len = Math.hypot(nx * 640, ny * 360) || 1, ux = nx * 640 / len, uy = ny * 360 / len, mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2, ex = mx + ux * 55, ey = my + uy * 55;
  const head = `${ex},${ey} ${ex - ux * 14 - uy * 8},${ey - uy * 14 + ux * 8} ${ex - ux * 14 + uy * 8},${ey - uy * 14 - ux * 8}`;
  return `<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="#2F8BFF" stroke-width="4" stroke-linecap="round"/><line x1="${mx}" y1="${my}" x2="${ex - ux * 10}" y2="${ey - uy * 10}" stroke="#fff" stroke-width="3"/><polygon points="${head}" fill="#fff"/><text x="${ex + ux * 14}" y="${ey + uy * 14 + 5}" fill="#fff" font-size="16" font-weight="700" text-anchor="middle" stroke="#0a2a55" stroke-width="3" paint-order="stroke">IN</text>${pts.map(dot).join("")}`;
}
function dirSvg(pts, dir) {
  if (!dir) return "";
  const v = DIRS[dir], cx = pts && pts.length >= 3 ? pts.reduce((a, p) => a + p[0], 0) / pts.length : 320, cy = pts && pts.length >= 3 ? pts.reduce((a, p) => a + p[1], 0) / pts.length : 180;
  const ux = v[0], uy = v[1], x1 = cx - ux * 55, y1 = cy - uy * 55, x2 = cx + ux * 55, y2 = cy + uy * 55, head = `${x2 + ux * 16},${y2 + uy * 16} ${x2 - uy * 11},${y2 + ux * 11} ${x2 + uy * 11},${y2 - ux * 11}`;
  return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#fff" stroke-width="6" stroke-linecap="round"/><line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#2F8BFF" stroke-width="3" stroke-linecap="round"/><polygon points="${head}" fill="#fff" stroke="#2F8BFF" stroke-width="2"/><text x="${x2 + ux * 34 + (uy ? 22 : 0)}" y="${y2 + uy * 34 + 5}" fill="#fff" font-size="16" font-weight="700" text-anchor="middle" stroke="#0a2a55" stroke-width="3" paint-order="stroke">IN</text>`;
}
function videoFrame(title, pts, drawable, snap, mode, flip, pdir, camId) {
  const shape = mode === "line" ? lineSvg(pts, flip) : mode === "zone" ? `${pts && pts.length ? `<polygon points="${pts.map((p) => p.join(",")).join(" ")}" fill="rgba(0,111,255,.28)" stroke="#2F8BFF" stroke-width="3" stroke-linejoin="round"/>${pts.map(dot).join("")}` : ""}${dirSvg(pts, pdir)}` : (pts && pts.length ? `<polygon points="${pts.map((p) => p.join(",")).join(" ")}" fill="rgba(0,111,255,.28)" stroke="#2F8BFF" stroke-width="3" stroke-linejoin="round"/>${pts.map(dot).join("")}` : "");
  const poly = (pts && pts.length) || drawable ? `<svg class="draw" ${drawable ? 'id="draw"' : ""} viewBox="0 0 640 360" preserveAspectRatio="none">${shape}</svg>` : "";
  return `<div class="vframe"><div class="vhead">${ico("camera2", 22)}<span>${esc(title)}</span><span class="lk ${S.live ? "on" : ""}" id="lv-state">${S.live ? "Live" : "Picture"}</span><span class="sp"></span>${camId && can("live") ? `<button class="btn sm" type="button" id="lv-btn" data-live="${camId}">${S.live ? "Stop" : "Watch live"}</button>` : ""}</div><div class="vpic">${(S.live && S.live.url) || snap ? `<img class="snap" src="${(S.live && S.live.url) || snap}" alt="Camera picture">` : SCENE}${poly}</div></div>`;
}

/* ------------------------------------------------------------------------------------------------ Overview */
/* ---- the status lights in the top bar (CPU, worker, camera analysis) and the numbers behind them ---- */
async function loadStatus() { try { S.sys = await api.get("/admin/v1/system/status"); } catch (e) { /* keep the last numbers; the lights stay as they were */ } renderLights(); }
const lcls = (x) => (x === "crit" ? "crit" : x === "warn" ? "warn" : "");
function renderLights() {
  const d = S.sys, el = $("#lights"); if (!el) return;
  if (!d) { el.innerHTML = ""; return; }
  const items = [["cpu", "CPU", d.cpu.pct == null ? "" : Math.round(d.cpu.pct) + "%", d.cpu.level, "The processor"], ["worker", "Worker", d.worker.level === "ok" ? "" : "down", d.worker.level, "The analysis worker"], ["cams", "Analysis", `${d.cameras.analysed}/${d.cameras.expected}`, d.cameras.level, "Camera analysis"]];
  el.innerHTML = items.map((i) => `<button class="sl" type="button" data-light="${i[0]}" aria-expanded="${S.lightOpen === i[0]}" aria-label="${i[4]}: ${i[3] === "ok" ? "fine" : i[3] === "warn" ? "needs a look" : "needs action"}"><span class="led ${lcls(i[3])}"></span><span class="lt">${i[1]}</span>${i[2] ? `<b>${esc(i[2])}</b>` : ""}</button>`).join("");
  if (S.lightOpen) showLight(S.lightOpen);
}
function closeLight() { S.lightOpen = null; const p = $("#lpop"); if (p) p.hidden = true; document.querySelectorAll(".sl").forEach((b) => b.setAttribute("aria-expanded", "false")); }
function showLight(k) {
  const d = S.sys, p = $("#lpop"); if (!d || !p) return; S.lightOpen = k;
  const ago = (iso) => { if (!iso) return "Never"; const sec = Math.max(0, Math.round((Date.now() - new Date(iso)) / 1000)); return sec < 20 ? "Just now" : sec < 90 ? sec + " seconds ago" : sec < 5400 ? Math.round(sec / 60) + " minutes ago" : Math.round(sec / 3600) + " hours ago"; };
  const row = (a) => a.map((x) => `<dt>${x[0]}</dt><dd>${x[1]}</dd>`).join("");
  const pct = (x) => (x == null ? "Not measured" : Math.round(x) + "%");
  const html = { cpu: [d.cpu.level, "Processor", [["Busy", `${pct(d.cpu.pct)} of ${d.cpu.cpus} processor${d.cpu.cpus > 1 ? "s" : ""}`], ["Memory", pct(d.memory.pct) + " used"], ["Disk", pct(d.disk.pct) + " used"]]],
    worker: [d.worker.level, "Analysis worker", [["State", esc(d.worker.text)], ["Last heard", ago(d.worker.last_seen)]]],
    cams: [d.cameras.level, "Camera analysis", [["Analysed", `${d.cameras.analysed} of ${d.cameras.expected} cameras`], ["Speed", d.cameras.fps == null ? "-" : d.cameras.fps + " pictures a second"], ["Silent", d.cameras.silent ? d.cameras.silent + " for 10 minutes" : "None"], ["Messages waiting", d.messages.waiting]]] }[k];
  p.innerHTML = `<h4><span class="led ${lcls(html[0])}"></span>${html[1]}</h4><dl class="kv">${row(html[2])}</dl>${can("sys") ? '<a class="lk-link" href="#setup/sys" data-opensys>Open System health</a>' : ""}`;
  p.hidden = false;
  document.querySelectorAll(".sl").forEach((b) => b.setAttribute("aria-expanded", b.dataset.light === k));
}
setInterval(() => { if (!document.hidden && api.signedIn()) loadStatus(); }, 30000);
document.addEventListener("click", (ev) => {
  const b = ev.target.closest(".sl"); if (b) { if (S.lightOpen === b.dataset.light) return closeLight(); return showLight(b.dataset.light); }
  if (ev.target.closest("[data-opensys]")) { ev.preventDefault(); closeLight(); S.setup = "sys"; return go("setup"); }
  if (!ev.target.closest("#lpop")) closeLight();
});
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeLight(); });

function serverStrip() {
  const d = S.sys; if (!d) return "";
  const tile = (k, v, lvl, meter, hint) => `<div class="tile"><span class="k"><span class="dot ${lvl === "ok" ? "ok" : lcls(lvl)}"></span>${k}</span><span class="v ${lvl === "crit" ? "t-crit" : lvl === "warn" ? "t-warn" : ""}">${v}</span>${meter != null ? `<div class="mtb"><i class="${lcls(lvl)}" data-w="${Math.min(100, Math.round(meter))}"></i></div>` : ""}<span class="sub small">${hint}</span></div>`;
  const lv = [d.cpu.level, d.disk.level, d.worker.level, d.cameras.level, d.messages.level], worst = lv.includes("crit") ? "crit" : lv.includes("warn") ? "warn" : "ok";
  const pc = (x) => (x == null ? "-" : Math.round(x) + "%");
  const days = d.disk.days_left == null ? `${d.disk.free_gb} GB free` : `about ${d.disk.days_left >= 100 ? "100+" : Math.round(d.disk.days_left)} days left`;
  return `<div class="pn"><h3><span class="dot ${worst === "ok" ? "ok" : lcls(worst)}"></span>Server and analysis<span class="sp"></span>${can("sys") ? '<a class="lk-link" href="#setup/sys" data-opensys>Open System health</a>' : ""}</h3><div class="tiles">
    ${tile("Processor", pc(d.cpu.pct), d.cpu.level, d.cpu.pct, `${d.cpu.cpus} processor${d.cpu.cpus > 1 ? "s" : ""}`)}${tile("Memory", pc(d.memory.pct), d.memory.level, d.memory.pct, d.memory.total_gb ? d.memory.total_gb + " GB in total" : "")}${tile("Disk", pc(d.disk.pct), d.disk.level, d.disk.pct, days)}
    ${tile("Analysis worker", esc(d.worker.text), d.worker.level, null, d.worker.last_seen ? "last heard " + new Date(d.worker.last_seen).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: true }) : "never heard")}${tile("Cameras analysed", `${d.cameras.analysed} of ${d.cameras.expected}`, d.cameras.level, null, d.cameras.fps == null ? "" : d.cameras.fps + " pictures a second")}${tile("Messages waiting", d.messages.waiting, d.messages.level, null, d.messages.oldest_minutes ? `oldest ${d.messages.oldest_minutes} minutes` : "nothing stuck")}
  </div></div>`;
}
async function pageOverview() {
  await loadStatus();
  const [ov, msgs, sum] = await Promise.all([api.get("/admin/v1/overview"), api.get("/admin/v1/messaging/notifications?limit=200"), api.get("/admin/v1/messaging/summary")]);
  const cs = S.customers, cnt = (s) => cs.filter((c) => statusOf(c)[0] === s).length;
  const recs = Object.values(ov.recorders || {}).reduce((a, b) => a + b, 0);
  const all24 = Object.values(sum.last_24_hours || {}).reduce((a, ch) => { for (const [k, v] of Object.entries(ch)) { if (["sent", "delivered", "read"].includes(k)) a.sent += v; else if (k === "failed") a.failed += v; else if (["suppressed", "cancelled"].includes(k)) a.held += v; } return a; }, { sent: 0, held: 0, failed: 0 });
  const needs = cs.filter((c) => statusOf(c)[0] === "Needs a look");
  const ending = cs.filter((c) => c.days !== null && c.days <= 30 && c.days >= -30).sort((a, b) => a.days - b.days);
  const hours = Array.from({ length: 24 }, () => ({ n: 0, bad: false })), now = Date.now();
  for (const m of msgs) { const age = Math.floor((now - new Date(m.created_at)) / 36e5); if (age >= 0 && age < 24) { const b = hours[23 - age]; b.n++; if (m.status === "failed") b.bad = true; } }
  const max = Math.max(1, ...hours.map((h) => h.n));
  return `<div class="rise"><div class="title"><h1>Overview</h1><span class="sub">Everything across all customers, right now</span></div>
  <div class="stats"><button class="st" type="button" data-stat="customers|all"><b>${cs.length}</b><span>Customers</span></button><button class="st" type="button" data-stat="customers|Active"><b>${cnt("Active")}</b><span>Active</span></button><button class="st" type="button" data-stat="customers|Needs a look"><b class="t-warn">${cnt("Needs a look")}</b><span>Need a look</span></button><button class="st" type="button" data-stat="customers|all"><b>${ov.cameras_in_use}</b><span>Cameras</span></button><button class="st" type="button" data-stat="customers|all"><b>${recs}</b><span>Recorders</span></button><button class="st" type="button" data-stat="messages|Sent"><b>${all24.sent}</b><span>Sent in 24 hours</span></button><button class="st" type="button" data-stat="messages|Held back"><b class="${all24.held ? "t-warn" : ""}">${all24.held}</b><span>Held back</span></button><button class="st" type="button" data-stat="messages|Failed"><b class="${all24.failed ? "t-crit" : ""}">${all24.failed}</b><span>Failed to send</span></button></div>
  ${serverStrip()}
  <div class="cc"><div>
   <div class="pn"><h3>Needs a look</h3>${needs.length ? `<table class="tbl"><thead><tr><th class="dotc"></th><th>Customer</th><th>What is wrong</th></tr></thead><tbody>${needs.map((c) => `<tr class="click" data-cust="${c.id}"><td class="dotc"><span class="dot warn"></span></td><td class="main-c">${esc(c.name)}</td><td>${esc(whyOf(c))}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">Nothing needs a look.</div>`}</div>
   <div class="pn"><h3>Ending soon <span class="sub light">next 30 days, plus ended in the last 30</span></h3>${ending.length ? `<table class="tbl"><thead><tr><th class="dotc"></th><th>Customer</th><th>Ends on</th><th>Days left</th><th></th></tr></thead><tbody>${ending.map((c) => `<tr><td class="dotc"><span class="dot ${c.days < 0 ? "crit" : c.days <= 7 ? "warn" : ""}"></span></td><td class="main-c"><button class="lnk dark" type="button" data-cust="${c.id}">${esc(c.name)}</button></td><td class="num">${fmtDate(c.endsOn)}</td><td class="num ${c.days < 0 ? "t-crit" : c.days <= 7 ? "t-warn" : ""}">${c.days < 0 ? "Ended" : c.days}</td><td><button class="btn sm" type="button" data-extendc="${c.id}" ${can("expiry") ? "" : "disabled"}>Extend</button></td></tr>`).join("")}</tbody></table>` : `<div class="empty">No end dates in the next 30 days.</div>`}</div>
  </div><div>
   <div class="pn"><h3>Latest messages <button class="lnk" type="button" data-go="messages">See all</button></h3>${msgs.length ? `<table class="tbl"><tbody>${msgs.slice(0, 6).map((m) => `<tr><td class="dotc"><span class="dot ${m.status === "failed" ? "crit" : "ok"}"></span></td><td class="main-c">${esc(m.title || humanize((m.event || "message").split(".").pop()))}<br><span class="sub small">${esc(m.tenant)}</span></td><td class="num sub">${fmtTime(m.created_at)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">No messages yet.</div>`}</div>
   <div class="pn"><h3>Messages in the last 24 hours</h3><div class="pad"><div class="bars" role="img" aria-label="Messages per hour, the last 24 hours">${hours.map((b) => `<i class="${b.bad ? "bad" : ""}" data-h="${Math.max(4, Math.round(b.n / max * 88))}"></i>`).join("")}</div><div class="axis"><span>24 h ago</span><span>18 h</span><span>12 h</span><span>6 h</span><span>Now</span></div></div></div>
  </div></div></div>`;
}

/* ------------------------------------------------------------------------------------------------ Customers */
function phoneCustomers(list) {
  const chips = [["all", "All"], ...STATUSES.map((x) => [x, x])];
  return `<div class="rise stack"><label class="search">${ico("search")}<span class="sr">Search customers</span><input id="q" placeholder="Search customers" value="${esc(S.q)}" autocomplete="off"></label>
  <div class="pchips">${chips.map((c) => `<button class="chip" type="button" data-filter="${c[0]}" aria-pressed="${S.filter === c[0]}">${c[1]}</button>`).join("")}</div>
  ${list.length ? list.map((c) => { const st = statusOf(c), k = st[1] === "mute" ? "" : st[1]; return `<button class="card" type="button" data-cust="${c.id}"><span class="dot ${k}"></span><span class="ct"><b>${esc(c.name)}</b><span class="sub small">${esc(c.place)}${c.place ? " · " : ""}${c.cams} camera${c.cams === 1 ? "" : "s"}</span>${whyOf(c) ? `<span class="small t-${st[1]}">${esc(whyOf(c))}</span>` : ""}</span><span class="tag ${k}">${st[0]}</span>${ico("chev", 16)}</button>`; }).join("") : `<div class="empty">${S.customers.length ? "No customers match. Clear the search or the filter." : "No customers yet."}</div>`}
  <div class="sub small pdesk">Adding a customer is best done on a larger screen.</div></div>`;
}
function pageCustomers() {
  const q = S.q.toLowerCase();
  const list = S.customers.filter((c) => (!q || (c.name + c.place).toLowerCase().includes(q)) && (S.filter === "all" || statusOf(c)[0] === S.filter));
  if (PH.matches) return phoneCustomers(list);
  const body = !list.length ? `<div class="empty">${S.customers.length ? "No customers match. Clear the search or the filter." : "No customers yet. Add the first one."}</div>` : S.view === "list" ?
    `<div class="scroll"><table class="tbl"><thead><tr><th class="dotc"></th><th class="ico">Type</th><th>Name</th><th>Status</th><th>Place</th><th>Cameras</th><th>Messages go to</th><th>Last message</th><th>Ends on</th></tr></thead><tbody>${list.map((c) => { const st = statusOf(c); return `<tr class="click" data-cust="${c.id}"><td class="dotc"><span class="dot ${st[1] === "mute" ? "" : st[1]}"></span></td><td class="ico"><span class="glyph">${ico("store", 20)}</span></td><td class="main-c">${esc(c.name)}</td><td class="t-${st[1]}">${st[0]}${whyOf(c) ? `<br><span class="sub small">${esc(whyOf(c))}</span>` : ""}</td><td>${esc(c.place)}</td><td class="num">${c.cams}</td><td>${esc(peopleShort(c.people))}</td><td class="num">${fmtTime(c.last)}</td><td class="num">${fmtDate(c.endsOn)}</td></tr>`; }).join("")}</tbody></table></div>` :
    `<div class="grid">${list.map((c) => { const st = statusOf(c); return `<button class="tile" type="button" data-cust="${c.id}"><div class="pic"><span class="cc2">${c.cams} cameras</span>${ico("store", 30)}</div><div class="tb"><b>${esc(c.name)}</b><span class="t-${st[1]}">${st[0]}</span><span> · ${esc(c.place)}</span></div></button>`; }).join("")}</div>`;
  return `<div class="rise"><div class="bar"><label class="search">${ico("search")}<span class="sr">Search customers</span><input id="q" placeholder="Search" value="${esc(S.q)}"></label>${S.filter !== "all" ? `<span class="tag brand">${S.filter}</span>` : ""}<span class="sp"></span>
   <div class="tools rel"><button class="tool" type="button" data-view="list" aria-pressed="${S.view === "list"}" aria-label="List">${ico("list")}</button><button class="tool" type="button" data-view="grid" aria-pressed="${S.view === "grid"}" aria-label="Grid">${ico("grid")}</button><button class="tool" type="button" data-pop aria-pressed="${S.pop || S.filter !== "all"}" aria-label="Filter">${ico("filter")}</button>
   ${S.pop ? `<div class="pop">${[["all", "All"]].concat(STATUSES.map((s) => [s, s])).map((s) => `<button type="button" data-filter="${s[0]}" aria-pressed="${S.filter === s[0]}">${s[1]}<span class="sub">${s[0] === "all" ? S.customers.length : S.customers.filter((c) => statusOf(c)[0] === s[0]).length}</span></button>`).join("")}</div>` : ""}</div>
   <button class="btn pri" type="button" data-go="new" ${can("newCustomer") ? "" : "disabled title='Owner, Admin or Onboarding only'"}>${ico("plus")}New customer</button></div>${body}</div>`;
}

/* ------------------------------------------------------------------------------------------------ form sections (shared by New customer and a customer's own page) */
const PHONE_OK = (v) => /^(\+?91)?0?[6-9]\d{9}$/.test(v.replace(/[\s\-().]/g, ""));
function secShop(f) { return `<div class="f"><label for="f-name">Customer name<small>Business or company name</small></label><input class="in" id="f-name" data-bind="name" value="${esc(f.name)}" placeholder="For example, Lakshmi Jewellers" autocomplete="off">
  <label for="f-oname">Owner name<small>The main contact</small></label><input class="in" id="f-oname" data-bind="ownerName" value="${esc(f.ownerName)}" placeholder="Full name" autocomplete="off">
  <span class="lb">Owner mobile<small>Used later to sign in. One number can belong to one customer only.</small></span><div class="stack">${f.ownerPhones.map((p, i) => `<div class="row"><input class="in" id="f-ph${i}" aria-label="Owner mobile ${i + 1}" data-oph="${i}" value="${esc(p)}" placeholder="${i ? "Another number (optional)" : "98765 43210"}" inputmode="tel" autocomplete="off">${i ? `<button class="ibtn" type="button" data-delph="${i}" aria-label="Remove number">${ico("trash")}</button>` : ""}</div>`).join("")}${f.ownerPhones.length < 3 ? `<div><button class="btn sm" type="button" data-addph>${ico("plus", 15)}Add another number</button></div>` : ""}</div>
  <label for="f-oemail">Owner email<small>Optional</small></label><input class="in" id="f-oemail" type="email" data-bind="ownerEmail" value="${esc(f.ownerEmail)}" placeholder="name@company.com" autocomplete="off">
  <label for="f-place">City</label><input class="in" id="f-place" data-bind="place" value="${esc(f.place)}" placeholder="Town or city" autocomplete="off"></div>`; }
/* What is missing on step 1 (null when it is complete): the field to point at and a plain sentence. */
function ownerProblem(f) {
  if (!f.name.trim()) return ["f-name", "Enter the customer name."];
  if (!f.ownerName.trim()) return ["f-oname", "Enter the owner's name."];
  if (!f.ownerPhones[0]?.trim()) return ["f-ph0", "Enter the owner's mobile number."];
  for (let i = 0; i < f.ownerPhones.length; i++) if (f.ownerPhones[i].trim() && !PHONE_OK(f.ownerPhones[i])) return ["f-ph" + i, "That mobile number does not look right. Use 10 digits, like 98765 43210."];
  if (f.ownerEmail.trim() && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(f.ownerEmail.trim())) return ["f-oemail", "That email does not look right."];
  if (!f.place.trim()) return ["f-place", "Enter the city."];
  return null;
}
function pointAt(id, msg) { toast(msg, true); const el = document.getElementById(id); if (el) { el.classList.add("bad"); el.focus(); } }
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

function secSummaries(f) { return `<h2 class="h2 pt8">Daily and weekly summary</h2><div class="f"><span class="lb">Daily summary<small>One message each morning</small></span><div class="row">${sw("f-daily", f.daily, false, "daily")}<label class="sr" for="f-dat">Daily time</label><input class="in time" id="f-dat" type="time" data-bind="dailyAt" value="${f.dailyAt}"></div>
  <span class="lb">Weekly summary<small>Sent once a week, on the day set for all shops</small></span><div class="row">${sw("f-weekly", f.weekly, false, "weekly")}</div></div>`; }

/* ------------------------------------------------------------------------------------------------ New customer */
const blankForm = () => ({ _id: null, name: "", place: "", ownerName: "", ownerPhones: [""], ownerEmail: "", _pn: "", _pm: "", people: [{ id: null, n: "", m: "", a: true, s: true }], hmode: "set", hours: DAYS.map((d, i) => ({ open: i < 6, from: "09:00", to: "21:00" })), rtype: "dahua", rname: "", daily: true, dailyAt: "08:00", weekly: true, expiry: plusDays(365), done: {} });
function pageNew() {
  const f = S.form, i = NSTEPS.findIndex((s) => s[0] === S.step), last = i === NSTEPS.length - 1;
  const body = { shop: secShop, people: secPeople, hours: secHours, cams: secRecorderNew, summary: secSummary }[S.step](f);
  return `<div class="rise"><div class="title"><h1>${NSTEPS[i][1]}</h1><span class="tag brand">Step ${i + 1} of ${NSTEPS.length}</span></div><div class="form">${body}
   <div class="actions">${last ? `<button class="btn pri" type="button" data-create>Create customer</button>` : `<button class="btn pri" type="button" data-next>Next</button>`}${i ? `<button class="btn" type="button" data-prev>Back</button>` : ""}<button class="btn bare" type="button" data-go="customers">Cancel</button></div></div></div>`;
}
async function createCustomer() {
  const f = S.form;
  const bad = ownerProblem(f);
  if (bad) { S.step = "shop"; render(); return pointAt(...bad); }
  const people = f.people.filter((p) => p.n.trim() || p.m.trim());
  if (people.some((p) => !p.n.trim() || !p.m.trim())) { S.step = "people"; render(); return toast("Each person needs a name and a mobile number.", true); }
  const btn = document.querySelector("[data-create]"); if (btn) btn.disabled = true;
  let t;
  try {
    if (!S.plans.length) S.plans = (await api.get("/admin/v1/plans")).items || (await api.get("/admin/v1/plans"));
    const plan = S.plans.find((p) => p.anpr && !p.alerts_chosen) || S.plans.find((p) => p.anpr) || S.plans[0];       // there is no plan to choose: every customer gets everything
    t = await api.post("/admin/v1/tenants", { name: f.name.trim(), city: f.place.trim(), owner_name: f.ownerName.trim(), owner_phones: f.ownerPhones.map((p) => p.trim()).filter(Boolean), owner_email: f.ownerEmail.trim() || null, plan_id: plan.id, camera_limit: 64, ends_on: f.expiry || null });
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
/* What the customer page shows is loaded once and kept. The light part (people, hours, events, cameras, recorders, which pages exist) comes first; the marked areas and
   plate/line data of every camera are only fetched when the Events list needs them. Opening another section reuses what is loaded and refreshes it quietly. */
async function fetchCore(id) {
  const shop = `/admin/v1/tenants/${id}/shop`;
  const [people, hours, events, cams, recs, views] = await Promise.all([api.get(shop + "/people"), api.get(shop + "/hours"), api.get(shop + "/events"), api.get(shop + "/cameras"), api.get(`/admin/v1/recorders?tenant_id=${id}`), api.get(shop + "/views").catch(() => null)]);
  return { people, hours, events, cams, recs, views };
}
async function fetchGeo(id, cams) {
  const shop = `/admin/v1/tenants/${id}/shop`;
  const [areas, zones] = await Promise.all([Promise.all(cams.map(async (c) => [c.id, await api.get(`${shop}/cameras/${c.id}/areas`).catch(() => [])])), Promise.all(cams.map(async (c) => [c.id, await api.get(`${shop}/cameras/${c.id}/zones`).catch(() => [])]))]);
  return { areas: Object.fromEntries(areas), zones: Object.fromEntries(zones) };
}
async function loadCurrent(id) {
  const wantGeo = !!(S.cur && S.cur.id === id && S.cur.geo);                      // a reload after a change keeps what was already loaded
  const { people, hours, events, cams, recs, views } = await fetchCore(id);
  const c = cust(id);
  const rules = events.filter((e) => e.saved);
  const alertRules = rules.filter((e) => e.enabled && !["daily_summary", "weekly_summary"].includes(e.type)), sumRules = rules.filter((e) => ["daily_summary", "weekly_summary"].includes(e.type));
  const inAlerts = (pid) => !alertRules.length || alertRules.some((e) => e.recipient_ids.includes(pid)), inSummary = (pid) => !sumRules.length || sumRules.some((e) => e.recipient_ids.includes(pid));
  const daily = events.find((e) => e.type === "daily_summary"), weekly = events.find((e) => e.type === "weekly_summary");
  const f = { _id: id, name: c.name, place: c.place, lang: c.lang, people: people.map((p) => ({ id: p.id, n: p.name, m: p.phone || "", a: inAlerts(p.id), s: inSummary(p.id) })),
    hmode: hours.set ? "set" : "always", hours: hours.days.map((d) => ({ open: d.open, from: d.from, to: d.to })), daily: daily ? daily.enabled : true, dailyAt: String(daily?.params?.hour ?? 8).padStart(2, "0") + ":00", weekly: weekly ? weekly.enabled : true, expiry: c.endsOn || "", done: {} };
  const g = wantGeo ? await fetchGeo(id, cams) : { areas: {}, zones: {} };
  S.viewsByCust[id] = views;
  S.cur = { id, f, people, events, cams, recs, areas: g.areas, zones: g.zones, geo: wantGeo, views, channels: {}, at: Date.now() };
}
async function ensureGeo() {
  const C = S.cur; if (!C || C.geo) return;
  const g = await fetchGeo(C.id, C.cams);
  if (S.cur === C) { C.areas = g.areas; C.zones = g.zones; C.geo = true; }
}
/* Fetch again in the background and update the page only when something it shows has changed (never while a window is open) */
let skipRefresh = false;
const sigOf = (o, g) => JSON.stringify([o.events.map((e) => [e.type, e.enabled, e.saved, e.label, e.params, e.recipient_ids]), o.cams.map((c) => [c.id, c.name, c.enabled, c.use_footfall, c.use_anpr, c.use_areas, c.recorder_id]), o.recs.map((r) => [r.id, r.name, r.status, r.device_id]), o.views, g ? [g.areas, g.zones] : null]);
async function refreshCurrent() {
  const C = S.cur; if (!C || C.refreshing || Date.now() - (C.at || 0) < 8000) return;
  C.refreshing = true;
  try {
    const core = await fetchCore(C.id), g = C.geo ? await fetchGeo(C.id, core.cams) : null;
    if (S.cur !== C) return;
    C.at = Date.now();
    if (sigOf(core, g) === sigOf(C, C.geo ? { areas: C.areas, zones: C.zones } : null)) return;
    Object.assign(C, { events: core.events, cams: core.cams, recs: core.recs, views: core.views, people: core.people }); if (g) { C.areas = g.areas; C.zones = g.zones; }
    S.viewsByCust[C.id] = core.views;
    if (S.route === "customer" && ["events", "cams", "count", "vehicles", "health"].includes(S.section) && !$("#layer").innerHTML.trim()) { skipRefresh = true; render(true); } else side();
  } catch { /* the page keeps what it has */ } finally { C.refreshing = false; }
}
async function pageCustomer() {
  if (!S.cur || S.cur.id !== S.cid) await loadCurrent(S.cid);
  else if (skipRefresh) skipRefresh = false;
  else refreshCurrent();                                                         // the page below uses what is already loaded
  if (S.section === "events") await ensureGeo();
  const c = cust(S.cid), st = statusOf(c), f = S.cur.f;
  const titles = { health: "Health", people: "Who gets messages", hours: "Business hours", cams: "Recorder and cameras", events: "Events", log: "Messages sent", count: "People count", vehicles: "Vehicles" };
  if (S.section === "count" && !S.cur.views?.people.show || S.section === "vehicles" && !S.cur.views?.vehicles.show) S.section = "events";          // nothing counts people (or reads plates) and nothing is left to show
  let body;
  if (S.section === "events") body = secEvents();
  else if (S.section === "cams") body = secCams();
  else if (S.section === "health") body = await secHealth();
  else if (S.section === "log") { await loadLog(S.cid, false, true); body = logTable(false); }
  else if (S.section === "count") { await loadCount(); body = secCount(); }
  else if (S.section === "vehicles") { await loadVehicles(false, S.veh.hold); S.veh.hold = false; body = secVehicles(); }
  else body = (S.section === "hours" ? secHours(f) + secSummaries(f) : secPeople(f)) + `<div class="actions"><button class="btn pri" type="button" data-save>Save changes</button></div>`;
  return `<div class="rise"><div class="title"><h1>${esc(c.name)}</h1><span class="t-${st[1]} bold"><span class="dot ${st[1] === "mute" ? "" : st[1]}"></span> ${st[0]}</span><span class="sub">${esc(c.place)} · ends ${fmtDate(c.endsOn)}</span><span class="sp"></span><span class="hb">
   <button class="btn sm" type="button" data-testmsg>${ico("send", 15)}Send test message</button><button class="btn sm" type="button" data-editc ${can("editc") ? "" : "disabled title='Not for your role'"}>${ico("edit", 15)}Edit</button><button class="btn sm" type="button" data-pausec ${can("pause") ? "" : "disabled title='Not for your role'"}>${ico("pause", 15)}${c.status === "suspended" ? "Resume" : "Pause"}</button><button class="btn sm" type="button" data-extend ${can("expiry") ? "" : "disabled title='Owner or Admin only'"}>${ico("clock", 15)}Change end date</button><button class="btn sm danger" type="button" data-delc ${can("del") ? "" : "disabled title='Owner only'"}>${ico("trash", 15)}Delete</button></span></div>
   ${c.ownerPhones.length ? "" : `<div class="whyline t-warn">The owner's mobile number is missing. Add it with Edit.</div>`}
   ${whyOf(c) ? `<div class="whyline ${st[1] === "warn" ? "t-warn" : st[1] === "crit" ? "t-crit" : "sub"}">${esc(whyOf(c))}</div>` : ""}
   <div class="pchips ponly" role="tablist" aria-label="This customer">${[...custSecs()].sort((a, b) => (b[0] === "health") - (a[0] === "health")).map((x) => `<button class="chip" type="button" role="tab" data-sec="${x[0]}" aria-selected="${S.section === x[0]}">${x[3]}</button>`).join("")}</div>
   <h2 class="h2">${titles[S.section]}</h2><div class="form ${["events", "cams", "log", "count", "vehicles", "health"].includes(S.section) ? "full" : ""}">${body}</div>${S.section === "events" ? "" : '<div class="ponly sub small pdesk">Editing this customer, changing the end date and deleting are best done on a larger screen.</div>'}</div>`;
}
/* A recorder's window on the Health page: connection, disks, recordings, clock, hardware and the last day's connection history */
const hsize = (mb) => (mb == null ? "-" : mb >= 1e6 ? `${+(mb / 1e6).toFixed(1)} TB` : `${Math.round(mb / 1e3)} GB`);
function openRecorder(n) {
  const r = S.health.recorders[n], d = r.detail, lv = (x) => (x === "crit" ? "crit" : x === "warn" ? "warn" : "ok");
  const ago = (iso) => { if (!iso) return "Never"; const sec = Math.max(0, Math.round((Date.now() - new Date(iso)) / 1000)); return sec < 20 ? "just now" : sec < 90 ? `${sec} seconds ago` : sec < 5400 ? `${Math.round(sec / 60)} minutes ago` : sec < 172800 ? `${Math.round(sec / 3600)} hours ago` : `${Math.round(sec / 86400)} days ago`; };
  const when = (iso) => { const x = new Date(iso); return isNaN(x) ? "" : x.toLocaleString("en-IN", { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit", hour12: true }); };
  const up = (sec) => { if (sec == null) return "Not reported"; const dd = Math.floor(sec / 86400), hh = Math.floor((sec % 86400) / 3600); return dd ? `${dd} day${dd === 1 ? "" : "s"} ${hh} hour${hh === 1 ? "" : "s"}` : hh ? `${hh} hour${hh === 1 ? "" : "s"}` : `${Math.max(1, Math.round(sec / 60))} minutes`; };
  const kv = (rows) => `<dl class="kv">${rows.map((x) => `<dt>${x[0]}</dt><dd>${x[1]}</dd>`).join("")}</dl>`;
  const nr = '<span class="t-mute">Not reported</span>';
  const tot = d.disks.reduce((a, x) => a + (x.total_mb || 0), 0), free = d.disks.reduce((a, x) => a + (x.free_mb || 0), 0), pc = tot ? Math.round((1 - Math.min(free, tot) / tot) * 100) : null;
  const state = r.status === "offline" ? ["Offline", "crit"] : r.status === "never" ? ["Not connected yet", ""] : lv(r.level) === "ok" ? ["Healthy", "ok"] : ["Needs a look", "warn"];
  let body = `<div class="sec-h">Connection</div>` + kv([["Status", `<span class="${r.status === "online" ? "t-ok" : r.status === "offline" ? "t-crit" : "t-mute"}">${r.status === "never" ? "Not connected yet" : humanize(r.status)}</span>, last heard ${ago(r.last_seen)}`], ["Connects from", esc(d.address || "-")], ["Firmware", d.firmware ? esc(d.firmware) : nr], ["Running for", up(d.uptime_seconds)], ["Restarts today", r.restarts_today], ["Dropped connections", `${r.drops_last_hour} in the last hour`]]);
  body += `<div class="sec-h">Storage</div>`;
  if (r.disks.state == null) body += `<div class="sub">Not read yet. The recorder has to be online.</div>`;
  else if (!d.disks.length) body += `<div class="note warn">The recorder reports no disk.</div>`;
  else body += `<div class="sub mb6">${d.disks.length} disk${d.disks.length > 1 ? "s" : ""}, ${hsize(tot)} in total${pc != null ? `, ${pc}% full` : ""}</div>` + (pc != null ? `<div class="mtb"><i class="${pc >= 90 ? "crit" : pc >= 80 ? "warn" : ""}" data-w="${pc}"></i></div>` : "") + d.disks.map((k) => { const used = k.total_mb ? Math.round((1 - Math.min(k.free_mb, k.total_mb) / k.total_mb) * 100) : null, st = k.state === "fault" ? ["Failed", "crit"] : k.state === "wearing" ? ["Wearing out", "warn"] : ["Healthy", "ok"];
    return `<div class="disk"><div class="row"><b class="dark">Disk ${(k.number ?? 0) + 1}</b><span class="tag ${st[1]}">${st[0]}</span><span class="sp"></span><span>${hsize(k.total_mb)}</span></div><div class="sub">${k.model ? esc(k.model) : "Make not reported"}${k.serial ? " · " + esc(k.serial) : ""}</div><div class="sub">${hsize(k.free_mb)} free${used != null ? ` (${used}% used)` : ""}${k.life_percent != null ? ` · ${Math.round(k.life_percent)}% of its life left` : ""}</div>${used != null ? `<div class="mtb"><i class="${k.state === "fault" || used >= 90 ? "crit" : used >= 80 ? "warn" : ""}" data-w="${k.state === "fault" ? 100 : used}"></i></div>` : ""}</div>`; }).join("")
    + (d.disks.some((k) => k.state === "fault") ? `<div class="note crit">A disk has failed. Recordings may be lost. Replace it as soon as you can.</div>` : d.disks.some((k) => k.state === "wearing") ? `<div class="note warn">A disk is wearing out. Plan to replace it.</div>` : "");
  body += `<div class="sec-h">Recordings</div>` + kv([["Recordings from", r.oldest_recording ? esc(when(r.oldest_recording)) : nr], ["Days held", r.days_held != null ? `About ${r.days_held} days` : nr]]);
  if (d.channels.length) body += `<div class="scroll"><table class="tbl mini"><thead><tr><th>Channel</th><th>Camera</th><th>Online</th><th>Recording</th></tr></thead><tbody>${d.channels.map((c) => `<tr><td>${c.number}</td><td>${c.camera ? esc(c.camera) : `<span class="t-mute">${esc(c.name || "Not added")}</span>`}</td><td class="${c.online === false ? "t-crit" : c.online ? "t-ok" : "t-mute"}">${c.online == null ? "-" : c.online ? "Yes" : "No"}</td><td class="${c.recording === false ? "t-warn" : ""}">${c.recording == null ? "-" : c.recording ? "Yes" : "No"}</td></tr>`).join("")}</tbody></table></div>`;
  body += `<div class="sec-h">Clock</div>` + kv([["Recorder time", d.clock_local ? esc(when(d.clock_local)) : nr], ["Compared with true time", r.clock_out_minutes == null ? nr : r.clock_out_minutes < 5 ? `<span class="t-ok">Correct</span> (${r.clock_out_minutes} min out)` : `<span class="t-warn">${r.clock_out_minutes} minutes out. Set its time on the recorder.</span>`]]);
  body += `<div class="sec-h">Hardware</div>` + kv([["Temperature", d.temperature_c == null ? nr : `<span class="${d.temperature_c >= 60 ? "t-warn" : ""}">${d.temperature_c} °C</span>${d.temperature_c >= 60 ? " (too hot)" : " (normal)"}`], ["Fan", d.fan_ok == null ? nr : d.fan_ok ? "Working" : '<span class="t-crit">Not working</span>'], ["Camera power (PoE)", d.poe_fault == null ? nr : d.poe_fault ? '<span class="t-crit">Fault</span>' : "Fine"]]);
  body += `<div class="sec-h">Connection history, last 24 hours</div>` + (d.history.length ? d.history.map((e) => `<div class="hev"><time>${new Date(e.at).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", hour12: true })}</time><span>${esc(e.text)}</span></div>`).join("") : `<div class="sub">No drops or restarts.</div>`);
  $("#layer").innerHTML = `<div class="shade" data-close></div><aside class="drawer ev" role="dialog" aria-label="${esc(r.name)}"><div class="dh"><div><h2>${esc(r.name)}</h2><div class="sub">${esc(humanize(d.brand || ""))} · ${esc(humanize(d.protocol || ""))} · ${esc(d.device_id)}</div></div><span class="tag ${state[1]}">${state[0]}</span><button class="ibtn" type="button" id="dclose" aria-label="Close">${ico("x")}</button></div><div class="dbody">${body}</div><div class="df"><button class="btn" type="button" id="dclose2">Close</button></div></aside>`;
  document.querySelectorAll("#layer .mtb i[data-w]").forEach((b) => { b.style.width = b.dataset.w + "%"; });
}

/* ------------------------------------------------------------------------------------------------ Health of one customer */
async function secHealth() {
  const h = await api.get(`/admin/v1/tenants/${S.cid}/shop/health`);
  const lv = (x) => (x === "crit" ? "crit" : x === "warn" ? "warn" : "ok");
  const ago = (sec) => (sec == null ? "Never" : sec < 20 ? "Just now" : sec < 90 ? `${sec} seconds ago` : sec < 5400 ? `${Math.round(sec / 60)} minutes ago` : sec < 172800 ? `${Math.round(sec / 3600)} hours ago` : `${Math.round(sec / 86400)} days ago`);
  const agoIso = (iso) => (iso ? ago(Math.max(0, Math.round((Date.now() - new Date(iso)) / 1000))) : "Never");
  const when = (iso) => { const d = new Date(iso); return isNaN(d) ? "" : d.toLocaleString("en-IN", { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit", hour12: true }); };
  const diskText = (d) => ({ ok: `Healthy${d.percent_full != null ? ` · ${d.percent_full}% full` : ""}`, fault: "A disk has failed", wearing: "Wearing out", none: "No disk found" }[d.state] || "Not read yet");
  const diskCls = (d) => (d.state === "fault" ? "t-crit" : d.state === "wearing" ? "t-warn" : d.state === "ok" ? "" : "t-mute");
  const statusText = (r) => (r.status === "never" ? "Not connected yet" : humanize(r.status)), statusCls = (r) => (r.status === "online" ? "t-ok" : r.status === "offline" ? "t-crit" : "t-mute");
  const size = (mb) => (mb >= 1e6 ? `${+(mb / 1e6).toFixed(1)} TB` : `${Math.round(mb / 1e3)} GB`);
  S.health = h;
  const max = Math.max(1, ...h.days.map((d) => d.problems)), c = h.counts, m = h.messages;
  const dayName = (iso) => new Date(iso + "T00:00:00").toLocaleDateString("en-IN", { weekday: "short" });
  return `<div class="stack"><div class="hv ${lv(h.overall)}"><span class="dot ${lv(h.overall)}"></span><div><b>${h.overall === "ok" ? "Everything is fine" : h.issues.length === 1 ? "1 thing needs a look" : h.issues.length + " things need a look"}</b>${h.overall === "ok" ? `<div class="sub">${c.recorders_online} of ${c.recorders} recorders are online and ${c.cameras_working} of ${c.cameras} cameras are sending pictures.</div>` : `<ul>${h.issues.map((i) => `<li><span class="dot ${lv(i.level)}"></span>${esc(i.text)}</li>`).join("")}</ul>`}</div></div>
  <div class="stats"><div class="st"><b>${c.recorders_online} of ${c.recorders}</b><span>Recorders online</span></div><div class="st"><b class="${c.cameras_working === c.cameras ? "" : "t-warn"}">${c.cameras_working} of ${c.cameras}</b><span>Cameras working</span></div><div class="st"><b>${nf(m.sent_24h)}</b><span>Messages sent, 24 hours</span></div><div class="st"><b class="${m.failed_24h ? "t-crit" : ""}">${nf(m.failed_24h)}</b><span>Failed, 24 hours</span></div></div>
  <div class="pn"><h3>Recorders <span class="sub light">click one for everything about it</span></h3>${h.recorders.length ? `<div class="scroll"><table class="tbl"><thead><tr><th class="dotc"></th><th>Recorder</th><th>Status</th><th>Last heard</th><th>Storage</th><th>Recordings from</th><th class="end"></th></tr></thead><tbody>${h.recorders.map((r, n) => { const d = r.detail, tot = d.disks.reduce((a, x) => a + (x.total_mb || 0), 0), pc = r.disks.percent_full; return `<tr class="click" tabindex="0" data-hrec="${n}"><td class="dotc"><span class="dot ${r.status !== "online" ? (r.status === "offline" ? "crit" : "") : lv(r.level) === "ok" ? "ok" : lv(r.level)}"></span></td><td class="main-c">${esc(r.name)}</td><td class="${statusCls(r)}">${statusText(r)}</td><td class="sub">${agoIso(r.last_seen)}</td><td>${d.disks.length ? `${d.disks.length} disk${d.disks.length > 1 ? "s" : ""} · ${size(tot)}${pc != null ? ` · ${pc}% full<span class="mtb sm"><i class="${pc >= 90 ? "crit" : pc >= 80 ? "warn" : ""}" data-w="${pc}"></i></span>` : ""}${r.disks.state === "fault" ? ' <span class="tag crit">Failed</span>' : r.disks.state === "wearing" ? ' <span class="tag warn">Wearing out</span>' : ""}` : '<span class="t-mute">Not read yet</span>'}</td><td>${r.oldest_recording ? `${esc(when(r.oldest_recording).split(",").slice(0, 2).join(","))}<span class="sub"> · ${r.days_held ?? "?"} days</span>` : r.days_held != null ? `About ${r.days_held} days` : '<span class="t-mute">-</span>'}</td><td class="end t-mute">${ico("chev", 14)}</td></tr>`; }).join("")}</tbody></table></div>` : `<div class="empty">No recorder yet.</div>`}</div>
  <div class="pn"><h3>Cameras</h3>${h.cameras.length ? `<div class="scroll"><table class="tbl"><thead><tr><th class="dotc"></th><th>Camera</th><th>Recorder</th><th>Last picture</th><th>Recording</th><th>Picture</th></tr></thead><tbody>${h.cameras.map((k) => `<tr><td class="dotc"><span class="dot ${k.working ? "ok" : "crit"}"></span></td><td class="main-c">${esc(k.name)}</td><td>${esc(k.recorder || "-")}</td><td class="${k.working ? "sub" : "t-crit"}">${k.recorder_offline ? "Recorder offline" : ago(k.seconds_since_picture)}</td><td>${k.recording == null ? "-" : k.recording ? "Yes" : "No"}</td><td class="${k.picture ? "t-warn" : ""}">${k.picture ? esc(humanize(k.picture)) : "Fine"}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">No camera is switched on yet.</div>`}</div>
  <div class="pn"><h3>Problems in the last 7 days</h3><div class="pad"><div class="bars" role="img" aria-label="Problems each day, the last 7 days">${h.days.map((d) => `<i class="${d.problems ? "bad" : "zero"}" data-h="${Math.max(2, Math.round(d.problems / max * 88))}" title="${d.problems}"></i>`).join("")}</div><div class="dlab">${h.days.map((d) => `<span>${esc(dayName(d.date))}<b>${d.problems || ""}</b></span>`).join("")}</div></div></div></div>`;
}

function secCams() {
  const { recs, cams, channels } = S.cur;
  return `<div class="stack"><div class="bar flush"><span class="sub">The recorder sends its cameras once it has connected to our server.</span><span class="sp"></span><button class="btn pri" type="button" data-addrec ${can("events") ? "" : "disabled title='Not for your role'"}>${ico("plus")}Add recorder</button></div>
  ${recs.length ? `<div class="scroll"><table class="tbl"><thead><tr><th class="dotc"></th><th>Recorder</th><th>Brand</th><th>Status</th><th>Last seen</th><th></th></tr></thead><tbody>${recs.map((r) => `<tr><td class="dotc"><span class="dot ${r.status === "online" ? "ok" : r.status === "offline" ? "crit" : ""}"></span></td><td class="main-c">${esc(r.name || r.device_id)}</td><td>${esc(humanize(r.brand || ""))}</td><td class="${r.status === "online" ? "t-ok" : r.status === "offline" ? "t-crit" : "t-mute"}">${r.status === "never" ? "Not connected yet" : humanize(r.status)}</td><td class="num">${fmtTime(r.last_heartbeat_at)}</td><td><button class="btn sm" type="button" data-findcams="${r.id}">Find cameras</button> <button class="btn sm" type="button" data-showconn="${r.id}">Details</button> <button class="btn sm" type="button" data-editrec="${r.id}" ${can("recs") ? "" : "disabled title='Not for your role'"}>Edit</button></td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">No recorder yet. Add one, then type its details into the recorder.</div>`}
  ${cams.length ? `<div class="sec-h">Cameras</div><div class="scroll"><table class="tbl"><thead><tr><th class="dotc"></th><th>Camera</th><th>Recorder</th><th>Used for</th><th></th></tr></thead><tbody>${cams.map((c) => { const r = recs.find((x) => x.id === c.recorder_id); return `<tr><td class="dotc"><span class="dot ${r && r.status === "offline" ? "crit" : c.enabled ? "ok" : ""}"></span></td><td class="main-c">${esc(c.name)}</td><td>${esc(r ? (r.name || r.device_id) : "-")}</td><td>${usedFor(c)}</td><td class="end"><button class="btn sm" type="button" data-rencam="${c.id}" ${can("recs") ? "" : "disabled title='Not for your role'"}>${ico("edit", 14)}Rename</button> <button class="btn sm" type="button" data-campic="${c.id}" ${can("live") ? "" : "disabled title='Only the Owner and Admin can watch live'"}>${ico("play", 14)}Watch live</button></td></tr>`; }).join("")}</tbody></table></div>` : ""}
  ${Object.entries(channels).map(([rid, list]) => `<div class="sec-h">Cameras found on ${esc(recs.find((r) => r.id === rid)?.name || recs.find((r) => r.id === rid)?.device_id || "the recorder")}</div>${list.length ? `${list.some((x) => !x.enabled_as_camera) ? `<div class="row"><button class="btn sm" type="button" data-chall="${rid}|1">Select all</button><button class="btn sm" type="button" data-chall="${rid}|0">Deselect all</button><span class="sub">${list.filter((x) => !x.enabled_as_camera && x.on).length} of ${list.filter((x) => !x.enabled_as_camera).length} selected</span></div>` : ""}<div class="cams">${list.map((ch, i) => `<div class="cam"><div class="pic" data-pic="${rid}|${ch.channel_no}">${ico("cam", 24)}<span class="picmsg">Loading picture…</span></div><div class="cb">${ch.enabled_as_camera ? `<span class="tag ok">Already added</span>${(() => { const k = cams.find((x) => x.recorder_id === rid && x.channel_no === ch.channel_no); return k ? `<div class="pt8"><b class="dark">${esc(k.name)}</b> <button class="btn sm" type="button" data-rencam="${k.id}" ${can("recs") ? "" : "disabled title='Not for your role'"}>${ico("edit", 14)}Rename</button></div>` : ""; })()}` : `<label class="chk"><input type="checkbox" data-ch="${rid}.${i}" ${ch.on ? "checked" : ""}>Watch this camera</label><input class="in" aria-label="Camera name" data-chn="${rid}.${i}" value="${esc(ch.name)}">`}</div></div>`).join("")}</div>${list.some((x) => !x.enabled_as_camera) ? `<div><button class="btn pri" type="button" data-addcams="${rid}">Add the ticked cameras</button></div>` : ""}` : `<div class="note">The recorder has not sent its cameras yet. Check its details, then try again in a minute.</div>`}`).join("")}</div>`;
}
/* ------------------------------------------------------------------------------------------------ People count and Vehicles (read only) */
const nf = (x) => (x ?? 0).toLocaleString("en-IN");
const PV_RANGES = [["today", "Today"], ["yesterday", "Yesterday"], ["7", "Last 7 days"], ["30", "Last 30 days"]];
function pvDates(r) {
  const t = todayIST(), back = (k) => { const d = new Date(t); d.setDate(d.getDate() - k); return isoDay(d); };
  return r === "today" ? [isoDay(t), isoDay(t)] : r === "yesterday" ? [back(1), back(1)] : r === "30" ? [back(29), isoDay(t)] : [back(6), isoDay(t)];
}
const pvSel = (id, label, v, opts) => `<label class="sr" for="${id}">${label}</label><select class="in sel" id="${id}">${opts.map((o) => `<option value="${o[0]}" ${v === o[0] ? "selected" : ""}>${o[1]}</option>`).join("")}</select>`;
const hr12 = (h) => `${h % 12 || 12}${h < 12 ? " am" : " pm"}`;
async function loadCount() {
  const [from, to] = pvDates(S.cnt.range), p = new URLSearchParams({ from, to }); if (S.cnt.cam !== "all") p.set("camera_id", S.cnt.cam);
  S.cnt.data = await api.get(`/admin/v1/tenants/${S.cid}/shop/people-count?${p}`);
}
function secCount() {
  const D = S.cnt.data, v = S.cur.views.people, daily = !D.hourly, ser = D.series;
  let lo = 0, hi = ser.length - 1;
  if (!daily) { const used = ser.map((x, i) => x.in + x.out > 0 ? i : -1).filter((i) => i >= 0); lo = Math.min(9, ...(used.length ? used : [9])); hi = Math.max(20, ...(used.length ? used : [20])); }
  const shown = ser.slice(lo, hi + 1), mx = Math.max(1, ...shown.map((x) => x.in + x.out)), peak = D.busiest;
  const dayName = (x) => { const d = new Date(x.label + "T00:00:00"); return S.cnt.range === "7" ? d.toLocaleDateString("en-GB", { weekday: "short" }) : String(d.getDate()); };
  const lab = (x, i) => daily ? (S.cnt.range === "30" && i % 2 ? "" : dayName(x)) : (i === 0 || x.label === 12 ? hr12(x.label) : String(x.label % 12 || 12));
  const cams = S.cur.cams.filter((c) => c.use_footfall || D.cameras.some((q) => q.id === c.id));
  return `<div class="stack">${v.on ? "" : `<div class="note">No event counts people any more, so nothing new is being counted. Showing what was saved earlier.</div>`}
  <div class="bar flush">${pvSel("cnt-range", "Range", S.cnt.range, PV_RANGES)}${pvSel("cnt-cam", "Door", S.cnt.cam, [["all", "All doors"], ...cams.map((c) => [c.id, c.name])])}<span class="sp"></span><button class="btn" type="button" data-export="count">${ico("dl", 16)}Export to Excel</button></div>
  <div class="kpis"><div class="kpi"><div class="k">People in</div><div class="v">${nf(D.people_in)}</div></div><div class="kpi"><div class="k">People out</div><div class="v">${nf(D.people_out)}</div></div>
  <div class="kpi"><div class="k">${daily ? "Busiest day" : "Busiest hour"}</div><div class="v">${peak ? (daily ? dayName(peak) + (S.cnt.range === "30" ? " " + new Date(peak.label + "T00:00:00").toLocaleDateString("en-GB", { month: "short" }) : "") : hr12(peak.label)) : "—"}</div><div class="nt">${peak ? `${nf(peak.in)} in, ${nf(peak.out)} out` : "Nobody counted"}</div></div>
  <div class="kpi"><div class="k">About inside now</div><div class="v">${D.inside_now === null ? "—" : "~" + nf(D.inside_now)}</div><div class="nt">${D.inside_now === null ? "Shown when today is in the range" : "An estimate. A missed person at the door makes it drift."}</div></div></div>
  <div class="h2">${daily ? "People per day" : "People per hour"}<span class="legend"><span><i class="lg-in"></i>In</span><span><i class="lg-out"></i>Out</span></span></div>
  ${D.people_in + D.people_out ? `<div class="chart"><div class="plot">${shown.map((x) => `<div class="col ${peak && x.label === peak.label ? "peak" : ""}" title="${daily ? dayName(x) : hr12(x.label)}: ${x.in} in, ${x.out} out"><span class="i" data-hp="${x.in / mx * 100}"></span><span class="o" data-hp="${x.out / mx * 100}"></span></div>`).join("")}</div><div class="axis">${shown.map((x, i) => `<span>${lab(x, i)}</span>`).join("")}</div></div>` : `<div class="empty">Nobody was counted in this range.</div>`}
  <div class="h2">By camera</div><div class="scroll"><table class="tbl"><thead><tr><th>Camera</th><th class="num">In</th><th class="num">Out</th><th>Last counted</th></tr></thead><tbody>${D.cameras.length ? D.cameras.map((c) => `<tr><td class="main-c">${esc(c.name)}</td><td class="num">${nf(c.in)}</td><td class="num">${nf(c.out)}</td><td>${c.last_at ? fmtTime(c.last_at) : "—"}</td></tr>`).join("") : `<tr><td colspan="4" class="empty">No camera counts people.</td></tr>`}</tbody></table></div>
  <div class="sub small">Days are India time. Counts are kept for the days set under Setup, Data kept.</div></div>`;
}
async function loadVehicles(more, reuse) {
  if (reuse) return;                                                          // "Load more" asked for the next page: keep what is on screen
  const V = S.veh, [from, to] = pvDates(V.range), p = new URLSearchParams({ from, to, limit: "50" });
  if (V.q.trim()) p.set("q", V.q.trim()); if (V.dir !== "all") p.set("direction", V.dir); if (V.st !== "all") p.set("status", V.st); if (more) p.set("offset", String(V.items.length));
  const r = await api.get(`/admin/v1/tenants/${S.cid}/shop/vehicles?${p}`);
  V.items = more ? V.items.concat(r.items) : r.items; V.total = r.total; V.tz = r.tz;
}
const stayText = (m) => m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
const vWhen = (iso) => new Date(iso).toLocaleString("en-IN", { timeZone: TZ, day: "numeric", month: "short", hour: "numeric", minute: "2-digit", hour12: true });
const vThumb = (x, big) => x.picture ? `<img class="${big ? "vbig" : "vthumb"} vimg" src="${esc(x.picture)}" alt="Vehicle ${esc(x.plate)}" loading="lazy">` : `<div class="${big ? "vbig" : "vthumb"} vnone">${ico("car", big ? 40 : 22)}</div>`;
function secVehicles() {
  const V = S.veh, v = S.cur.views.vehicles, rows = V.items;
  return `<div class="stack">${v.on ? "" : `<div class="note">No event reads number plates any more, so nothing new is being read. Showing what was saved earlier.</div>`}
  <div class="bar flush"><label class="search">${ico("search")}<span class="sr">Search a plate</span><input id="vq" placeholder="Search a plate" value="${esc(V.q)}" autocomplete="off"></label><span class="sp"></span>
  ${pvSel("veh-range", "Range", V.range, PV_RANGES)}${pvSel("veh-dir", "Direction", V.dir, [["all", "In and out"], ["in", "Coming in"], ["out", "Going out"]])}${pvSel("veh-st", "Which vehicles", V.st, [["all", "All vehicles"], ["blocked", "Blocked list"], ["review", "Needs review"]])}
  <button class="btn" type="button" data-export="vehicles">${ico("dl", 16)}Export to Excel</button></div>
  ${rows.length ? `<div class="scroll"><table class="tbl"><thead><tr><th>Picture</th><th>Time</th><th>Plate</th><th>Where</th><th>Direction</th><th>Stay</th><th>Read</th></tr></thead><tbody>
  ${rows.map((x, i) => `<tr class="vrow" data-veh="${i}" tabindex="0"><td>${vThumb(x)}</td><td>${vWhen(x.seen_at)}</td><td><span class="plate">${esc(x.plate || "—")}</span>${x.blocked ? ' <span class="tag crit">Blocked</span>' : ""}</td><td>${esc(x.camera)}</td><td>${x.direction ? `<span class="tag ${x.direction === "in" ? "ok" : ""}">${x.direction === "in" ? "In" : "Out"}</span>` : '<span class="sub">—</span>'}</td><td>${x.inside ? '<span class="tag brand" title="No exit has been read yet. It may have left unseen.">Inside now?</span>' : x.stay_minutes !== null ? stayText(x.stay_minutes) : "—"}</td><td>${x.needs_review ? '<span class="tag warn">Needs review</span>' : x.confidence !== null ? x.confidence + "%" : "—"}</td></tr>`).join("")}</tbody></table></div>
  <div class="more"><span></span>${rows.length < V.total ? `<button class="btn" type="button" data-vmore>Load more</button>` : "<span></span>"}<span class="sub end">Showing ${rows.length} of ${V.total}</span></div>` : `<div class="empty">No vehicles ${V.q || V.dir !== "all" || V.st !== "all" ? "match" : "were read in this range"}.</div>`}
  <div class="sub small">Click a row to see the picture. Pictures and reads are kept for the days set under Setup, Data kept. An exit is matched to the latest entry of the same plate at any of the shop's plate cameras.</div></div>`;
}
function openVehicle(i) {
  const x = S.veh.items[i]; if (!x) return;
  modal(`${mh(esc(x.plate || "Unclear plate"))}${vThumb(x, true)}<div class="creds"><div class="kv"><span>Time</span><span>${vWhen(x.seen_at)}</span></div><div class="kv"><span>Where</span><span>${esc(x.camera)}</span></div><div class="kv"><span>Direction</span><span>${x.direction === "in" ? "Coming in" : x.direction === "out" ? "Going out" : "Not known"}</span></div><div class="kv"><span>Stay</span><span>${x.inside ? "Inside now? No exit read yet." : x.stay_minutes !== null ? stayText(x.stay_minutes) : "—"}</span></div><div class="kv"><span>How sure</span><span>${x.needs_review ? "Not sure, needs review" : x.confidence !== null ? x.confidence + "%" : "—"}</span></div><div class="kv"><span>Blocked list</span><span>${x.blocked ? "Yes, this plate is on the list" : "No"}</span></div></div><p class="sub nm">Green box: the vehicle. Red box: the plate. View only.</p>`);
}
async function downloadFile(url, name) {
  const blob = await api.call("GET", url, undefined, { blob: true });
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}
const RANGES = [["24h", "Last 24 hours"], ["today", "Today"], ["yesterday", "Yesterday"], ["7", "Last 7 days"], ["30", "Last 30 days"], ["custom", "Custom"]];
const isoDay = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
function rangeDates() {
  const t = todayIST(), back = (n) => { const d = new Date(t); d.setDate(d.getDate() - n); return isoDay(d); };
  if (S.logRange === "today") return [isoDay(t), isoDay(t)];
  if (S.logRange === "yesterday") return [back(1), back(1)];
  if (S.logRange === "30") return [back(29), isoDay(t)];
  if (S.logRange === "custom") return [S.logFrom || "", S.logTo || ""];
  return [back(6), isoDay(t)];
}
/* Reads one page of the message log for the range, status and search chosen. "Load more" adds the next page. */
async function loadLog(tenantId, more, reuse) {
  const [from, to] = rangeDates(), p = new URLSearchParams({ limit: "100" });
  if (S.logRange === "24h") p.set("hours", "24"); else { if (from) p.set("from", from); if (to) p.set("to", to); } if (tenantId) p.set("tenant_id", tenantId);
  if (S.logChip !== "all") p.set("group", S.logChip); if (S.logQ.trim()) p.set("q", S.logQ.trim());
  const key = p.toString(), same = S.logData && S.logData.key === key;
  if (reuse && same) return;
  if (more && same) p.set("offset", String(S.logData.items.length));
  const r = await api.get("/admin/v1/messaging/log?" + p.toString());
  S.logData = { key, total: r.total, items: more && same ? S.logData.items.concat(r.items) : r.items };
}
const kindOf = (m) => m.status === "failed" ? "Failed" : m.status === "queued" ? "Waiting" : ["suppressed", "cancelled"].includes(m.status) ? "Held back" : "Sent";
function phoneLog(rows, withCustomer, D) {
  const cls = (m) => ({ Failed: "crit", Waiting: "warn", "Held back": "", Sent: "ok" }[kindOf(m)]);
  return `<div class="pcards">${rows.map((m) => `<div class="msg"><div class="l1"><b>${esc(m.title || humanize((m.event || "message").split(".").pop()))}</b><span class="tag ${cls(m)}">${kindOf(m)}</span></div><span class="sub small">${fmtTime(m.created_at)}${withCustomer ? " · " + esc(m.tenant) : ""}${m.recipient ? " · " + esc(m.recipient) : ""}</span>${m.error ? `<span class="small t-crit">${esc(m.error)}</span>` : ""}${m.status === "failed" && can("templates") ? `<button class="btn sm" type="button" data-resend="${m.id}">Send again</button>` : ""}</div>`).join("")}</div>
  ${rows.length < D.total ? `<button class="btn" type="button" data-lmore>Load more</button>` : ""}<div class="sub small mid">Showing ${rows.length} of ${D.total}</div>`;
}
function logTable(withCustomer) {
  const chips = [["all", "All"], ["Sent", "Sent"], ["Failed", "Failed"], ["Waiting", "Waiting"], ["Held back", "Held back"]], D = S.logData, rows = D.items;
  const cls = (m) => ({ Failed: "crit", Waiting: "warn", "Held back": "", Sent: "ok" }[kindOf(m)]), txt = (m) => ({ Failed: "t-crit", Waiting: "t-warn", "Held back": "t-mute", Sent: "t-ok" }[kindOf(m)]);
  return `<div class="logbar"><label class="search">${ico("search")}<span class="sr">Search messages</span><input id="lq" placeholder="Search" value="${esc(S.logQ)}" autocomplete="off"></label><span class="sp"></span>
    ${S.logRange === "custom" ? `<label class="sub">From <input class="in time" type="date" id="lfrom" value="${esc(S.logFrom)}"></label><label class="sub">To <input class="in time" type="date" id="lto" value="${esc(S.logTo)}"></label>` : ""}
    <label class="sr" for="lrange">Date range</label><select class="in sel" id="lrange" data-lrangesel>${RANGES.map((c) => `<option value="${c[0]}" ${S.logRange === c[0] ? "selected" : ""}>${c[0] === "custom" ? "Custom range" : c[1]}</option>`).join("")}</select>
    <label class="sr" for="lstatus">Status</label><select class="in sel" id="lstatus" data-lchipsel>${chips.map((c) => `<option value="${c[0]}" ${S.logChip === c[0] ? "selected" : ""}>${c[0] === "all" ? "All messages" : c[1]}</option>`).join("")}</select></div>
  ${rows.length && PH.matches ? phoneLog(rows, withCustomer, D) : rows.length ? `<div class="scroll"><table class="tbl"><thead><tr><th class="dotc"></th><th>Time</th>${withCustomer ? "<th>Customer</th>" : ""}<th>Message</th><th>To</th><th>Result</th><th>Why</th><th></th></tr></thead><tbody>${rows.map((m) => `<tr><td class="dotc"><span class="dot ${cls(m)}"></span></td><td class="num">${fmtTime(m.created_at)}</td>${withCustomer ? `<td class="main-c">${esc(m.tenant)}</td>` : ""}<td>${esc(m.title || humanize((m.event || "message").split(".").pop()))}</td><td>${esc(m.recipient || "")}</td><td class="${txt(m)}">${kindOf(m)}</td><td class="sub">${esc(m.error || "")}</td><td>${m.status === "failed" && can("templates") ? `<button class="btn sm" type="button" data-resend="${m.id}">Send again</button>` : ""}</td></tr>`).join("")}</tbody></table></div><div class="more"><span></span>${rows.length < D.total ? `<button class="btn" type="button" data-lmore>Load more</button>` : "<span></span>"}<span class="sub end">Showing ${rows.length} of ${D.total}</span></div>` : `<div class="empty">No messages in this range${S.logQ || S.logChip !== "all" ? " match" : ""}.</div>`}`;
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
      const ids = f.people.filter((p) => p.id && p.s).map((p) => p.id);
      await api.put(base + "/events/daily_summary", { enabled: f.daily, params: { hour: +f.dailyAt.slice(0, 2) }, recipient_ids: ids });
      await api.put(base + "/events/weekly_summary", { enabled: f.weekly, params: {}, recipient_ids: ids });
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
const newEv = () => ({ name: "", nameAuto: true, to: S.cur.people.map((p) => p.id), rec: S.cur.recs.length === 1 ? { id: S.cur.recs[0].id } : null, target: null, key: null, pts: [], v: {}, notice: false, clip: false, on: true, snap: null, done: {} });
const GROUPS = ["On this camera", "People", "Vehicles", "Camera health", "Devices", "Recording"];
const typeOn = (x) => { const e = S.cur.events.find((q) => q.type === x.type); return !!(e && e.enabled && (e.saved || e.default) && !AREA_KIND[x.type]); };
function pageEvent() {
  const e = S.ev, o = eorder(); let body = "";
  if (S.estep === "event") body = `<div class="f"><label for="e-name">Event name<small>Shown in your list. It fills in when you choose the type.</small></label><input class="in" id="e-name" data-ebind="name" value="${esc(e.name)}" placeholder="For example, Queue at the counter" autocomplete="off">
    <span class="lb">Send to<small>Only these people get this event</small></span><div class="stack">${S.cur.people.length ? S.cur.people.map((p) => `<label class="chk dark"><input type="checkbox" data-eto="${p.id}" ${e.to.includes(p.id) ? "checked" : ""}>${esc(p.name)} <span class="sub">${esc(p.phone || "")}</span></label>`).join("") : `<div class="note warn">This shop has nobody to message yet. Add people under Who gets messages first.</div>`}</div></div>`;
  else if (S.estep === "type") { const all = DATA_TYPES.concat(S.cur.events.filter((x) => !HIDDEN.has(x.type)));
    body = `<div class="pick scroll"><table class="tbl"><tbody>${GROUPS.map((g) => { const rows = all.filter((x) => x.group === g); return rows.length ? `<tr><td colspan="4" class="sec-h grp">${g}</td></tr>` + rows.map((x) => `<tr data-etype="${x.type}"><td class="radio"><input type="radio" name="ty" aria-label="${esc(x.title)}" ${e.key === x.type ? "checked" : ""}></td><td class="main-c">${esc(x.title)}${x.ai ? ' <span class="tag ai">Smart</span>' : ""}${typeOn(x) ? ' <span class="tag ok">On for this shop</span>' : ""}<br><span class="sub small wrap">${esc(x.description)}</span></td><td><span class="tag ${MEDIA[carries(x.type)][1]}">${MEDIA[carries(x.type)][0]}</span></td><td>${{ area: '<span class="tag">Marks an area</span> ', line: '<span class="tag">Marks a counting line</span> ', zone: '<span class="tag">Marks the plate area</span> ' }[markMode(x.type)] || ""}${needsCam(x.type) ? '<span class="tag">Needs a camera</span>' : NEEDS_PLATES.has(x.type) ? '<span class="tag">Uses Read number plates</span>' : ""}</td></tr>`).join("") : ""; }).join("")}</tbody></table></div>`; }
  else if (S.estep === "recorder") body = `<div class="pick scroll"><table class="tbl"><thead><tr><th class="dotc"></th><th></th><th>Recorder</th><th>Brand</th><th>Status</th></tr></thead><tbody>${S.cur.recs.map((r) => `<tr data-erec="${r.id}"><td class="dotc"><span class="dot ${r.status === "online" ? "ok" : r.status === "offline" ? "crit" : ""}"></span></td><td class="radio"><input type="radio" name="rc" aria-label="${esc(r.name || r.device_id)}" ${e.rec?.id === r.id ? "checked" : ""}></td><td class="main-c">${esc(r.name || r.device_id)}</td><td>${esc(humanize(r.brand || ""))}</td><td class="${r.status === "online" ? "t-ok" : "t-mute"}">${r.status === "never" ? "Not connected yet" : humanize(r.status)}</td></tr>`).join("")}</tbody></table></div>`;
  else if (S.estep === "camera") { const list = S.cur.cams.filter((c) => !e.rec || S.cur.recs.length <= 1 || c.recorder_id === e.rec.id);
    body = list.length ? `<div class="cams big">${list.map((c) => { const rec = S.cur.recs.find((r) => r.id === c.recorder_id), down = rec && rec.status === "offline"; return `<div class="cam ${e.target?.id === c.id ? "sel" : ""} ${down ? "offl" : ""}" data-ecam="${c.id}" role="radio" aria-checked="${e.target?.id === c.id}" tabindex="0"><div class="pic" ${down ? "" : `data-campic-box="${c.id}"`}>${ico("cam", 24)}<span class="picmsg">${down ? esc((rec.name || rec.device_id) + " is offline") : "Loading picture…"}</span></div><div class="cb"><b class="dark">${esc(c.name)}</b><div>${usedFor(c)}</div>${down ? '<span class="tag crit">Offline</span>' : ""}${e.target?.id === c.id ? '<span class="tag brand">Selected</span>' : ""}</div></div>`; }).join("")}</div>` : `<div class="note warn">${e.rec && S.cur.recs.length > 1 ? "This recorder has no cameras added yet." : "This shop has no cameras added yet."} Add them under Recorder and cameras first.</div>`; }
  else if (S.estep === "area") { const m = markMode(e.key), help = { area: "Click on the picture to add the corners of the area. At least 3 corners.", line: "Click two points to draw a line across the door or the path. People crossing it the way the arrow points are counted IN, the others OUT.", zone: "Click the corners of the area where vehicles and their plates should be read, or leave it to use the whole picture. Then choose which way a vehicle moves when it comes in." }[m];
    const had = m === "line" ? e.zones?.some((z) => z.kind === "count_line") : m === "zone" ? e.zones?.some((z) => z.kind === "anpr_zone") : false;
    if (e.loadingPic) body = `<div class="note">Getting the picture from ${esc(e.target?.name || "the camera")}… this can take up to 30 seconds.</div>`;
    else if (e.snapErr) body = `<div class="note warn">${blockedText(e.target?.name || "This camera", e.snapErr, m === "area" ? "The area cannot be marked." : m === "line" ? "The counting line cannot be marked." : "The plate area cannot be marked on the picture.")}</div><div class="row"><button class="btn pri" type="button" data-snaptry>Try again</button></div>`;
    else body = `${had && !e.touched ? `<div class="note">${m === "line" ? "A counting line is already marked on this camera." : "A plate area is already marked on this camera."} Click on the picture to draw a new one, or press Next to keep it.</div>` : ""}${videoFrame((e.target?.name || "") + " · " + cname(), e.pts, true, e.snap, m, e.flip, e.pdir, e.target?.id)}<div class="vtool"><span class="sub">${help}${e.snap ? "" : " The camera's picture is not available yet, so this is a sample view."}</span><span class="grow"></span>${m === "line" ? '<button class="btn sm" type="button" data-flip>Swap IN and OUT</button>' : ""}${m === "zone" ? '<button class="btn sm" type="button" data-whole>Use the whole picture</button>' : ""}<button class="btn sm" type="button" data-clearpts>Start again</button></div>${m === "zone" ? `<div class="stack pdir"><span class="lb">Which way is IN?<small>A vehicle moving this way in the picture counts as coming in, the other way as going out. Needed to match entries with exits.</small></span>${dirPicker(e.pdir, "data-pdir")}${e.pdir ? "" : `<div class="note warn">Choose a direction to continue.</div>`}</div>` : ""}`; }
  else body = settingsBody(e);
  const pos = o.indexOf(S.estep), last = pos === o.length - 1;
  const m = markMode(e.key), had = m === "line" ? e.zones?.some((z) => z.kind === "count_line") : m === "zone" ? e.zones?.some((z) => z.kind === "anpr_zone") : false;
  const markOk = e.loadingPic ? false : e.snapErr ? false : m === "line" ? (e.pts.length === 2 || (had && !e.touched)) : m === "zone" ? (!!e.pdir && (e.pts.length === 0 || e.pts.length >= 3)) : e.pts.length >= 3;
  const ok = { event: true, type: !!e.key, recorder: !!e.rec, camera: !!e.target, area: markOk, settings: true }[S.estep];
  const title = S.estep === "area" ? MARK_TITLE[m] : ESTEPS.find((s) => s[0] === S.estep)[1];
  return `<div class="rise"><div class="title"><h1>${title}</h1><span class="tag brand">Step ${pos + 1} of ${o.length}</span><span class="sub">${esc(cname())}</span></div><div class="form">${body}
   <div class="actions">${last ? `<button class="btn pri" type="button" data-evcreate ${NEEDS_PLATES.has(e.key) && !plateCams().length ? "disabled" : ""}>Create event</button>` : `<button class="btn pri" type="button" data-evnext ${ok ? "" : "disabled"}>Next</button>`}${pos ? `<button class="btn" type="button" data-evprev>Back</button>` : ""}<button class="btn bare" type="button" data-go="customer">Cancel</button></div></div></div>`;
}
const usedFor = (c) => [c.use_footfall ? "Counts people" : "", c.use_anpr ? "Reads plates" : "", c.use_areas ? "Marked areas" : ""].filter(Boolean).map((x) => `<span class="tag">${x}</span>`).join(" ") || '<span class="sub">Nothing yet</span>';
const CHOICE_LABEL = { person: "People", vehicle: "Vehicles", closed: "When the shop is closed", between: "Between these times", always: "At any time" };
/* the fields of one marked area: numbers, and for an activity area the choices */
function areaFields(kind, v) {
  const rows = (AREA_FIELDS[kind] || []).map(([k, label, unit, def]) => `<div class="cfg"><span>${label}</span><input class="in sm" type="number" min="1" aria-label="${label}" data-ev="${k}" value="${v[k] ?? def}"><span class="sub">${unit}</span></div>`);
  if (kind === "activity_area") {
    const when = v.when || "closed";
    rows.unshift(`<div class="cfg"><span>What to look for</span><select class="in" aria-label="What to look for" data-ev="target">${["person", "vehicle"].map((x) => `<option value="${x}" ${(v.target || "person") === x ? "selected" : ""}>${CHOICE_LABEL[x]}</option>`).join("")}</select><span></span></div>`,
      `<div class="cfg"><span>When</span><select class="in" aria-label="When" data-ev="when">${["closed", "between", "always"].map((x) => `<option value="${x}" ${when === x ? "selected" : ""}>${CHOICE_LABEL[x]}</option>`).join("")}</select><span></span></div>`);
    if (when === "between") rows.splice(2, 0, `<div class="cfg"><span>From</span><input class="in time" type="time" aria-label="From" data-ev="from" value="${v.from || "22:00"}"><span></span></div><div class="cfg"><span>To</span><input class="in time" type="time" aria-label="To" data-ev="to" value="${v.to || "06:00"}"><span></span></div>`);
    if (when === "closed" && S.cur.f.hmode === "always") rows.push(`<div class="note warn">This shop has no business hours yet, so "When the shop is closed" never applies. Set Business hours first, or choose "Between these times".</div>`);
  }
  return rows;
}
const areaParams = (kind, v) => { const p = {}; for (const [k, , , def] of AREA_FIELDS[kind] || []) p[k] = +(v[k] ?? def); if (kind === "activity_area") { p.target = v.target || "person"; p.when = v.when || "closed"; if (p.when === "between") { p.from = v.from || "22:00"; p.to = v.to || "06:00"; } } if (kind === "staff_area") p.notice_confirmed = true; return p; };
function settingsBody(e) {
  const x = evMeta(e.key), kind = AREA_KIND[e.key], rows = [];
  if (kind) rows.push(...areaFields(kind, e.v));
  else for (const [k, s] of Object.entries(x.settings || {})) { if (s.type === "boolean") { rows.push(`<div class="cfg"><span>Include a short video</span><span>${sw("e-clip", e.clip, false, "eclip")}</span><span></span></div>`); continue; } const [label, unit] = PARAM[k] || [humanize(k), ""]; rows.push(`<div class="cfg"><span>${label}</span><input class="in sm" type="number" min="${s.min}" max="${s.max}" aria-label="${label}" data-ev="${k}" value="${e.v[k] ?? s.default}"><span class="sub">${unit}</span></div>`); }
  if (e.key === "phone_use") rows.push(`<label class="chk dark notice"><input type="checkbox" data-enotice ${e.notice ? "checked" : ""}>I have told the staff that this area is watched for phone use.</label>`);
  const m = MEDIA[carries(e.key)];
  const needs = NEEDS_COUNT.has(e.key) ? "People counting will be switched on for the camera you chose, because this event needs it." : NEEDS_PLATES.has(e.key) ? "" : !needsCam(e.key) ? (REC_LEVEL.has(e.key) ? "This covers every recorder in the shop." : "This covers every camera in the shop.") : "";
  if (needs) rows.push(`<div class="note">${needs}</div>`);
  if (NEEDS_PLATES.has(e.key)) rows.push(platesNote(e.key));
  return `<div class="stack"><div class="f f-narrow"><span class="lb">${esc(x.title)}<small>${esc(x.description)}</small></span><div class="stack">${rows.join("") || `<div class="sub">No setting needed for this event.</div>`}
    <div class="cfg"><span>Message carries</span><span class="tag ${m[1]}">${m[0]}</span><span></span></div><div class="cfg"><span>Switch on now</span><span>${sw("e-on", e.on !== false, false, "eon")}</span><span></span></div></div></div></div>`;
}
/* The plate alerts have no camera of their own: they use the cameras set up under Read number plates. Say what that means for this shop. */
function platesNote(type) {
  const cams = plateCams(), bad = noDirCams();
  if (!cams.length) return `<div class="note warn">No camera reads number plates yet, so this event has nothing to watch. Set up Read number plates first.<div class="row"><button class="btn pri sm" type="button" data-goplates>Set up Read number plates</button></div></div>`;
  const names = cams.map((c) => esc(c.name)).join(", ");
  if (type === "vehicle_overstay" && bad.length) return `<div class="note warn">This works on the cameras that read plates (${names}). ${bad.map((c) => esc(c.name)).join(", ")} ${bad.length === 1 ? "has" : "have"} no IN direction, so vehicles there cannot be matched coming in and going out. Set the direction under Read number plates.<div class="row"><button class="btn sm" type="button" data-goplates>Set the direction</button></div></div>`;
  return `<div class="note">This works on the cameras that read plates: ${names}. Add or change cameras under Read number plates.</div>`;
}
async function wizardPicture() {
  const E = S.ev; E.loadingPic = true; render();                           // show the wait at once, then the picture (or why there is none)
  await loadSnap(E);
  E.loadingPic = false;
  if (S.route === "event" && S.ev === E && S.estep === "area") render(true);
}
async function loadZones(e) {
  if (!e.target || e.zones) return;
  try {
    e.zones = await api.get(`/admin/v1/tenants/${S.cid}/shop/cameras/${e.target.id}/zones`);
    const m = markMode(e.key), z = e.zones.find((q) => q.kind === (m === "line" ? "count_line" : "anpr_zone"));
    if (m === "zone" && z && e.pdir === undefined) e.pdir = dirName(z.in_direction);
    if (z && !e.pts.length && m !== "area") { e.pts = z.points.map((p) => [Math.round(p[0] * 640), Math.round(p[1] * 360)]); if (m === "line" && z.in_direction) { const [a, b] = z.points, dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1; e.flip = (-dy / len) * z.in_direction[0] + (dx / len) * z.in_direction[1] < 0; } }
  } catch { e.zones = []; }
}
async function loadSnap(e) {
  await loadZones(e);
  if (e.snap || !e.target) return;
  e.snapErr = null;
  const cam = S.cur.cams.find((c) => c.id === e.target.id), rec = cam && S.cur.recs.find((r) => r.id === cam.recorder_id);
  if (rec && rec.status === "offline") { e.snapErr = { kind: "offline", recorder: rec.name || rec.device_id }; return; }          // already known: no need to wait for a picture that will not come
  let timer;
  try {
    const wait = new Promise((_, no) => { timer = setTimeout(() => no({ status: 504 }), 40000); });         // after 40 seconds it is too slow: say so and let the person try again
    const blob = await Promise.race([api.call("GET", `/admin/v1/tenants/${S.cid}/shop/cameras/${e.target.id}/snapshot`, undefined, { blob: true }), wait]);
    e.snap = URL.createObjectURL(blob);
  } catch (err) { e.snapErr = { kind: err.status === 409 ? "offline" : err.status === 504 ? "slow" : "down" }; }
  finally { clearTimeout(timer); }
}
/* Marking needs the camera's real picture. When it cannot be taken, say why and do not let anyone draw on a made-up view. */
function blockedText(name, err, what) {
  const why = err.recorder ? `${esc(err.recorder)} is offline, so ${esc(name)} cannot send a picture.` : err.kind === "offline" ? `${esc(name)} is not sending video right now. It may be offline.` : err.kind === "slow" ? `${esc(name)} did not send a picture in time. It may be offline or slow.` : `No picture could be taken from ${esc(name)} right now. It may be offline.`;
  return `${why} ${what} Try again when it is back online.`;
}
function blockedModal(name, err, what, retry) {
  S.retry = retry;
  modal(`${mh("Cannot mark it right now")}<div class="note warn">${blockedText(name, err, what)}</div><div class="row"><button class="btn pri" type="button" data-retry2>Try again</button><button class="btn" type="button" data-close>Close</button></div>`);
}
const norm1 = (p) => [Math.round(p[0] / 640 * 1000) / 1000, Math.round(p[1] / 360 * 1000) / 1000];
/* Save the counting line or the plate area of a camera. What is not being changed is sent again, because the server replaces both together. */
async function saveGeometry(camId, mode, pts, flip, touched, pdir) {
  if (!mode || mode === "area") return;
  const base = `/admin/v1/tenants/${S.cid}/shop/cameras/${camId}/zones`, cur = await api.get(base);
  const keep = (kind) => cur.filter((z) => z.kind === kind).map((z) => ({ kind: z.kind, points: z.points, in_direction: z.in_direction || null }));
  let zones;
  if (mode === "line") {
    if (!touched && keep("count_line").length) return;
    if (pts.length !== 2) throw new Error("Click two points to draw the counting line.");
    const [a, b] = pts.map(norm1), dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1; let n = [-dy / len, dx / len]; if (flip) n = [-n[0], -n[1]];
    zones = [{ kind: "count_line", points: [a, b], in_direction: n }, ...keep("anpr_zone")];
  } else {
    if (!touched) return;
    if (pts.length && pts.length < 3) throw new Error("A plate area needs at least 3 corners. Or press Use the whole picture.");
    if (!pdir) throw new Error("Choose which way a vehicle moves when it comes in.");
    zones = [...keep("count_line"), { kind: "anpr_zone", points: pts.length >= 3 ? pts.map(norm1) : [[0, 0], [1, 0], [1, 1], [0, 1]], in_direction: DIRS[pdir] }];        // no corners drawn means the whole picture
  }
  await api.put(base, { zones });
}
async function openMark(i) {
  const r = S.cur.rows[i], mode = r.flag === "use_footfall" ? "line" : "zone";
  S.mark = { i, mode, pts: [], flip: false, pdir: undefined, touched: false, snap: null, key: mode === "line" ? "__count" : "__plates", target: { kind: "cam", id: r.cam.id, name: r.cam.name }, zones: null };
  modal(`${mh(MARK_TITLE[mode])}<div class="note" data-loading>Getting the picture from ${esc(r.cam.name)}… this can take up to 30 seconds.</div><div class="row"><button class="btn" type="button" data-close>Cancel</button></div>`);
  await loadSnap(S.mark);
  if (!$("#layer [data-loading]")) return;                                  // closed while waiting
  if (S.mark.snapErr) return blockedModal(r.cam.name, S.mark.snapErr, mode === "line" ? "The counting line cannot be marked." : "The plate area cannot be marked.", () => openMark(i));
  drawMark();
}
function drawMark() {
  const M = S.mark, r = S.cur.rows[M.i];
  modal(`${mh(MARK_TITLE[M.mode])}${videoFrame(r.cam.name + " · " + cname(), M.pts, true, M.snap, M.mode, M.flip, M.pdir, r.cam.id)}<div class="vtool"><span class="sub">${M.mode === "line" ? "Click two points to draw a line across the door or the path. People crossing it the way the arrow points are counted IN." : "Click the corners of the area where plates should be read, or use the whole picture. Then choose which way is IN."}</span><span class="grow"></span>${M.mode === "line" ? '<button class="btn sm" type="button" data-mflip>Swap IN and OUT</button>' : '<button class="btn sm" type="button" data-mwhole>Use the whole picture</button>'}<button class="btn sm" type="button" data-mclear>Start again</button></div>${M.mode === "zone" ? `<div class="stack pdir"><span class="lb">Which way is IN?<small>A vehicle moving this way counts as coming in, the other way as going out.</small></span>${dirPicker(M.pdir, "data-mpdir")}</div>` : ""}<div class="row"><button class="btn pri" type="button" data-msave ${M.mode === "zone" && (!M.pdir || (M.pts.length > 0 && M.pts.length < 3)) ? "disabled" : ""}>Save</button><button class="btn" type="button" data-close>Cancel</button></div>`);
  const d = $("#draw"); if (d) d.addEventListener("click", (ev) => { const b = d.getBoundingClientRect(); if (M.mode === "line" && M.pts.length >= 2) M.pts = []; M.pts.push([Math.round((ev.clientX - b.left) / b.width * 640), Math.round((ev.clientY - b.top) / b.height * 360)]); M.touched = true; drawMark(); });
}
async function createEvent() {
  const e = S.ev, x = evMeta(e.key), base = `/admin/v1/tenants/${S.cid}/shop`, kind = AREA_KIND[e.key], cam = e.target;
  const btn = document.querySelector("[data-evcreate]"); if (btn) btn.disabled = true;
  try {
    if (!e.key.startsWith("__") && !e.to.length) throw new Error("Choose at least one person to send this to. Add people first if the shop has none.");
    if (needsCam(e.key) && !cam) throw new Error("Choose a camera first.");
    if (NEEDS_PLATES.has(e.key) && !plateCams().length) throw new Error("No camera reads number plates yet. Set up Read number plates first.");
    if (cam && markMode(e.key) && markMode(e.key) !== "area") await saveGeometry(cam.id, markMode(e.key), e.pts, e.flip, e.touched, e.pdir);
    if (e.key.startsWith("__")) {
      await api.patch(`${base}/cameras/${cam.id}`, e.key === "__count" ? { use_footfall: e.on !== false } : { use_anpr: e.on !== false, ...(e.on !== false ? { stream: "main" } : {}) });
      await loadCurrent(S.cid);
      return modal(`${mh("Setup complete!")}<p class="sub nm center">${esc(x.title)} is on for ${esc(cam.name)}.</p><button class="btn pri wide" type="button" data-evdone>Manage events</button>`);
    }
    if (kind) {
      if (e.key === "phone_use" && !e.notice) throw new Error("Please confirm that the staff have been told.");
      if (e.key === "area_activity" && (e.v.when || "closed") === "between" && (e.v.from || "22:00") === (e.v.to || "06:00")) throw new Error("The start and the end time cannot be the same.");
      const existing = await api.get(`${base}/cameras/${cam.id}/areas`);
      await api.put(`${base}/cameras/${cam.id}/areas`, { areas: existing.map((a) => ({ id: a.id, kind: a.kind, name: a.name, points: a.points, params: a.params })).concat([{ kind, name: e.name || x.title, points: e.pts.map((p) => [Math.round(p[0] / 640 * 1000) / 1000, Math.round(p[1] / 360 * 1000) / 1000]), params: areaParams(kind, e.v) }]) });
    }
    const params = {}; if (!kind) for (const [k, s] of Object.entries(x.settings || {})) params[k] = s.type === "boolean" ? !!e.clip : +(e.v[k] ?? s.default);
    await api.put(`${base}/events/${e.key}`, { label: e.name || x.title, enabled: e.on !== false, params, recipient_ids: e.to });
    if (cam && NEEDS_COUNT.has(e.key)) await api.patch(`${base}/cameras/${cam.id}`, { use_footfall: true });
  } catch (err) { if (btn) btn.disabled = false; return toast(errText(err), true); }
  await loadCurrent(S.cid);
  modal(`${mh("Setup complete!")}<p class="sub nm center">${esc(e.name || x.title)} is on${cam ? " for " + esc(cam.name) : " for this shop"}.</p>${kind ? videoFrame(cam.name + " · " + cname(), e.pts, false, e.snap) : ""}<button class="btn pri wide" type="button" data-evdone>Manage events</button>`);
}

/* ---- the Events list: what the shop really gets, with Edit and Delete ---- */
const AREA_TYPES = new Set(Object.keys(AREA_KIND));
const EV_GROUPS = [["cam", "Counting and plates"], ["area", "Marked areas"], ["alert", "Alerts added"], ["default", "Default alerts"]];
function eventRows() {
  const { events, cams, areas, zones } = S.cur, byType = Object.fromEntries(events.map((e) => [e.type, e])), rows = [];
  for (const c of cams) {
    const z = zones[c.id] || [], line = z.some((q) => q.kind === "count_line"), plate = z.some((q) => q.kind === "anpr_zone");
    if (c.use_footfall || line) rows.push({ k: "data", g: "cam", flag: "use_footfall", t: "Count people", cam: c, on: !!c.use_footfall, marked: line });
    if (c.use_anpr || plate) rows.push({ k: "data", g: "cam", flag: "use_anpr", t: "Read number plates", cam: c, on: !!c.use_anpr, marked: plate });
    for (const a of areas[c.id] || []) rows.push({ k: "area", g: "area", type: a.alert, cam: c, area: a, rule: byType[a.alert], on: !!byType[a.alert]?.enabled });
  }
  for (const e of events) if (!HIDDEN.has(e.type) && !AREA_TYPES.has(e.type) && (e.saved || e.default)) rows.push({ k: "rule", g: e.default ? "default" : "alert", type: e.type, rule: e, on: !!e.enabled });
  return rows;
}
/* A short warning for a row that cannot do its job yet (empty when it is fine) */
function warnText(r) {
  if (r.k === "data" && r.flag === "use_footfall" && !r.marked) return "No line marked";
  if (r.k === "data" && r.flag === "use_anpr" && r.on && !plateDirOf(r.cam)) return "No direction";
  if (r.k === "rule" && NEEDS_PLATES.has(r.type) && r.on) { if (!plateCams().length) return "No plate camera"; if (r.type === "vehicle_overstay" && noDirCams().length) return `No direction on ${noDirCams().length} camera${noDirCams().length === 1 ? "" : "s"}`; }
  return "";
}
const namesOf = (ids) => (ids || []).map((i) => S.cur.people.find((p) => p.id === i)?.name).filter(Boolean).join(", ") || "—";
const rowName = (r) => r.k === "data" ? `${r.t} · ${r.cam.name}` : r.k === "area" ? r.area.name : (r.rule.label || r.rule.title);
const rowType = (r) => r.k === "data" ? r.t : r.k === "area" ? (evMeta(r.type)?.title || r.type) + (r.type === "area_activity" ? ` · ${{ person: "people", vehicle: "vehicles" }[r.area.params?.target || "person"]}, ${{ closed: "when closed", between: `${r.area.params?.from}–${r.area.params?.to}`, always: "at any time" }[r.area.params?.when || "closed"]}` : "") : r.rule.title;
const rowWhere = (r) => r.k === "rule" ? (REC_LEVEL.has(r.type) ? "Every recorder" : NEEDS_COUNT.has(r.type) ? "Counting cameras" : NEEDS_PLATES.has(r.type) ? "Plate cameras" : "Every camera") : r.cam.name;
const rowSends = (r) => MEDIA[carries(r.k === "data" ? (r.flag === "use_footfall" ? "__count" : "__plates") : r.type)];
/* The Events list in two groups: Smart events (counting, plates, marked areas and the alerts built on them; you add and delete these) and System events (the default checks) */
const EV_GROUPS2 = [["smart", "Smart events", "you add and delete these"], ["system", "System events", "on by default for every shop, switch off to stop"]];
const groupOf = (r) => (r.k === "data" || r.k === "area" ? "smart" : r.rule?.ai ? "smart" : "system");
function groupsHtml(rows, list, line, searching) {
  S.evOpen = S.evOpen || { smart: true, system: true };
  return EV_GROUPS2.map(([key, label, note]) => {
    const all = rows.filter((r) => groupOf(r) === key), mine = list.filter((r) => groupOf(r) === key); if (!mine.length) return "";
    const open = searching || S.evOpen[key];
    return `<tr class="grp"><td colspan="7"><button class="gbtn" type="button" data-evgrp="${key}" aria-expanded="${open}"><span class="chev ${open ? "" : "shut"}">${ico("chev", 14)}</span>${label}<span class="meta">${all.filter((r) => r.on).length} on of ${all.length} · ${note}</span></button></td></tr>${open ? mine.map(line).join("") : ""}`;
  }).join("");
}
function phoneEvents(rows, list, ro, on, k) {
  const card = (r) => { const i = rows.indexOf(r), warn = warnText(r); return `<div class="card nodot ev ${r.on ? "" : "off"}" data-evrow="${i}" role="button" tabindex="0"><span class="ct"><b>${esc(rowName(r))}</b><span class="sub small">${rowSends(r)[0]}${r.g === "default" ? " · Default" : ""}${warn ? ` · <span class="t-warn">${warn}</span>` : ""}</span></span>${sw("evsw-" + i, r.on, ro, "evsw:" + i)}</div>`; };
  const groups = EV_GROUPS2.map(([key, label]) => {
    const all = rows.filter((r) => groupOf(r) === key), mine = list.filter((r) => groupOf(r) === key); if (!mine.length) return "";
    const open = !!k || S.evOpen[key];
    return `<button class="gbtn" type="button" data-evgrp="${key}" aria-expanded="${open}"><span class="chev ${open ? "" : "shut"}">${ico("chev", 14)}</span>${label}<span class="meta">${all.filter((r) => r.on).length} on of ${all.length}</span></button>${open ? mine.map(card).join("") : ""}`;
  }).join("");
  return `<div class="stack"><div class="sub">${on} on of ${rows.length}. Tap an event to see or change it.</div><label class="search">${ico("search")}<span class="sr">Search events</span><input id="evq" placeholder="Search events" value="${esc(S.evQ)}" autocomplete="off"></label>
  <div class="ev-tbl pcards">${groups || '<div class="empty">No events match your search.</div>'}</div><div class="sub small pdesk">Adding or deleting an event and drawing an area are best done on a larger screen.</div></div>`;
}
function secEvents() {
  const rows = S.cur.rows = eventRows(), ro = !can("events"), on = rows.filter((r) => r.on).length, k = S.evQ.trim().toLowerCase();
  const list = rows.filter((r) => !k || `${rowName(r)} ${rowType(r)} ${rowWhere(r)} ${r.k === "data" ? "" : namesOf(r.rule?.recipient_ids)}`.toLowerCase().includes(k));
  const line = (r) => {
    const i = rows.indexOf(r), m = rowSends(r), warn = !!warnText(r), goes = r.k === "data" ? "—" : namesOf(r.rule?.recipient_ids);
    return `<tr class="ev ${r.on ? "" : "off"}" data-evrow="${i}" tabindex="0"><td class="dotc"><span class="dot ${r.on ? (warn ? "warn" : "ok") : ""}"></span></td><td class="main-c">${esc(rowName(r))}${warn ? ` <span class="tag warn">${warnText(r)}</span>` : ""}${r.g === "default" ? ' <span class="tag">Default</span>' : ""}</td><td>${esc(rowType(r))}</td><td>${esc(rowWhere(r))}</td><td><span class="tag ${m[1]}">${m[0]}</span></td><td>${esc(goes)}</td><td class="sw-c">${sw("evsw-" + i, r.on, ro, "evsw:" + i)}</td></tr>`;
  };
  if (PH.matches) return phoneEvents(rows, list, ro, on, k);
  return `<div class="stack"><div class="bar flush"><span class="sub">${on} on of ${rows.length}. Click an event to see it, change it or delete it.</span><span class="sp"></span><button class="btn pri" type="button" data-addev ${ro ? "disabled title='Not for your role'" : ""}>${ico("plus")}Add event</button></div>
  <div class="bar flush"><label class="search">${ico("search")}<span class="sr">Search events</span><input id="evq" placeholder="Search events" value="${esc(S.evQ)}" autocomplete="off"></label>${k ? `<span class="sub">${list.length} found</span>` : ""}</div>
  ${rows.length ? `<div class="scroll"><table class="tbl ev-tbl"><thead><tr><th class="dotc"></th><th>Event</th><th>Event type</th><th>Where</th><th>Sends</th><th>Goes to</th><th class="sw-c">On</th></tr></thead><tbody>${list.length ? groupsHtml(rows, list, line, !!k) : '<tr><td colspan="7" class="empty">No events match your search.</td></tr>'}</tbody></table></div>` : `<div class="empty">No events yet. Add the first one.</div>`}</div>`;
}
/* Switch an event on or off straight from the list */
async function toggleRow(i, checked) {
  const r = S.cur.rows[i], base = `/admin/v1/tenants/${S.cid}/shop`;
  if (r.k === "data") {
    const c = r.cam, others = [c.use_footfall && r.flag !== "use_footfall", c.use_anpr && r.flag !== "use_anpr", c.use_areas].some(Boolean);
    if (!checked && !others && c.enabled) { S.pendingOff = i; return modal(`${mh("Switch this off?")}<p class="nm"><b>${esc(rowName(r))}</b></p><div class="note warn">This is the camera's only use. Switching it off stops watching this camera. Continue?</div><div class="row"><button class="btn pri" type="button" data-confirm="rowoff">Switch off</button><button class="btn" type="button" data-confirm="rowkeep">Keep it on</button></div>`); }
    await api.patch(`${base}/cameras/${c.id}`, { [r.flag]: checked, ...(r.flag === "use_anpr" && checked ? { stream: "main" } : {}) });
  } else {
    const e = r.rule;
    await api.put(`${base}/events/${e.type}`, { label: e.label, enabled: checked, params: e.params, recipient_ids: e.recipient_ids, quiet_start: e.quiet_start, quiet_end: e.quiet_end });
  }
  await loadCurrent(S.cid); toast(checked ? "Switched on" : "Switched off"); render(true);
}
/* Click an event: its details in a side panel, with Save and Delete */
function openEditEvent(i) {
  const r = S.cur.rows[i], kind = r.k === "area" ? AREA_KIND[r.type] : null, rule = r.rule;
  S.edit = { i, v: r.k === "data" ? {} : kind ? { ...(r.area.params || {}) } : { ...(rule.params || {}) }, pts: kind ? r.area.points.map((p) => [Math.round(p[0] * 640), Math.round(p[1] * 360)]) : null, to: [...(rule?.recipient_ids || [])], name: r.k === "data" ? rowName(r) : kind ? r.area.name : (rule.label || rule.title), on: r.on, clip: !!(rule?.params || {}).clip };
  drawEdit();
}
function drawEdit() {
  const { i, v, to, name } = S.edit, r = S.cur.rows[i], kind = r.k === "area" ? AREA_KIND[r.type] : null, data = r.k === "data", def = r.g === "default", ro = !can("events"), m = rowSends(r);
  const x = data ? { title: r.t, description: r.flag === "use_footfall" ? "Counts the people crossing the line marked on this camera. Used for the shop's visitor numbers and for crowding, after-hours and counting alerts. Sends no message." : "Reads the number plates of vehicles on this camera, for the vehicle list and the vehicle alerts. Sends no message.", settings: {} } : evMeta(r.type);
  const fields = data ? [] : kind ? areaFields(kind, v).map((f) => f.replace(/data-ev=/g, "data-xv=")) : Object.entries(x.settings || {}).map(([k, s]) => { if (s.type === "boolean") return `<div class="cfg"><span>Include a short video</span><span>${sw("x-clip", S.edit.clip, false, "xclip")}</span><span></span></div>`; const [label, unit] = PARAM[k] || [humanize(k), ""]; return `<div class="cfg"><span>${label}</span><input class="in sm" type="number" min="${s.min}" max="${s.max}" aria-label="${label}" data-xv="${k}" value="${v[k] ?? s.default}"><span class="sub">${unit}</span></div>`; });
  const marked = data && r.marked, warn = !!warnText(r), pdir = data && r.flag === "use_anpr" ? plateDirOf(r.cam) : null;
  const shared = r.k === "area" ? "Switching this on or off applies to every area of this kind in the shop." : def ? "Default alerts can be switched off, not deleted." : "Switch off to stop this without deleting it.";
  $("#layer").innerHTML = `<div class="shade" data-close></div><aside class="drawer ev" role="dialog" aria-label="${esc(name)}"><div class="dh"><div><h2>${esc(name)}</h2><div class="sub">${esc(rowType(r))} · ${esc(rowWhere(r))}</div></div><button class="ibtn" type="button" id="dclose" aria-label="Close">${ico("x")}</button></div>
  <div class="dbody"><div class="rowsw"><span><b>On</b><br><span class="sub small">${shared}</span></span>${sw("x-on", S.edit.on, ro, "xon")}</div>
  ${data ? "" : `<div><div class="sec-h">Name</div><input class="in full" id="x-name" value="${esc(name)}" ${ro ? "disabled" : ""}></div><div><div class="sec-h">Goes to</div><div class="stack">${S.cur.people.map((p) => `<label class="chk dark"><input type="checkbox" data-xto="${p.id}" ${to.includes(p.id) ? "checked" : ""}>${esc(p.name)}</label>`).join("") || '<span class="sub">Nobody yet</span>'}</div>${kind ? '<div class="sub small mt6">This list is shared by every area of this kind in the shop.</div>' : ""}</div>`}
  ${fields.length ? `<div><div class="sec-h">Settings</div><div class="stack">${fields.join("")}</div></div>` : ""}
  <div><div class="sec-h">Sends</div><span class="tag ${m[1]}">${m[0]}</span></div>
  ${data ? `<div class="${warn ? "note warn" : "sub"}">${r.flag === "use_footfall" ? (warn ? "No line is marked on this camera, so nothing is counted yet." : "A counting line is marked. People crossing the way the arrow points are counted IN.") : (pdir ? `${marked ? "A plate area is marked." : "Plates are read in the whole picture."} ${DIR_LABEL[pdir]} counts as coming in.` : "No direction is set, so vehicles on this camera have no in or out. Mark the plate area and choose the direction.")}</div><div><button class="btn ${warn ? "pri" : ""}" type="button" data-editmark="${i}" ${ro ? "disabled" : ""}>${r.flag === "use_footfall" ? (marked ? "Mark the line again" : "Mark the line") : (pdir ? "Change the plate area or direction" : "Mark the plate area and direction")}</button></div>` : ""}
  ${!data && NEEDS_PLATES.has(r.type) ? platesNote(r.type) : ""}
  ${kind ? `<div><button class="btn" type="button" data-redraw ${ro ? "disabled" : ""}>Redraw the area</button></div>` : ""}
  <div class="ponly sub small">${def ? "" : "Deleting this event"}${kind ? (def ? "Redrawing the area" : " or redrawing its area") : ""}${def && !kind ? "" : " is best done on a larger screen."}</div></div>
  <div class="df"><button class="btn pri" type="button" data-esave ${ro ? "disabled" : ""}>Save</button><button class="btn" type="button" id="dclose2">Cancel</button><span class="grow"></span>${def ? "" : `<button class="btn danger" type="button" data-delev="${i}" ${ro ? "disabled" : ""}>Delete</button>`}</div></aside>`;
}
async function saveEdit() {
  const E = S.edit, r = S.cur.rows[E.i], kind = r.k === "area" ? AREA_KIND[r.type] : null, rule = r.rule, base = `/admin/v1/tenants/${S.cid}/shop`, x = r.k === "data" ? null : evMeta(r.type);
  try {
    if (r.k === "data") {
      if (E.on !== r.on) { await toggleRowSilent(r, E.on); }
    } else if (kind) {
      const params = areaParams(kind, E.v);
      const list = S.cur.areas[r.cam.id].map((a) => a.id === r.area.id ? { id: a.id, kind: a.kind, name: E.name.trim() || a.name, points: E.pts.map(norm1), params } : { id: a.id, kind: a.kind, name: a.name, points: a.points, params: a.params });
      await api.put(`${base}/cameras/${r.cam.id}/areas`, { areas: list });
      await api.put(`${base}/events/${r.type}`, { label: rule?.label || null, enabled: E.on, params: rule?.params || {}, recipient_ids: E.to });
    } else {
      const params = {}; for (const [k, s] of Object.entries(x.settings || {})) params[k] = s.type === "boolean" ? !!E.clip : +(E.v[k] ?? s.default);
      await api.put(`${base}/events/${r.type}`, { label: E.name.trim() || null, enabled: E.on, params, recipient_ids: E.to });
    }
    closeModal(); await loadCurrent(S.cid); toast("Saved"); render(true);
  } catch (e) { toast(e.code === "use_required" ? "A camera needs at least one use. Delete it, or turn Watching off, instead." : errText(e), true); }
}
async function toggleRowSilent(r, checked) { await api.patch(`/admin/v1/tenants/${S.cid}/shop/cameras/${r.cam.id}`, { [r.flag]: checked, ...(r.flag === "use_anpr" && checked ? { stream: "main" } : {}) }); }
/* Redraw an area on the camera picture */
async function openRedraw() {
  const r = S.cur.rows[S.edit.i]; S.redraw = { pts: (S.edit.pts || []).map((p) => [p[0], p[1]]), snap: null, fresh: false }; const tmp = { target: r.cam, snap: null };      // starts with the area as it is now
  modal(`${mh("Redraw the area")}<div class="note" data-loading>Getting the picture from ${esc(r.cam.name)}… this can take up to 30 seconds.</div><div class="row"><button class="btn" type="button" data-close>Cancel</button></div>`);
  await loadSnap(tmp);
  if (!$("#layer [data-loading]")) return;                                  // closed while waiting
  if (tmp.snapErr) return blockedModal(r.cam.name, tmp.snapErr, "The area cannot be redrawn.", openRedraw);
  S.redraw.snap = tmp.snap; drawRedraw();
}
function drawRedraw() {
  const r = S.cur.rows[S.edit.i], R = S.redraw;
  modal(`${mh("Redraw the area")}${videoFrame(r.cam.name + " · " + cname(), R.pts, true, R.snap, undefined, undefined, undefined, r.cam.id)}<div class="vtool"><span class="sub">${R.fresh || !R.pts.length ? "Click to add the corners of the new area. At least 3 corners." : "This is the area now. Click on the picture to draw a new one, or press Use this area to keep it."}</span><span class="grow"></span><button class="btn sm" type="button" data-redrawclear>Start again</button></div><div class="row"><button class="btn pri" type="button" data-redrawsave ${R.pts.length >= 3 ? "" : "disabled"}>Use this area</button><button class="btn" type="button" data-redrawcancel>Cancel</button></div>`);
  const d = $("#draw"); if (d) d.addEventListener("click", (ev) => { const b = d.getBoundingClientRect(); if (!R.fresh) { R.fresh = true; R.pts = []; } R.pts.push([Math.round((ev.clientX - b.left) / b.width * 640), Math.round((ev.clientY - b.top) / b.height * 360)]); drawRedraw(); });
}
/* Delete, with the cautions */
function confirmDelete(i) {
  const r = S.cur.rows[i], shop = esc(cname());
  let what, extra = "";
  if (r.k === "data") { const c = r.cam, other = [c.use_footfall && r.flag !== "use_footfall", c.use_anpr && r.flag !== "use_anpr", c.use_areas].some(Boolean); what = `${r.t} on ${esc(c.name)}`; if (!other) extra = `<div class="note warn">This is the camera's only use. Deleting it stops watching this camera. Continue?</div>`; }
  else if (r.k === "area") { const c = r.cam, last = (S.cur.areas[c.id] || []).length === 1 && !c.use_footfall && !c.use_anpr; what = `${r.area.name} (${esc(evMeta(r.type)?.title || "")}) on ${esc(c.name)}`; if (last) extra = `<div class="note warn">This is the camera's only use. Deleting it stops watching this camera. Continue?</div>`; }
  else what = esc(r.rule.label || r.rule.title);
  modal(`${mh("Delete this event?")}<p class="nm"><b>${what}</b></p><p class="sub nm">${r.k === "rule" ? `${shop} stops getting this and it leaves the list. This changes this shop only; the event itself stays for every other shop.` : `It is removed from ${esc(r.cam.name)}. Messages already sent stay in the log.`}</p>${extra}<div class="row"><button class="btn danger" type="button" data-confirm="delev" data-i="${i}">Delete</button><button class="btn" type="button" data-close>Keep</button></div>`);
}
async function doDelete(i) {
  const r = S.cur.rows[i], base = `/admin/v1/tenants/${S.cid}/shop`;
  if (r.k === "rule") await api.del(`${base}/events/${r.type}`);
  else if (r.k === "data") {
    const c = r.cam, other = [c.use_footfall && r.flag !== "use_footfall", c.use_anpr && r.flag !== "use_anpr", c.use_areas].some(Boolean), kind = r.flag === "use_footfall" ? "count_line" : "anpr_zone";
    const zones = await api.get(`${base}/cameras/${c.id}/zones`);                         // the marked line or plate area goes too, or the row would stay in the list switched off
    if (zones.some((z) => z.kind === kind)) await api.put(`${base}/cameras/${c.id}/zones`, { zones: zones.filter((z) => z.kind !== kind).map((z) => ({ kind: z.kind, points: z.points, in_direction: z.in_direction || null })) });
    await api.patch(`${base}/cameras/${c.id}`, { [r.flag]: false, ...(other ? {} : { enabled: false }) });
  }
  else { const c = r.cam, list = (S.cur.areas[c.id] || []).filter((a) => a.id !== r.area.id), last = !list.length && !c.use_footfall && !c.use_anpr; if (last) await api.patch(`${base}/cameras/${c.id}`, { enabled: false }); await api.put(`${base}/cameras/${c.id}/areas`, { areas: list.map((a) => ({ id: a.id, kind: a.kind, name: a.name, points: a.points, params: a.params })) }); }
  closeModal(); await loadCurrent(S.cid); toast("Deleted"); render(true);
}

/* ------------------------------------------------------------------------------------------------ Messages */
async function pageMessages() { await loadLog(null, false, true); return `<div class="rise">${logTable(true)}</div>`; }

/* ------------------------------------------------------------------------------------------------ Setup */
const lock = (n) => `<div class="empty rise">${ico("lock", 28)}<h2 class="h2 pt8">${n} is for ${n === "WhatsApp connection" ? "the Owner" : "Owner or Admin"}</h2><p>Ask the Owner if you need this changed.</p></div>`;
function pageMore() {
  const m = S.me || {}, desk = ["Activity log", "WhatsApp templates", "WhatsApp connection", "Console users", "Data kept", "Adding customers and events", "Drawing areas on the picture"];
  return `<div class="rise stack"><div class="card nodot"><span class="av lg">${esc(initials(m.name))}</span><span class="ct"><b>${esc(m.name || "")}</b><span class="sub small">${esc(m.email || "")}</span></span><span class="tag brand">${esc(ROLES[S.role]?.[0] || "")}</span></div>
   <div class="sec-h">This phone</div>
   <button class="card nodot" type="button" data-pwopen>${ico("lock", 20)}<span class="ct"><b>Change password</b></span>${ico("chev", 16)}</button>
   <button class="card nodot" type="button" id="theme2">${ico("moon", 20)}<span class="ct"><b>Dark or light</b><span class="sub small">Switch how it looks</span></span>${ico("chev", 16)}</button>
   <button class="card nodot out" type="button" data-signout>${ico("out", 20)}<span class="ct"><b>Sign out</b></span></button>
   <details class="dgrp"><summary>${ico("screen", 20)}<span class="ct"><b>On a larger screen</b><span class="sub small">${desk.length} things that are best done on a computer or tablet</span></span>${ico("chev", 16)}</summary><div class="stack">${desk.map((x) => `<div class="card nodot desk"><span class="ct"><b>${x}</b><span class="sub small">Best done on a larger screen</span></span><span class="tag">Larger screen</span></div>`).join("")}</div></details>
   <div class="sub small mid">Control Center 1.0</div></div>`;
}
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
  if (S.setup === "keep") {
    if (!can("keep")) return lock("Data kept");
    const k = await api.get("/admin/v1/maintenance/keep"), days = S.keepDays || k.days, prev = await api.get(`/admin/v1/maintenance/keep/preview?days=${days}`), last = k.last_run;
    const n = (x) => (x ?? 0).toLocaleString("en-IN");
    return `<div class="rise"><div class="title"><h1>Data kept</h1></div><div class="form"><div class="note">One setting for every customer. Everything older than this is removed automatically (the clean-up runs every few hours): alerts and their messages, people counts, plate reads, and pictures and clips. Pictures and clips are kept for the same time, so a longer time uses more disk space.</div>
    <div class="f f-narrow"><label for="kd">Keep data for<small>Not shorter than 7 days</small></label><select class="in time" id="kd" data-keepdays>${[7, 14, 30, 60, 90, 180, 365].map((d) => `<option value="${d}" ${d === days ? "selected" : ""}>${d} days</option>`).join("")}</select></div>
    <div class="pn"><h3>${days === k.days ? `If you keep ${days} days, this is what is older right now` : `If you save ${days} days, this much would be removed at the next clean-up`}</h3><table class="tbl"><tbody>${[["Alerts", prev.alerts], ["Messages", prev.messages], ["People counts", prev.people_counts], ["Plate reads", prev.plate_reads], ["Pictures and clips", prev.pictures_and_clips]].map(([l, v]) => `<tr><td>${l}</td><td class="num">${n(v)}</td></tr>`).join("")}</tbody></table></div>
    <div class="actions"><button class="btn pri" type="button" data-keepsave ${days === k.days ? "disabled" : ""}>Save</button><button class="btn" type="button" data-keepclear>Clear now</button></div>
    <div class="sub">${k.updated_by ? `Set by ${esc(k.updated_by)} on ${esc(fmtTime(k.updated_at))}. ` : ""}${last ? `Last clean-up: ${esc(fmtTime(last.ran_at))}${last.ok ? "" : " (some steps failed)"}.` : "No clean-up has run yet."}</div></div></div>`;
  }
  if (S.setup === "team") {
    if (!can("team")) return lock("Console users");
    const st = await api.get("/admin/v1/staff"); S.team = st.items;
    return `<div class="rise"><div class="bar"><span class="sp"></span><button class="btn pri" type="button" data-invite>${ico("plus")}Add a person</button></div>
    <div class="scroll mb22"><table class="tbl"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Last sign-in</th><th></th></tr></thead><tbody>${st.items.map((m) => `<tr><td class="main-c">${esc(m.name)}</td><td>${esc(m.email)}</td><td><span class="tag brand">${ROLES[m.role]?.[0] || humanize(m.role)}</span></td><td class="num">${m.last_login_at ? fmtTime(m.last_login_at) : "Never"}</td><td>${m.role === "owner" && S.role !== "owner" ? "" : `<button class="btn sm" type="button" data-editstaff="${m.id}">Edit</button> `}${m.id === S.me.id ? "" : m.role === "owner" && S.role !== "owner" ? "" : `<button class="btn sm" type="button" data-${m.status === "active" ? "disable" : "enable"}="${m.id}">${m.status === "active" ? "Switch off" : "Switch on"}</button> <button class="btn sm" type="button" data-resetlink="${m.id}">New sign-in link</button>`}</td></tr>`).join("")}</tbody></table></div>
    <h2 class="h2">What each role can do</h2><div class="scroll"><table class="tbl matrix"><thead><tr><th></th>${Object.values(ROLES).map((r) => `<th>${r[0]}</th>`).join("")}</tr></thead><tbody>${PERMS.map((p) => `<tr><td>${p[0]}</td>${[0, 1, 2, 3].map((i) => `<td>${"oas".includes(p[1][i]) ? '<span class="tick">✓</span>' : '<span class="cross">—</span>'}</td>`).join("")}</tr>`).join("")}</tbody></table></div></div>`;
  }
  if (S.setup === "wa") {
    if (!can("wa")) return lock("WhatsApp connection");
    const tab = S.waTab === "alerts" ? "alerts" : "sum";
    const tabs = `<div class="dirs mb12" role="group" aria-label="Which connection"><button class="btn" type="button" data-watab="sum" aria-pressed="${tab === "sum"}">Summaries</button><button class="btn" type="button" data-watab="alerts" aria-pressed="${tab === "alerts"}">Alerts</button></div>`;
    if (tab === "alerts") {
      const c = await api.get("/admin/v1/webhooks/connection"); S.data.alc = c; const ok = c.key.set && c.url;
      return `<div class="rise"><div class="title"><h1>WhatsApp connection</h1><span class="${ok ? "t-ok" : "t-warn"} bold"><span class="dot ${ok ? "ok" : "warn"}"></span> ${ok ? "Connected" : "Not finished"}</span></div>${tabs}<div class="form"><div class="note">One address and key for every alert (Camera offline, Recorder offline and so on). Change the key here once and every alert that uses this connection follows. Summaries have their own tab.</div>
      <div class="f"><label for="a1">Address</label><input class="in wide" id="a1" data-al="url" value="${esc(c.url || "")}" placeholder="https://"><label for="a2">Key<small>${c.key.set ? `In use, ends ${esc(c.key.hint || "")}. It cannot be shown again.` : "Not set"}</small></label><div class="stack"><input class="in wide" id="a2" type="password" data-alkey placeholder="${c.key.set ? "Paste a new key to replace it" : "Paste the key"}" autocomplete="off"><label class="chk dark"><input type="checkbox" id="a3" data-alsum>Use the same key as the summaries</label></div>
      <span class="lb">Alerts using it<small>The rest send to their own address</small></span><span class="pt9"><b>${c.using} of ${c.alerts}</b> alerts${c.using < c.alerts && c.url ? ` <button class="btn sm" type="button" data-alapply>Use for all alerts</button>` : ""}</span></div>
      <div class="actions"><button class="btn pri" type="button" data-alsave>Save</button><button class="btn" type="button" data-altest>${ico("send", 15)}Send a test</button></div><div class="sub">${c.updated_by ? `Set by ${esc(c.updated_by)} on ${esc(fmtTime(c.updated_at))}.` : ""}</div></div></div>`;
    }
    const s = await api.get("/admin/v1/summaries"); S.data.sum = s; const c = s.config;
    return `<div class="rise"><div class="title"><h1>WhatsApp connection</h1><span class="${s.key.set && c.url ? "t-ok" : "t-warn"} bold"><span class="dot ${s.key.set && c.url ? "ok" : "warn"}"></span> ${s.key.set && c.url ? "Connected" : "Not finished"}</span></div>${tabs}<div class="form"><div class="f"><label for="w1">Address</label><input class="in wide" id="w1" data-wa="url" value="${esc(c.url || "")}" placeholder="https://"><label for="w2">Key<small>${s.key.set ? `In use, ends ${esc(s.key.hint || "")}. It cannot be shown again.` : "Not set"}</small></label><input class="in wide" id="w2" type="password" data-wakey placeholder="${s.key.set ? "Paste a new key to replace it" : "Paste the key"}" autocomplete="off">
    <span class="lb">What counts as sent<small>Any 2xx answer from the provider</small></span><span class="pt9">Wait ${c.timeout} seconds for the answer, retry ${c.retries} times, ${c.gap} seconds apart</span></div>
    <div class="actions"><button class="btn pri" type="button" data-wasave>Save</button><button class="btn" type="button" data-testwa>${ico("send", 15)}Send a test to my number</button></div></div></div>`;
  }
  if (S.setup === "sys") {
    if (!can("sys")) return lock("System health");
    const h = await api.get("/admin/v1/system/health"); S.sysAt = Date.now();
    const lv = (x) => (x === "crit" ? "crit" : x === "warn" ? "warn" : "ok"), word = { ok: "Healthy", warn: "Needs a look", crit: "Needs action" };
    const n = (x) => (x ?? 0).toLocaleString("en-IN"), sv = h.server, cm = h.cameras, ms = h.messages, st = h.storage, cp = h.capacity;
    const gb = (mb) => (mb == null ? "Not measured" : mb >= 1000 ? (mb / 1000).toFixed(1) + " GB" : Math.round(mb) + " MB");
    const meter = (label, v, l, hint) => `<div class="mt"><div class="mtl"><span>${label}</span><b>${v == null ? "Not measured" : v + "%"}</b></div><div class="mtb" role="img" aria-label="${label} ${v ?? 0} per cent"><i class="${lv(l)}" data-w="${Math.min(100, v ?? 0)}"></i></div>${hint ? `<div class="sub mth">${hint}</div>` : ""}</div>`;
    const ago = (iso) => { if (!iso) return "Never"; const sec = Math.max(0, Math.round((Date.now() - new Date(iso)) / 1000)); return sec < 20 ? "Just now" : sec < 90 ? sec + " seconds ago" : sec < 5400 ? Math.round(sec / 60) + " minutes ago" : sec < 172800 ? Math.round(sec / 3600) + " hours ago" : Math.round(sec / 86400) + " days ago"; };
    const spark = (vals, label, line, max) => { const v = (vals || []).filter((x) => x != null).reverse(); if (v.length < 2) return `<div class="sub pad">Collecting. This chart fills in as the server saves its readings, every 5 minutes.</div>`; const W = 300, H = 80, m = max || Math.max(1, ...v), x = (i) => 6 + i * (W - 12) / (v.length - 1), y = (a) => H - 8 - (Math.min(a, m) / m) * (H - 16), pts = v.map((a, i) => `${x(i).toFixed(1)},${y(a).toFixed(1)}`).join(" ");
      return `<svg class="spk" viewBox="0 0 ${W} ${H}" role="img" aria-label="${label}">${line ? `<line class="spk-l" x1="6" x2="${W - 6}" y1="${y(line).toFixed(1)}" y2="${y(line).toFixed(1)}"/>` : ""}<polygon class="spk-a" points="6,${H - 8} ${pts} ${W - 6},${H - 8}"/><polyline class="spk-p" points="${pts}"/><circle class="spk-e" cx="${x(v.length - 1).toFixed(1)}" cy="${y(v[v.length - 1]).toFixed(1)}" r="3.5"/></svg>`; };
    const maxPer = Math.max(1, ...ms.per_hour);
    const up = h.services.filter((x) => x.level === "ok").length;
    const days = sv.disk_days_left == null ? "" : `About ${sv.disk_days_left >= 100 ? "100+" : Math.round(sv.disk_days_left)} days left at this pace`;
    return `<div class="rise"><div class="title"><h1>System health</h1><span class="sub">Our own server and services. Updated ${esc(ago(h.generated_at).toLowerCase())}, and it refreshes by itself.</span><span class="sp"></span><button class="btn" type="button" data-sysrefresh>Refresh now</button></div>
    <div class="hv ${lv(h.overall)}"><span class="dot ${lv(h.overall)}"></span><div><b>${h.overall === "ok" ? "Everything is healthy" : h.todo.length === 1 ? "1 thing needs attention" : h.todo.length + " things need attention"}</b>${h.overall === "ok" ? `<div class="sub">All ${h.services.length} services are running, ${cm.streaming} of ${cm.expected} cameras are being analysed, and no message is stuck.</div>` : `<ul>${h.todo.map((t) => `<li><span class="dot ${lv(t.level)}"></span>${esc(t.text)}</li>`).join("")}</ul>`}</div></div>
    <div class="stats"><div class="st"><b>${up} of ${h.services.length}</b><span>Services running</span></div><div class="st"><b class="${cm.level === "ok" ? "" : "t-" + lv(cm.level)}">${cm.streaming} of ${cm.expected}</b><span>Cameras analysed</span></div><div class="st"><b class="${sv.disk_level === "ok" ? "" : "t-" + lv(sv.disk_level)}">${sv.disk_pct}%</b><span>Disk used</span></div><div class="st"><b class="${ms.level === "ok" ? "" : "t-" + lv(ms.level)}">${n(ms.waiting)}</b><span>Messages waiting</span></div><div class="st"><b>${cp.room == null ? "-" : cp.room}</b><span>More cameras fit</span></div></div>
    <div class="cc"><div>
     <div class="pn"><h3>Services <span class="sub light">when each last reported in</span></h3><table class="tbl"><thead><tr><th class="dotc"></th><th>Service</th><th>State</th><th>Last heard</th></tr></thead><tbody>${h.services.map((x) => `<tr><td class="dotc"><span class="dot ${lv(x.level)}"></span></td><td class="main-c">${esc(x.name)}</td><td class="t-${lv(x.level)}">${esc(x.text)}</td><td class="sub">${esc(ago(x.last_seen))}</td></tr>`).join("")}</tbody></table></div>
     <div class="pn"><h3>Camera pipeline <span class="dot ${lv(cm.level)}"></span></h3><div class="pad"><div class="mini"><div><b>${cm.streaming} of ${cm.expected}</b><span>cameras analysed</span></div><div><b>${cm.fps == null ? "-" : cm.fps}</b><span>pictures a second, on average</span></div><div><b>${cm.offline}</b><span>reported unreachable</span></div></div>
      ${cm.silent.length ? `<div class="note warn pn-n"><b>Sent nothing for 10 minutes</b><br>${cm.silent.map((x) => `${esc(x.camera)} <span class="sub">${esc(x.customer)}, last heard ${esc(ago(x.last_seen).toLowerCase())}</span>`).join("<br>")}</div>` : `<div class="sub">Every camera that should be analysed has reported in the last 10 minutes.</div>`}</div></div>
     <div class="pn"><h3>WhatsApp messages, last 24 hours <span class="dot ${lv(ms.level)}"></span></h3><div class="pad"><div class="mini"><div><b>${n(ms.sent)}</b><span>sent</span></div><div><b class="${ms.failed >= 5 ? "t-warn" : ""}">${n(ms.failed)}</b><span>failed</span></div><div><b class="${ms.waiting ? "t-warn" : ""}">${n(ms.waiting)}</b><span>waiting</span></div></div>
      <div class="bars" role="img" aria-label="Messages sent each hour, the last 24 hours">${ms.per_hour.slice().reverse().map((c) => `<i class="${c ? "" : "zero"}" data-h="${Math.max(2, Math.round(c / maxPer * 88))}" title="${c}"></i>`).join("")}</div>${ms.oldest_waiting_minutes ? `<div class="note warn pn-n">The oldest message has been waiting ${ms.oldest_waiting_minutes} minutes. Check the WhatsApp connection under Setup.</div>` : ""}</div></div>
    </div><div>
     <div class="pn"><h3>This server <span class="dot ${lv(sv.level)}"></span></h3><div class="pad mts">${meter("Processor", sv.load_pct == null ? null : Math.round(sv.load_pct), sv.load_level, `${sv.cpus} processor${sv.cpus > 1 ? "s" : ""}`)}${meter("Memory", sv.mem_pct == null ? null : Math.round(sv.mem_pct), sv.mem_level, sv.mem_total_gb ? sv.mem_total_gb + " GB in total" : "")}${meter("Disk space", Math.round(sv.disk_pct), sv.disk_level, `${sv.disk_free_gb} GB free of ${sv.disk_total_gb} GB. ${days}`)}</div></div>
     <div class="pn"><h3>Room to grow <span class="dot ${lv(cp.level)}"></span></h3><div class="pad">${cp.room == null ? `<div class="sub">Not enough running yet to estimate. It appears once cameras are being analysed.</div>` : `<div class="mini"><div><b>${cp.room}</b><span>more cameras fit</span></div><div><b>${cp.cameras}</b><span>cameras running</span></div></div><div class="sub">An estimate from how busy the server is now. Plan more capacity when the processor stays above 70%.</div>`}</div></div>
     <div class="pn"><h3>Storage and database <span class="dot ${st.cloud ? "ok" : ""}"></span></h3><table class="tbl"><tbody><tr><td>Pictures and videos</td><td class="end">${st.cloud ? "In cloud storage" : gb(st.evidence_mb)}</td></tr><tr><td>Database</td><td class="end"><b>${gb(st.database_mb)}</b></td></tr>${st.biggest.map((x) => `<tr><td class="sub">${esc(x.name)}</td><td class="end sub">${gb(x.mb)}</td></tr>`).join("")}</tbody></table></div>
    </div></div>
    <div class="pn"><h3>Last 24 hours</h3><div class="pad trends"><div><b>Disk used (%)</b>${spark(h.trend.disk, "Disk used over the last 24 hours", 80, 100)}</div><div><b>Processor busy (%)</b>${spark(h.trend.load, "Processor busy over the last 24 hours", 70, 100)}</div><div><b>Memory used (%)</b>${spark(h.trend.mem, "Memory used over the last 24 hours", 85, 100)}</div></div></div></div>`;
  }
  const a = await api.get("/admin/v1/audit?limit=40");
  return `<div class="rise">${(a.items || []).length ? `<div class="scroll"><table class="tbl"><thead><tr><th>When</th><th>Who</th><th>What</th></tr></thead><tbody>${a.items.map((r) => `<tr><td class="num">${fmtTime(r.ts)}</td><td>${esc(r.actor_name || "")}</td><td class="main-c">${esc(humanize(r.action || ""))}${r.target_type ? ` · ${esc(r.target_type)}` : ""}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty">Nothing recorded yet.</div>`}</div>`;
}
async function openTpl(type) {
  const a = S.data.cat.find((x) => x.type === type), w = S.data.wh.items.find((x) => x.alert_type === type), direct = ["daily_summary", "weekly_summary"].includes(type), m = MEDIA[carries(type)];
  $("#layer").innerHTML = `<aside class="drawer" role="dialog" aria-label="${esc(a.title)}"><div class="row spread"><h2 class="h2">${esc(a.title)}</h2><button class="ibtn" type="button" id="dclose" aria-label="Close">${ico("x")}</button></div>
  <div class="row"><span class="tag ${m[1]}">${m[0]}</span></div>
  ${direct ? `<div class="note">Summaries are sent as direct text, so they have no template.</div>` : w ? `<div><div class="sec-h mb6">Name</div><input class="in full" id="d-name" value="${esc(w.name)}"></div>${w.use_shared ? `<div class="note">This alert sends through the shared WhatsApp connection (address and key), set under Setup, WhatsApp connection, Alerts. <button class="btn sm" type="button" id="down" data-wid="${w.id}">Use its own address and key</button></div>` : `<div><div class="sec-h mb6">Address</div><input class="in full" id="d-url" value="${esc(w.url)}"></div><div><div class="sec-h mb6">Access key</div><div class="sub mb6">${w.key_hint ? `In use, ends ${esc(w.key_hint)}. It cannot be shown again.` : "No key saved. The provider will refuse this alert until one is added."}</div><input class="in full" id="d-key" type="password" autocomplete="off" placeholder="Paste a new key to replace it"><label class="chk dark pt8"><input type="checkbox" id="d-keyall" checked>Use it for every alert with this address</label><div class="row pt8"><button class="btn" type="button" id="dkey" data-wid="${w.id}">Save key</button>${S.role === "owner" ? `<button class="btn" type="button" id="dkeyconn" data-wid="${w.id}">Use the WhatsApp connection key</button>` : ""}</div></div>${S.data.wh.connection?.url ? `<div class="row"><button class="btn" type="button" id="dshared" data-wid="${w.id}">Use the shared connection</button></div>` : ""}`}
  <div><div class="sec-h mb6">Message sent to the provider</div><textarea class="in area" id="d-tpl" spellcheck="false">${esc(w.payload_template || "")}</textarea><div class="chips">${a.shortcodes.map((s) => `<button class="sc pick-sc" type="button" data-sc="${s}">{${s}}</button>`).join(" ")}</div></div>
  <div class="row"><span>On</span>${sw("d-on", w.active, false, "")}</div>
  <div class="row"><button class="btn pri" type="button" id="dsave" data-wid="${w.id}">Save</button><button class="btn" type="button" id="dtest" data-wid="${w.id}">Send a test</button></div>` : `<div class="note">This alert is not connected to the WhatsApp provider yet.</div><div><div class="sec-h mb6">Address</div><input class="in full" id="d-url" value="${esc(S.data.wh.items[0]?.url || "")}" placeholder="https://"></div><div><button class="btn pri" type="button" id="dnew" data-type="${type}">Set up this alert</button></div>`}</aside>`;
}

/* ------------------------------------------------------------------------------------------------ modal, render, events */
function modal(html) { $("#layer").innerHTML = `<div class="scrim" data-scrim><div class="modal" role="dialog" aria-modal="true">${html}</div></div>`; const b = $("#layer .modal input, #layer .modal button"); if (b) b.focus(); }
/* ---- live view: moving pictures of one camera, from the camera picture window (Owner and Admin) ---- */
async function startLive(camId) {
  if (S.live) return;
  const frame = () => document.querySelector(".vframe .vpic"), say = (t, on) => { const x = $("#lv-state"); if (x) { x.textContent = t; x.classList.toggle("on", !!on); } }, button = (t) => { const b = $("#lv-btn"); if (b) b.textContent = t; };
  if (!frame()) return;
  const LIVE_MS = PH.matches ? 120000 : 300000, ctl = new AbortController(), L = S.live = { ctl, url: null, timer: null, ends: Date.now() + LIVE_MS };
  const campic = frame().hasAttribute("data-campic-box");
  if (campic) { frame().textContent = ""; frame().insertAdjacentHTML("beforeend", '<span class="picmsg">Starting the live view…</span>'); }
  button("Stop"); say("Starting…", true);
  const tick = () => { const left = Math.max(0, Math.round((L.ends - Date.now()) / 1000)); say(`Live · ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} left`, true); if (PH.matches && left === 0 && L.url) { stopLive(true); toast("The live view stopped after 2 minutes. Press Watch live to continue."); } };
  L.timer = setInterval(() => { if (L.url) tick(); }, 1000);
  let got = false;
  try {
    await api.stream(`/admin/v1/tenants/${S.cid}/shop/cameras/${camId}/live`, (bytes) => {
      if (S.live !== L) return;
      const box = frame(); if (!box) return;
      let img = box.querySelector("img.snap");
      if (!img) { img = Object.assign(document.createElement("img"), { className: "snap", alt: "Live picture" }); const scene = box.querySelector("svg:not(.draw)"); if (scene) scene.remove(); box.prepend(img); }
      if (!got) { got = true; L.ends = Date.now() + LIVE_MS; box.querySelectorAll(".picmsg").forEach((m) => m.remove()); }
      const url = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" })), old = L.url; L.url = url; img.src = url; if (old) setTimeout(() => URL.revokeObjectURL(old), 500);
      tick();
    }, { signal: ctl.signal });
    if (S.live === L) { say(got ? "Live ended" : "Picture", false); if (got) toast("The live view ended. Press Watch live to continue."); else if (campic) loadPics(); }
  } catch (e) { say("Picture", false); toast(errText(e), true); if (!got && campic) loadPics(); }
  finally { clearInterval(L.timer); if (S.live === L) { S.live = null; button("Watch live"); const x = $("#lv-state"); if (x) x.classList.remove("on"); } }
}
function stopLive(manual) {
  const L = S.live; if (!L) return; L.ctl.abort(); clearInterval(L.timer); S.live = null;
  if (L.url) setTimeout(() => URL.revokeObjectURL(L.url), 1000);
  const state = $("#lv-state"), btn = $("#lv-btn"); if (manual && state) { state.textContent = "Picture"; state.classList.remove("on"); } if (btn) btn.textContent = "Watch live";
}
const closeModal = () => { stopLive(); $("#layer").innerHTML = ""; S.edit = null; };
const mh = (t) => `<div class="mh"><span class="spacer"></span><h2>${t}</h2><button class="ibtn" type="button" data-close aria-label="Close">${ico("x")}</button></div>`;
const PAGES = { more: pageMore, overview: pageOverview, customers: pageCustomers, new: pageNew, customer: pageCustomer, event: pageEvent, messages: pageMessages, setup: pageSetup };
PH.addEventListener("change", () => { if (S.me) render(true); });
let picRun = 0;
setInterval(() => { if (S.route === "setup" && S.setup === "sys" && !document.hidden && !$("#layer").innerHTML && Date.now() - (S.sysAt || 0) > 25000) render(true); }, 30000);
async function loadPics() {
  const run = ++picRun, jobs = [...document.querySelectorAll("[data-pic],[data-campic-box]")];
  const one = async (el) => {
    if (run !== picRun || !el.isConnected) return;
    const url = el.dataset.pic ? (([rid, ch]) => `/admin/v1/tenants/${S.cid}/shop/recorders/${rid}/channels/${ch}/snapshot`)(el.dataset.pic.split("|")) : `/admin/v1/tenants/${S.cid}/shop/cameras/${el.dataset.campicBox}/snapshot`;
    try { const blob = await api.call("GET", url, undefined, { blob: true }); if (!el.isConnected) return; const img = document.createElement("img"); img.className = "snap"; img.alt = "Camera picture"; img.src = URL.createObjectURL(blob); el.textContent = ""; el.appendChild(img); }
    catch (e) { if (!el.isConnected) return; const m = el.querySelector(".picmsg") || el.appendChild(document.createElement("span")); m.className = "picmsg"; m.textContent = e.status === 409 ? "Camera offline" : e.status === 504 ? "No picture: the camera did not answer in time" : "No picture available right now";
    const tile = el.closest(".cam[data-ecam]"); if (tile) { tile.classList.add("offl"); const cb = tile.querySelector(".cb"); if (cb && !cb.querySelector(".tag.crit")) cb.insertAdjacentHTML("beforeend", '<span class="tag crit">Offline</span>'); } }
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
  p.innerHTML = html; p.scrollTop = keepScroll ? y : 0; syncHash();
  if (PH.matches) {                                                                // on a phone the page itself scrolls, and a move between tabs slides the new page in from that side
    if (!keepScroll) window.scrollTo(0, 0);
    const tabs = tabList().map((t) => t[0]), now = tabs.indexOf(tabOf()), was = S.tabIdx ?? now; S.tabIdx = now;
    p.classList.remove("in-r", "in-l", "in-u"); void p.offsetWidth;
    if (!keepScroll) p.classList.add(now > was ? "in-r" : now < was ? "in-l" : "in-u");
  }
  if (S.route === "customer") side();                                        // the menu depends on what this customer has (loaded just now)
  p.querySelectorAll(".bars i[data-h]").forEach((b) => { b.style.height = b.dataset.h + "px"; });
  p.querySelectorAll(".mtb i[data-w]").forEach((b) => { b.style.width = b.dataset.w + "%"; });
  p.querySelectorAll(".plot span[data-hp]").forEach((b) => { b.style.height = b.dataset.hp + "%"; });
  loadPics();
  const d = $("#draw"); if (d) d.addEventListener("click", (ev) => { const r = d.getBoundingClientRect(), E = S.ev; if (markMode(E.key) === "line" && E.pts.length >= 2) E.pts = []; E.pts.push([Math.round((ev.clientX - r.left) / r.width * 640), Math.round((ev.clientY - r.top) / r.height * 360)]); E.touched = true; E.whole = false; render(true); });
}
const SECTIONS = ["cams", "health", "events", "count", "vehicles", "people", "hours", "log"], SETUPS = ["tpl", "team", "keep", "wa", "sys", "audit"];
function hashNow() {
  if (S.route === "more") return "more";
  if (S.route === "customer" && S.cid) return `customer/${S.cid}/${S.section}`;
  if (S.route === "setup") return `setup/${S.setup}`;
  return ["overview", "customers", "messages"].includes(S.route) ? S.route : null;        // the step-by-step screens are not remembered
}
function syncHash() { const h = hashNow(); if (h && location.hash !== "#" + h) { try { history.replaceState(null, "", "#" + h); } catch { /* fine */ } } }
function go(r, extra) { Object.assign(S, extra || {}); S.route = r; S.pop = false; closeModal(); return render(); }
const ORDER = NSTEPS.map((s) => s[0]);
let busyAct = false;
document.addEventListener("click", async (ev) => {
  const t = ev.target.closest("[data-tab],[data-go],[data-cust],[data-view],[data-pop],[data-filter],[data-lchip],[data-lrange],[data-lmore],[data-step],[data-sec],[data-setup],[data-sysrefresh],[data-live],[data-campic],[data-rencam],[data-hrec],[data-evgrp],[data-stat],[data-watab],[data-alsave],[data-alapply],[data-altest],[data-next],[data-prev],[data-create],[data-addph],[data-editstaff],[data-delstaff],[data-delph],[data-editc],[data-hmode],[data-copyh],[data-rtype],[data-addp],[data-delp],[data-tpl],[data-close],[data-scrim],[data-finish],[data-testmsg],[data-pausec],[data-extend],[data-extendc],[data-delc],[data-save],[data-resend],[data-invite],[data-testwa],[data-addev],[data-estep],[data-evnext],[data-evprev],[data-evcreate],[data-erec],[data-ecam],[data-etype],[data-evrow],[data-evgrp],[data-editmark],[data-delev],[data-esave],[data-redraw],[data-redrawclear],[data-redrawsave],[data-redrawcancel],[data-clearpts],[data-snaptry],[data-retry2],[data-flip],[data-pdir],[data-mpdir],[data-goplates],[data-whole],[data-mflip],[data-mwhole],[data-mclear],[data-msave],[data-evdone],[data-retry],[data-addrec],[data-campic],[data-findcams],[data-showconn],[data-veh],[data-vmore],[data-export],[data-editrec],[data-newpw],[data-delrec],[data-addcams],[data-chall],[data-setupall],[data-disable],[data-enable],[data-resetlink],[data-wasave],[data-keepsave],[data-keepclear],[data-confirm],#home,#theme,#theme2,#signout,[data-signout],[data-pwopen]");
  if (S.pop && !ev.target.closest(".tools")) { S.pop = false; render(true); }
  if (ev.target.closest(".ev-tbl .sw")) return;                      // a switch in the Events list is handled by its own change
  if (!t) return; const d = t.dataset;
  try {
    if ("scrim" in d) { if (ev.target !== t) return; return closeModal(); }
    if (t.id === "home") return go("overview");
    if (t.id === "theme" || t.id === "theme2") { const dark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme:dark)").matches); document.documentElement.dataset.theme = dark ? "light" : "dark"; return shell(); }
    if (t.id === "signout" || "signout" in d) { try { await api.call("POST", "/admin/v1/auth/logout", { refresh_token: api.refreshToken }); } catch { /* signing out anyway */ } api.clear(); return location.replace("/"); }
    if ("pwopen" in d) { await openProfile(); return $("#pf-pwb")?.click(); }
    if ("close" in d) return closeModal();
    if ("retry" in d) return render();
    if (d.tab) { S.logData = null; if (d.tab === "health") { S.setup = "sys"; return go("setup"); } if (d.tab === "customers") await loadCustomers(); return go(d.tab); }
    if (d.go || d.cust || d.sec || d.setup) S.logData = null;
    if (d.go) { if (d.go === "new") { S.form = blankForm(); S.step = "shop"; } if (d.go === "customers") await loadCustomers(); if (d.go === "customer") S.cur = null; return go(d.go); }
    if (d.cust) { S.cid = d.cust; S.cur = null; S.section = PH.matches ? "health" : "cams"; return go("customer"); }
    if (d.view) { S.view = d.view; return render(true); }
    if ("pop" in d) { S.pop = !S.pop; return render(true); }
    if (d.filter) { S.filter = d.filter; S.pop = false; return render(true); }
    if (d.lchip) { S.logChip = d.lchip; return render(true); }
    if (d.lrange) { S.logRange = d.lrange; return render(true); }
    if ("lmore" in d) { const where = S.route === "customer" ? S.cid : null; await loadLog(where, true); return render(true); }
    if (d.step) { S.step = d.step; return render(); }
    if (d.sec) { S.section = d.sec; return render(); }
    if (d.setup) { S.setup = d.setup; return go("setup"); }
    if ("next" in d) { if (S.step === "shop") { const bad = ownerProblem(S.form); if (bad) return pointAt(...bad); const f = S.form, one = f.people[0]; if (one && ((!one.n && !one.m) || (one.n === f._pn && one.m === f._pm))) { one.n = f.ownerName.trim(); one.m = f.ownerPhones[0].trim(); f._pn = one.n; f._pm = one.m; } }   /* the owner starts as the first person who gets messages */
      S.form.done[S.step] = true; S.step = ORDER[ORDER.indexOf(S.step) + 1]; return render(); }
    if ("prev" in d) { S.step = ORDER[ORDER.indexOf(S.step) - 1]; return render(); }
    if ("create" in d) { S.form.done.summary = true; return createCustomer(); }
    if ("finish" in d) return go("customer", { section: "events" });
    if ("addph" in d) { if (S.route === "new") { S.form.ownerPhones.push(""); return render(true); } const m = $("#layer .modal"); if (m && m.querySelectorAll("[data-ephone]").length < 3) { const i = document.createElement("div"); i.className = "row"; i.innerHTML = `<input class="in" data-ephone aria-label="Another owner mobile" placeholder="Another number (optional)" inputmode="tel" autocomplete="off">`; m.querySelector("#ephones").appendChild(i); i.firstChild.focus(); if (m.querySelectorAll("[data-ephone]").length >= 3) t.remove(); } return; }
    if (d.delph) { S.form.ownerPhones.splice(+d.delph, 1); return render(true); }
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
    if ("editc" in d) { const c = cust(S.cid), ph = c.ownerPhones.length ? c.ownerPhones : [""]; return modal(`${mh("Edit customer")}<label class="bold" for="ec-name">Customer name</label><input class="in" id="ec-name" value="${esc(c.name)}" autocomplete="off"><label class="bold" for="ec-own">Owner name</label><input class="in" id="ec-own" value="${esc(c.ownerName)}" autocomplete="off"><span class="bold">Owner mobile</span><div class="stack" id="ephones">${ph.map((p) => `<div class="row"><input class="in" data-ephone aria-label="Owner mobile" value="${esc(p)}" placeholder="98765 43210" inputmode="tel" autocomplete="off"></div>`).join("")}</div>${ph.length < 3 ? `<div><button class="btn sm" type="button" data-addph>${ico("plus", 15)}Add another number</button></div>` : ""}<label class="bold" for="ec-mail">Owner email</label><input class="in" id="ec-mail" type="email" value="${esc(c.ownerEmail)}" placeholder="Optional" autocomplete="off"><label class="bold" for="ec-city">City</label><input class="in" id="ec-city" value="${esc(c.place)}" autocomplete="off"><div class="row"><button class="btn pri" type="button" data-confirm="savec">Save</button><button class="btn" type="button" data-close>Cancel</button></div>`); }
    if (d.confirm === "savec") { const g = (i) => $("#" + i).value.trim(), ph = [...document.querySelectorAll("[data-ephone]")].map((x) => x.value.trim()).filter(Boolean);
      if (!g("ec-name")) return toast("Enter the customer name.", true); if (!g("ec-own")) return toast("Enter the owner's name.", true); if (!ph.length) return toast("Enter the owner's mobile number.", true); if (ph.some((p) => !PHONE_OK(p))) return toast("A mobile number does not look right. Use 10 digits, like 98765 43210.", true); if (!g("ec-city")) return toast("Enter the city.", true);
      await api.patch(`/admin/v1/tenants/${S.cid}`, { name: g("ec-name"), owner_name: g("ec-own"), owner_phones: ph, owner_email: g("ec-mail"), city: g("ec-city") }); await loadCustomers(); S.cur = null; closeModal(); toast("Saved"); return render(true); }
    if ("delc" in d) return modal(`${mh("Delete " + esc(cust(S.cid).name) + "?")}<div class="note warn">This removes the customer, its cameras and all history. It cannot be undone.</div><label class="bold" for="dn">Type the shop name to confirm</label><input class="in" id="dn" placeholder="${esc(cust(S.cid).name)}" autocomplete="off"><div class="row"><button class="btn danger" type="button" data-confirm="delete">Delete forever</button><button class="btn" type="button" data-close>Keep</button></div>`);
    if (d.confirm === "delete") { if ($("#dn").value.trim() !== cust(S.cid).name) return toast("The name does not match.", true); await api.del(`/admin/v1/tenants/${S.cid}?reason=${encodeURIComponent("Deleted from the Control Center")}`); await loadCustomers(); closeModal(); toast("Customer deleted"); return go("customers"); }
    if (d.resend) { S.logData = null; await api.post(`/admin/v1/webhooks/deliveries/${d.resend}/resend`); toast("Sent again"); return render(true); }
    if (d.editstaff) { const m = S.team.find((x) => x.id === d.editstaff), me = m.id === S.me.id; return modal(`${mh("Edit " + esc(m.name))}<label class="bold" for="es-n">Name</label><input class="in" id="es-n" value="${esc(m.name)}" autocomplete="off"><label class="bold" for="es-e">Email</label><input class="in" id="es-e" type="email" value="${esc(m.email)}" autocomplete="off"><label class="bold" for="es-r">Role</label><select class="in" id="es-r" ${me ? "disabled" : ""}>${[["owner", "Owner"], ["admin", "Admin"], ["onboarding", "Onboarding"], ["support", "Support"]].filter((r) => S.role === "owner" || r[0] !== "owner").map((r) => `<option value="${r[0]}" ${m.role === r[0] ? "selected" : ""}>${r[1]}</option>`).join("")}</select>${me ? `<p class="sub nm">You cannot change your own role. Ask another Owner.</p>` : `<p class="sub nm">A new role takes effect at their next sign-in. They are signed out now.</p>`}<div class="row"><button class="btn pri" type="button" data-confirm="savestaff" data-id="${m.id}">Save</button><button class="btn" type="button" data-close>Cancel</button><span class="sp"></span>${me ? "" : `<button class="btn danger" type="button" data-delstaff="${m.id}">Delete</button>`}</div>`); }
    if (d.confirm === "savestaff") { const m = S.team.find((x) => x.id === d.id), body = {}, n = $("#es-n").value.trim(), e = $("#es-e").value.trim(), r = $("#es-r").value; if (!n || !e) return toast("The name and the email cannot be empty.", true); if (n !== m.name) body.name = n; if (e !== m.email) body.email = e; if (r !== m.role && m.id !== S.me.id) body.role = r; if (!Object.keys(body).length) return closeModal(); await api.patch(`/admin/v1/staff/${d.id}`, body); closeModal(); toast("Saved"); return render(true); }
    if (d.delstaff) { const m = S.team.find((x) => x.id === d.delstaff); return modal(`${mh("Delete " + esc(m.name) + "?")}<div class="note warn">${esc(m.name)} (${esc(m.email)}) can no longer sign in, and this cannot be undone. If they only need a break, use Switch off instead.</div><label class="bold" for="dn">Type their name to confirm</label><input class="in" id="dn" placeholder="${esc(m.name)}" autocomplete="off"><div class="row"><button class="btn danger" type="button" data-confirm="dodelstaff" data-id="${m.id}" data-nm="${esc(m.name)}">Delete person</button><button class="btn" type="button" data-close>Keep</button></div>`); }
    if (d.confirm === "dodelstaff") { if ($("#dn").value.trim() !== d.nm) return toast("The name does not match.", true); await api.del(`/admin/v1/staff/${d.id}?reason=${encodeURIComponent("Deleted from the Control Center")}`); closeModal(); toast("Person deleted"); return render(true); }
    if ("invite" in d) return modal(`${mh("Add a person")}<label class="bold" for="in1">Name</label><input class="in" id="in1" autocomplete="off"><label class="bold" for="in2">Email</label><input class="in" id="in2" type="email" autocomplete="off"><label class="bold" for="in3">Role</label><select class="in" id="in3">${S.role === "owner" ? '<option value="owner">Owner</option>' : ""}<option value="admin">Admin</option><option value="onboarding">Onboarding</option><option value="support">Support</option></select><button class="btn pri wide" type="button" data-confirm="invite">Create sign-in link</button>`);
    if (d.confirm === "invite") { const r = await api.post("/admin/v1/staff", { name: $("#in1").value.trim(), email: $("#in2").value.trim(), role: $("#in3").value }); modal(`${mh("Sign-in link")}<p class="sub nm">Send this link to ${esc(r.email)}. It works once and lets them choose a password.</p><input class="in full" readonly value="${esc(r.link)}" id="lk"><button class="btn pri wide" type="button" data-close>Done</button>`); $("#lk").select(); return; }
    if (d.disable || d.enable) { await api.post(`/admin/v1/staff/${d.disable || d.enable}/${d.disable ? "disable" : "enable"}?reason=${encodeURIComponent("Changed from the Control Center")}`); toast("Done"); return render(true); }
    if (d.resetlink) { const r = await api.post(`/admin/v1/staff/${d.resetlink}/reset-link?reason=${encodeURIComponent("New link from the Control Center")}`); modal(`${mh("Sign-in link")}<p class="sub nm">This link works once.</p><input class="in full" readonly value="${esc(r.link)}" id="lk"><button class="btn pri wide" type="button" data-close>Done</button>`); $("#lk").select(); return; }
    if ("watab" in d) { S.waTab = d.watab; return render(true); }
    if ("alsave" in d) {
      const url = document.querySelector("[data-al=url]").value.trim(), key = document.querySelector("[data-alkey]").value.trim(), same = $("#a3").checked, c = S.data.alc || {}, body = {};
      if (url && url !== (c.url || "")) body.url = url;
      if (same) body.use_summary_key = true; else if (key) body.key = key;
      if (!Object.keys(body).length) return toast("Nothing has changed.");
      await api.put("/admin/v1/webhooks/connection", body); toast("Saved"); return render(true);
    }
    if ("alapply" in d) return modal(`${mh("Use for all alerts?")}<p class="nm">Every alert will send to the shared address with the shared key. Their own addresses and keys stop being used. You can switch one alert back in its own window.</p><div class="actions"><button class="btn" type="button" data-close>Cancel</button><button class="btn pri" type="button" data-confirm="alapply">Use for all alerts</button></div>`);
    if (d.confirm === "alapply") { const r = await api.post("/admin/v1/webhooks/connection/apply", {}); closeModal(); toast(`${r.switched} alerts now use it`); return render(true); }
    if ("altest" in d) {
      const wh = await api.get("/admin/v1/webhooks"); let last = ""; try { last = localStorage.getItem("gi_test_to") || ""; } catch (e) { /* no storage */ }
      return modal(`${mh("Send a test alert")}<p class="sub nm">Sends one test alert with sample values to the number below, the way a real alert would go.</p>
      <label class="bold" for="at-a">Which alert?</label><select class="in" id="at-a">${wh.items.map((x) => `<option value="${esc(x.alert_type)}" ${x.alert_type === "camera_offline" ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select>
      <label class="bold" for="at-n">Send to (mobile number)</label><input class="in" id="at-n" inputmode="tel" placeholder="+91 98765 43210" value="${esc(last)}">
      <div class="actions"><button class="btn" type="button" data-close>Cancel</button><button class="btn pri" type="button" data-confirm="doaltest">Send test</button></div>`);
    }
    if (d.confirm === "dodtest") {
      const to = $("#dt-n").value.trim();
      if (!/^\+?[0-9 ]{8,16}$/.test(to)) return toast("Enter a mobile number with 8 to 16 digits, for example +91 98765 43210.", true);
      try { localStorage.setItem("gi_test_to", to); } catch (e) { /* no storage */ }
      const r = await api.post(`/admin/v1/webhooks/${S.testEp}/send-now`, { to_phone: to }); closeModal();
      return r.ok ? toast("Sent. The provider accepted it.") : toast("The provider did not accept it: " + (r.error || "status " + r.status), true);
    }
    if (d.confirm === "doaltest") {
      const to = $("#at-n").value.trim(), alert_type = $("#at-a").value;
      if (!/^\+?[0-9 ]{8,16}$/.test(to)) return toast("Enter a mobile number with 8 to 16 digits, for example +91 98765 43210.", true);
      try { localStorage.setItem("gi_test_to", to); } catch (e) { /* no storage */ }
      const r = await api.post("/admin/v1/webhooks/connection/test", { alert_type, to_phone: to }); closeModal();
      return r.ok ? toast("Sent. The provider accepted it.") : toast("The provider did not accept it: " + (r.error || "status " + r.status), true);
    }
    if (d.evgrp) { S.evOpen = S.evOpen || { smart: true, system: true }; S.evOpen[d.evgrp] = !S.evOpen[d.evgrp]; return render(true); }
    if (d.hrec !== undefined) return openRecorder(+d.hrec);
    if (d.rencam) { const c = S.cur.cams.find((x) => x.id === d.rencam); return modal(`${mh("Rename the camera")}<label class="bold" for="rn-n">Camera name</label><input class="in" id="rn-n" maxlength="80" value="${esc(c.name)}"><div class="actions"><button class="btn" type="button" data-close>Cancel</button><button class="btn pri" type="button" data-confirm="dorencam" data-id="${c.id}">Save</button></div>`); }
    if (d.confirm === "dorencam") { const name = $("#rn-n").value.trim(); if (!name) return toast("Type a name for the camera.", true); await api.patch(`/admin/v1/tenants/${S.cid}/shop/cameras/${d.id}`, { name }); closeModal(); await loadCurrent(S.cid); toast("Camera renamed"); return render(true); }
    if (d.stat) { const [to, v] = d.stat.split("|"); if (to === "customers") { S.filter = v; await loadCustomers(); return go("customers"); } S.logChip = v; S.logRange = "24h"; S.logData = null; return go("messages"); }
    if ("sysrefresh" in d) return render(true);
    if ("testwa" in d) {
      const r = await api.get("/admin/v1/tenants?limit=200"); let last = ""; try { last = localStorage.getItem("gi_test_to") || ""; } catch (e) { /* no storage */ }
      return modal(`${mh("Send a test message")}<p class="sub nm">Sends one real summary, starting with the word TEST, to the number below. The customer is not told.</p>
      <label class="bold" for="tw-c">Which customer's summary?</label><select class="in" id="tw-c">${(r.items || []).map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("")}</select>
      <label class="bold" for="tw-n">Send to (mobile number)</label><input class="in" id="tw-n" inputmode="tel" placeholder="+91 98765 43210" value="${esc(last)}">
      <div class="actions"><button class="btn" type="button" data-close>Cancel</button><button class="btn pri" type="button" data-confirm="dotestwa">Send test</button></div>`);
    }
    if (d.confirm === "dotestwa") {
      const to = $("#tw-n").value.trim(), tenant_id = $("#tw-c").value;
      if (!tenant_id) return toast("Choose a customer first.", true);
      if (!/^\+?[0-9 ]{10,16}$/.test(to)) return toast("Enter a mobile number with 10 to 16 digits, for example +91 98765 43210.", true);
      try { localStorage.setItem("gi_test_to", to); } catch (e) { /* no storage */ }
      const r = await api.post("/admin/v1/summaries/test", { tenant_id, kind: "daily", to });
      closeModal();
      return r.ok ? toast("Sent. The provider accepted it" + (r.message_id ? " (id " + r.message_id + ")" : "") + ".") : toast("The provider did not accept it: " + (r.error || "status " + r.status), true);
    }
    if ("keepsave" in d) { await api.put("/admin/v1/maintenance/keep", { days: +S.keepDays }); S.keepDays = null; toast("Saved. Everything older is removed at the next clean-up."); return render(true); }
    if ("keepclear" in d) { const k = await api.get("/admin/v1/maintenance/keep"), p = await api.get(`/admin/v1/maintenance/keep/preview?days=${k.days}`); return modal(`${mh("Clear now?")}<p class="nm">Everything older than ${k.days} days is removed for every customer. This cannot be undone.</p><div class="creds">${[["Alerts", p.alerts], ["Messages", p.messages], ["People counts", p.people_counts], ["Plate reads", p.plate_reads], ["Pictures and clips", p.pictures_and_clips]].map(([l, v]) => `<div class="kv"><span>${l}</span><span class="num">${(v ?? 0).toLocaleString("en-IN")}</span></div>`).join("")}</div><div class="row"><button class="btn danger" type="button" data-confirm="keepclear">Clear now</button><button class="btn" type="button" data-close>Cancel</button></div>`); }
    if (d.confirm === "keepclear") { const r = await api.post("/admin/v1/maintenance/retention/run"); closeModal(); toast("Done"); return render(true); }
    if ("wasave" in d) { const url = document.querySelector("[data-wa=url]").value.trim(), key = document.querySelector("[data-wakey]").value.trim(); await api.put("/admin/v1/summaries", { config: { url } }); if (key) await api.put("/admin/v1/summaries/key", { key }); toast("Saved"); return render(true); }
    /* recorder and cameras */
    if ("addrec" in d) return modal(`${mh("Add a recorder")}<label class="bold" for="rn">Recorder name</label><input class="in" id="rn" placeholder="For example, Main shop" autocomplete="off"><p class="sub nm">Dahua recorders connect to our server by themselves. You get the details to type into the recorder next.</p><button class="btn pri wide" type="button" data-confirm="addrec">Add recorder</button>`);
    if (d.confirm === "addrec") { const r = await api.post(`/admin/v1/tenants/${S.cid}/recorders`, { brand: "dahua", name: $("#rn").value.trim() || null, reason: "Added from the Control Center" }); S.cur = null; modal(`${mh("Type these into the recorder")}<div class="creds">${credRows(r)}</div><p class="sub nm">This is the only time the key is shown.</p><button class="btn pri wide" type="button" data-close>Done</button>`); return render(true); }
    if (d.campic) { const c = S.cur.cams.find((x) => x.id === d.campic); modal(`${mh(esc(c.name))}<div class="vframe"><div class="vhead">${ico("camera2", 22)}<span>${esc(c.name)} · ${esc(cname())}</span><span class="lk" id="lv-state">Picture</span><span class="sp"></span>${can("live") ? `<button class="btn sm" type="button" id="lv-btn" data-live="${c.id}">Watch live</button>` : ""}</div><div class="vpic big" data-campic-box="${c.id}"><span class="picmsg">Loading picture…</span></div></div><button class="btn wide" type="button" data-close>Close</button>`); if (can("live")) return startLive(c.id); return loadPics(); }
    if (d.live) { if (S.live) return stopLive(true); return startLive(d.live); }
    if (d.veh !== undefined) return openVehicle(+d.veh);
    if ("vmore" in d) { await loadVehicles(true); S.veh.hold = true; return render(true); }
    if (d.export) {
      const c = d.export === "count", V = S.veh, [from, to] = pvDates(c ? S.cnt.range : V.range), p = new URLSearchParams({ from, to });
      if (!c) { if (V.q.trim()) p.set("q", V.q.trim()); if (V.dir !== "all") p.set("direction", V.dir); if (V.st !== "all") p.set("status", V.st); }
      await downloadFile(`/admin/v1/tenants/${S.cid}/shop/${c ? "people-count" : "vehicles"}/export?${p}`, `${c ? "people_count" : "vehicles"}_${from}${from !== to ? "_to_" + to : ""}.xlsx`); return toast("Excel file ready");
    }
    if (d.showconn) { const r = await api.get(`/admin/v1/recorders/${d.showconn}/connection`); return modal(`${mh("Recorder details")}<div class="creds">${credRows(r)}</div><button class="btn pri wide" type="button" data-close>Done</button>`); }
    if (d.editrec) { const r = S.cur.recs.find((x) => x.id === d.editrec); return modal(`${mh("Edit recorder")}<label class="bold" for="rn">Name</label><input class="in" id="rn" value="${esc(r.name || "")}" placeholder="${esc(r.device_id)}" autocomplete="off"><label class="bold" for="rid">Recorder ID</label><input class="in" id="rid" value="${esc(r.device_id)}" autocomplete="off"><p class="sub nm">The ID is what the recorder calls itself when it connects. If you change it, type the new ID into the recorder as well. Until then it shows as not connected.</p><div class="row"><button class="btn pri" type="button" data-confirm="saverec" data-id="${r.id}">Save</button><button class="btn" type="button" data-newpw="${r.id}">New password</button><span class="sp"></span><button class="btn danger" type="button" data-delrec="${r.id}">Delete</button></div>`); }
    if (d.confirm === "saverec") { const r = S.cur.recs.find((x) => x.id === d.id), name = $("#rn").value.trim(), rid = $("#rid").value.trim(); if (!rid) return toast("The recorder needs an ID.", true); const body = {}; if (name !== (r.name || "")) body.name = name; if (rid !== r.device_id) body.device_id = rid; if (!Object.keys(body).length) return closeModal(); const out = await api.patch(`/admin/v1/recorders/${d.id}`, body); await loadCurrent(S.cid); closeModal(); if (out.id_changed) modal(`${mh("Type the new ID into the recorder")}<div class="creds"><div class="kv"><span>Recorder ID</span><span class="sc">${esc(out.device_id)}</span></div></div><p class="sub nm">The password stays the same. The recorder connects again once it has the new ID.</p><button class="btn pri wide" type="button" data-close>Done</button>`); else toast("Saved"); return render(true); }
    if (d.newpw) { const r = S.cur.recs.find((x) => x.id === d.newpw); return modal(`${mh("New password?")}<div class="note warn">${esc(r.name || r.device_id)} stops connecting with its old password straight away. You get the new one once, and type it into the recorder.</div><div class="row"><button class="btn pri" type="button" data-confirm="dopw" data-id="${r.id}">Make a new password</button><button class="btn" type="button" data-close>Cancel</button></div>`); }
    if (d.confirm === "dopw") { const r = await api.post(`/admin/v1/recorders/${d.id}/new-password`); await loadCurrent(S.cid); modal(`${mh("Type these into the recorder")}<div class="creds">${credRows(r)}</div><p class="sub nm">This is the only time the password is shown.</p><button class="btn pri wide" type="button" data-close>Done</button>`); return render(true); }
    if (d.delrec) { const p = await api.get(`/admin/v1/recorders/${d.delrec}/delete-preview`); return modal(`${mh("Delete " + esc(p.name) + "?")}<div class="note warn">This removes the recorder and its ${p.cameras} camera${p.cameras === 1 ? "" : "s"}${p.events.length ? `, and ${p.events.length} event${p.events.length === 1 ? "" : "s"} that would have nothing left to watch (${p.events.map((e) => esc(e.title)).join(", ")})` : ""}. Past alerts and messages stay until they are cleared automatically. The recorder stops connecting. This cannot be undone.</div><label class="bold" for="dn">Type the recorder name to confirm</label><input class="in" id="dn" placeholder="${esc(p.name)}" autocomplete="off"><div class="row"><button class="btn danger" type="button" data-confirm="dodelrec" data-id="${d.delrec}" data-nm="${esc(p.name)}">Delete recorder</button><button class="btn" type="button" data-close>Keep</button></div>`); }
    if (d.confirm === "dodelrec") { if ($("#dn").value.trim() !== d.nm) return toast("The name does not match.", true); await api.del(`/admin/v1/recorders/${d.id}`); await loadCurrent(S.cid); closeModal(); toast("Recorder deleted"); return render(true); }
    if (d.findcams) { const list = await api.get(`/admin/v1/tenants/${S.cid}/shop/recorders/${d.findcams}/channels`); S.cur.channels = { [d.findcams]: list.map((c) => ({ ...c, on: !c.enabled_as_camera, name: c.name || "Camera " + c.channel_no })) }; return render(true); }
    if (d.chall) { const [rid, on] = d.chall.split("|"); S.cur.channels[rid].forEach((c) => { if (!c.enabled_as_camera) c.on = on === "1"; }); return render(true); }
    if (d.addcams) { const rid = d.addcams; let n = 0; for (const ch of S.cur.channels[rid]) { if (ch.enabled_as_camera || !ch.on) continue; await api.post(`/admin/v1/tenants/${S.cid}/shop/cameras`, { recorder_id: rid, channel_no: ch.channel_no, name: ch.name.trim() || "Camera " + ch.channel_no, use_footfall: true }); n++; } delete S.cur.channels[rid]; const keep = S.cur.channels; await loadCurrent(S.cid); S.cur.channels = keep; await loadCustomers(); toast(n ? `${n} camera${n === 1 ? "" : "s"} added` : "Nothing was ticked"); return render(true); }
    /* event wizard */
    if ("addev" in d) { await ensureGeo(); S.ev = newEv(); S.estep = "event"; return go("event"); }
    if (d.estep) { S.estep = d.estep; if (d.estep === "area" && S.ev.target) return wizardPicture(); return render(); }
    if (d.erec) { S.ev.rec = { id: d.erec }; S.ev.target = null; S.ev.snap = null; return render(true); }
    if (d.ecam && t.classList.contains("offl")) return toast("This camera cannot send a picture right now, so it cannot be marked. Choose it when it is back online.", true);
    if (d.ecam) { const c = S.cur.cams.find((q) => q.id === d.ecam); S.ev.target = { kind: "cam", id: c.id, name: c.name }; S.ev.snap = null; S.ev.snapErr = null; S.ev.zones = null; S.ev.pts = []; S.ev.touched = false; S.ev.whole = false; S.ev.flip = false; S.ev.pdir = undefined; return render(true); }
    if (d.etype) { const x = evMeta(d.etype); S.ev.key = d.etype; S.ev.v = {}; if (!needsCam(d.etype)) S.ev.target = null; if (S.cur.recs.length === 1) S.ev.rec = { id: S.cur.recs[0].id }; if (!S.ev.name || S.ev.nameAuto) { S.ev.name = x.title; S.ev.nameAuto = true; } return render(true); }
    if (d.evrow !== undefined) { openEditEvent(+d.evrow); return; }
    if (d.evgrp) { S.evOpen[d.evgrp] = !S.evOpen[d.evgrp]; return render(true); }
    if (d.editmark !== undefined) return openMark(+d.editmark);
    if (d.confirm === "rowoff") { const r = S.cur.rows[S.pendingOff]; await api.patch(`/admin/v1/tenants/${S.cid}/shop/cameras/${r.cam.id}`, { [r.flag]: false, enabled: false }); closeModal(); await loadCurrent(S.cid); toast("Switched off"); return render(true); }
    if (d.confirm === "rowkeep") { closeModal(); return render(true); }
    if ("mflip" in d) { S.mark.flip = !S.mark.flip; S.mark.touched = true; return drawMark(); }
    if ("mwhole" in d) { S.mark.pts = []; S.mark.touched = true; return drawMark(); }
    if ("mclear" in d) { S.mark.pts = []; S.mark.touched = true; return drawMark(); }
    if ("msave" in d) { const M = S.mark, r = S.cur.rows[M.i]; await saveGeometry(r.cam.id, M.mode, M.pts, M.flip, true, M.pdir); closeModal(); await loadCurrent(S.cid); toast("Saved"); return render(true); }
    if (d.delev) { confirmDelete(+d.delev); return; }
    if (d.confirm === "delev") return doDelete(+d.i);
    if ("esave" in d) return saveEdit();
    if ("redraw" in d) return openRedraw();
    if ("redrawclear" in d) { S.redraw.pts = []; S.redraw.fresh = true; return drawRedraw(); }
    if ("redrawcancel" in d) { drawEdit(); return; }
    if ("redrawsave" in d) { S.edit.pts = S.redraw.pts; drawEdit(); return; }
    if ("clearpts" in d) { S.ev.pts = []; S.ev.touched = true; S.ev.whole = false; return render(true); }
    if ("snaptry" in d) { S.ev.snap = null; S.ev.snapErr = null; return wizardPicture(); }
    if ("retry2" in d) { const f = S.retry; closeModal(); return f && f(); }
    if (d.pdir) { S.ev.pdir = d.pdir; S.ev.touched = true; return render(true); }
    if (d.mpdir) { S.mark.pdir = d.mpdir; S.mark.touched = true; return drawMark(); }
    if ("goplates" in d) { S.ev = newEv(); S.ev.key = "__plates"; S.ev.name = "Read number plates"; S.estep = eorder()[2]; return render(true); }
    if ("flip" in d) { S.ev.flip = !S.ev.flip; S.ev.touched = true; return render(true); }
    if ("whole" in d) { S.ev.pts = []; S.ev.whole = true; S.ev.touched = true; return render(true); }
    if ("evnext" in d) { S.ev.done[S.estep] = true; const o = eorder(); S.estep = o[o.indexOf(S.estep) + 1]; if (S.estep === "area") return wizardPicture(); return render(); }
    if ("evprev" in d) { const o = eorder(); S.estep = o[o.indexOf(S.estep) - 1]; return render(); }
    if ("evcreate" in d) return createEvent();
    if ("evdone" in d) return go("customer", { section: "events" });
    if ("setupall" in d) { const sh = S.data.wh.connection || {}; return modal(`${mh("Set up all alerts")}<p class="sub nm">${sh.url ? "Every alert that has none yet is set up to use the shared WhatsApp connection. Each gets its standard message, which you can change one by one." : "Set the shared address first, under Setup, WhatsApp connection, Alerts. Every alert then uses it."}</p><div class="actions"><button class="btn" type="button" data-close>Cancel</button><button class="btn pri" type="button" data-confirm="setupall" ${sh.url ? "" : "disabled"}>Set up</button></div>`); }
    if (d.confirm === "setupall") { const r = await api.post("/admin/v1/webhooks/standard", {}); closeModal(); toast(`${(r.made || []).length} alerts set up`); return render(true); }
  } catch (e) { toast(errText(e), true); }
});
document.addEventListener("click", async (ev) => {
  const g = (s) => ev.target.closest(s);
  if (g("#dclose") || g("#dclose2")) return closeModal();
  const sc = g("[data-sc]"); if (sc) { const ta = $("#d-tpl"); const p = ta.selectionStart; ta.setRangeText(`{${sc.dataset.sc}}`, p, ta.selectionEnd, "end"); ta.focus(); return; }
  try {
    if (g("#dsave")) { const id = g("#dsave").dataset.wid; await api.put(`/admin/v1/webhooks/${id}`, { name: $("#d-name").value.trim(), ...($("#d-url") ? { url: $("#d-url").value.trim() } : {}), payload_template: $("#d-tpl").value, active: $("#d-on").checked }); closeModal(); toast("Saved"); return render(true); }
    if (g("#down") || g("#dshared")) { const id = (g("#down") || g("#dshared")).dataset.wid; await api.put(`/admin/v1/webhooks/${id}`, { use_shared: !!g("#dshared") }); closeModal(); toast(g("#dshared") ? "Now uses the shared connection" : "Now uses its own address and key"); return render(true); }
    if (g("#dkey") || g("#dkeyconn")) {
      const conn = !!g("#dkeyconn"), id = (g("#dkey") || g("#dkeyconn")).dataset.wid, key = $("#d-key").value.trim();
      if (!conn && key.length < 8) return toast("Paste the key from your WhatsApp provider first.", true);
      const r = await api.post("/admin/v1/webhooks/key", conn ? { endpoint_id: id, use_connection: true, all_on_address: $("#d-keyall").checked } : { endpoint_id: id, key, all_on_address: $("#d-keyall").checked });
      closeModal(); toast(r.updated === 1 ? "Key saved" : `Key saved on ${r.updated} alerts`); return render(true);
    }
    if (g("#dtest")) {
      let last = ""; try { last = localStorage.getItem("gi_test_to") || ""; } catch (e) { /* no storage */ }
      S.testEp = g("#dtest").dataset.wid;
      return modal(`${mh("Send a test")}<p class="sub nm">Sends this alert with sample values, and a sample picture and video, to the number below. Nobody else receives it.</p>
      <label class="bold" for="dt-n">Send to (mobile number)</label><input class="in" id="dt-n" inputmode="tel" placeholder="+91 98765 43210" value="${esc(last)}">
      <div class="actions"><button class="btn" type="button" data-close>Cancel</button><button class="btn pri" type="button" data-confirm="dodtest">Send test</button></div>`);
    }
    if (g("#dnew")) { const type = g("#dnew").dataset.type, a = S.data.cat.find((x) => x.type === type); await api.post("/admin/v1/webhooks", { name: a.title, alert_type: type, url: $("#d-url").value.trim() }); closeModal(); toast("Set up"); return render(true); }
  } catch (e) { toast(errText(e), true); }
});
document.addEventListener("input", (ev) => {
  const t = ev.target;
  if (t.id === "q") { S.q = t.value; const p = t.selectionStart; render(true).then(() => { const n = $("#q"); if (n) { n.focus(); n.setSelectionRange(p, p); } }); return; }
  if (t.id === "vq") { S.veh.q = t.value; const p = t.selectionStart; clearTimeout(S.veh.t); S.veh.t = setTimeout(() => render(true).then(() => { const nn = $("#vq"); if (nn) { nn.focus(); nn.setSelectionRange(p, p); } }), 350); return; }
  if (t.id === "evq") { S.evQ = t.value; const p = t.selectionStart; render(true).then(() => { const n = $("#evq"); if (n) { n.focus(); n.setSelectionRange(p, p); } }); return; }
  if (t.id === "lq") { S.logQ = t.value; clearTimeout(S.lqTimer); S.lqTimer = setTimeout(() => { const p = t.selectionStart; render(true).then(() => { const n = $("#lq"); if (n) { n.focus(); n.setSelectionRange(p, p); } }); }, 350); return; }
  if (t.id === "tq") { S.tq = t.value; const p = t.selectionStart; render(true).then(() => { const n = $("#tq"); if (n) { n.focus(); n.setSelectionRange(p, p); } }); return; }
  if (S.edit && (t.dataset.xv || t.dataset.xto || t.id === "x-name" || t.dataset.act === "xclip" || t.dataset.act === "xon" || t.dataset.ev)) { const E = S.edit, d = t.dataset;
    if (d.xv) { E.v[d.xv] = t.value; if (["when", "target"].includes(d.xv)) drawEdit(); } else if (d.ev) { E.v[d.ev] = t.value; if (["when", "target"].includes(d.ev)) drawEdit(); }
    else if (d.xto) { const i = E.to.indexOf(d.xto); if (t.checked && i < 0) E.to.push(d.xto); if (!t.checked && i >= 0) E.to.splice(i, 1); }
    else if (t.id === "x-name") E.name = t.value; else if (d.act === "xclip") E.clip = t.checked; else if (d.act === "xon") E.on = t.checked; return; }
  if (S.route === "event" && S.ev) { const v = S.ev, d = t.dataset;
    if (d.ebind) { v[d.ebind] = t.value; if (d.ebind === "name") v.nameAuto = false; } else if (d.eto) { const i = v.to.indexOf(d.eto); if (t.checked && i < 0) v.to.push(d.eto); if (!t.checked && i >= 0) v.to.splice(i, 1); } else if (d.ev) { v.v[d.ev] = t.value; if (d.ev === "when") render(true); } else if ("enotice" in d) v.notice = t.checked;
    else if (d.act === "eon") v.on = t.checked; else if (d.act === "eclip") v.clip = t.checked; return; }
  const f = S.route === "new" ? S.form : S.route === "customer" ? S.cur?.f : null; if (!f) return; const d = t.dataset;
  t.classList.remove("bad");
  if (d.bind) f[d.bind] = t.value;
  else if (d.oph !== undefined) f.ownerPhones[+d.oph] = t.value;
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
    if (d.act?.startsWith("evsw:") && S.cur) { return toggleRow(+d.act.slice(5), t.checked); }
    if (d.act?.startsWith("camfoot:") || d.act?.startsWith("camplate:")) { const plate = d.act.startsWith("camplate:"), id = d.act.split(":")[1], c = S.cur.cams.find((x) => x.id === id);
      const body = plate ? { use_anpr: t.checked, ...(t.checked ? { stream: "main" } : {}) } : { use_footfall: t.checked };
      await api.patch(`/admin/v1/tenants/${S.cid}/shop/cameras/${id}`, body); toast("Saved"); const keep = S.cur.channels; await loadCurrent(S.cid); S.cur.channels = keep; return render(true); }
    if (d.act?.startsWith("camon:")) { await api.patch(`/admin/v1/tenants/${S.cid}/shop/cameras/${d.act.slice(6)}`, { enabled: t.checked }); toast("Saved"); return loadCurrent(S.cid).then(() => render(true)); }
    if (t.id === "cnt-range") { S.cnt.range = t.value; return render(true); }
    if (t.id === "cnt-cam") { S.cnt.cam = t.value; return render(true); }
    if (t.id === "veh-range") { S.veh.range = t.value; return render(true); }
    if (t.id === "veh-dir") { S.veh.dir = t.value; return render(true); }
    if (t.id === "veh-st") { S.veh.st = t.value; return render(true); }
    if ("lrangesel" in d) { S.logRange = t.value; return render(true); }
    if ("lchipsel" in d) { S.logChip = t.value; return render(true); }
    if ("keepdays" in d) { S.keepDays = +t.value; return render(true); }
    if (t.id === "lfrom" || t.id === "lto") { if (t.id === "lfrom") S.logFrom = t.value; else S.logTo = t.value; return render(true); }
    if (d.camName) { await api.patch(`/admin/v1/tenants/${S.cid}/shop/cameras/${d.camName}`, { name: t.value.trim() }); toast("Saved"); }
  } catch (e) { toast(e.code === "use_required" ? "A camera needs at least one use. Turn Watching off to stop it instead." : errText(e), true); render(true); }
});
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeModal(); });

/* ------------------------------------------------------------------------------------------------ start */
async function boot() {
  if (!api.signedIn()) return location.replace("/");
  try { S.me = await api.get("/admin/v1/me"); } catch (e) { if (e.status === 401) return location.replace("/"); return void ($("#page").innerHTML = `<div class="empty">${esc(errText(e))}<br><button class="btn" type="button" data-retry>Try again</button></div>`); }
  S.role = S.me.role;
  loadStatus();
  try { await loadCustomers(); } catch (e) { toast(errText(e), true); }
  const [r, a, b] = location.hash.slice(1).split("/");
  if (["overview", "customers", "messages", "more"].includes(r)) S.route = r;
  else if (r === "setup") { S.route = "setup"; if (SETUPS.includes(a)) S.setup = a; }
  else if (r === "customer" && cust(a)) { S.route = "customer"; S.cid = a; S.cur = null; S.section = SECTIONS.includes(b) ? b : "cams"; }
  render();
}
boot();

/* A vehicle picture that was removed (or never kept) becomes the plain car mark instead of a broken picture */
document.addEventListener("error", (e) => { const t = e.target; if (t.tagName === "IMG" && t.classList.contains("vimg")) { const d = document.createElement("div"); d.className = (t.classList.contains("vbig") ? "vbig" : "vthumb") + " vnone"; d.innerHTML = ico("car", t.classList.contains("vbig") ? 40 : 22); t.replaceWith(d); } }, true);

/* ------------------------------------------------------------------------------------------------ My profile: a compact panel that drops down from the avatar */
const PW_HINT = "At least 10 characters. A long phrase is best.";
function pwStrength(p, email) {                                  // the same plain rules as the server: 10 or more characters, nothing common or easy
  if (!p) return [0, PW_HINT];
  if (p.length < 10) return [1, `Use at least 10 characters (${p.length} so far).`];
  const low = p.toLowerCase();
  if (["password123", "1234567890", "qwertyuiop"].includes(low) || (email && (low === email.toLowerCase() || low === email.toLowerCase().split("@")[0])) || new Set(low).size < 4) return [1, "Too easy to guess. Choose a longer one that is not a common word."];
  const sc = 2 + (p.length >= 14 ? 1 : 0) + (/\d/.test(p) && /[a-z]/i.test(p) ? 1 : 0);
  return [Math.min(4, sc), sc >= 4 ? "Strong." : sc === 3 ? "Good." : "Okay. A longer one is stronger."];
}
const initials = (n) => (n || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
function renderProfile() {
  const m = S.prof, ch = `<svg class="ch" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>`;
  $("#pop").innerHTML = `<div class="pf-hd"><span class="av lg" id="pf-av">${esc(initials(m.name))}</span><div class="pf-t">
    <div class="pf-nm" id="pf-view"><b id="pf-name">${esc(m.name)}</b><button class="ibtn sm" id="pf-edit" type="button" aria-label="Edit name">${ico("edit", 15)}</button></div>
    <div class="pf-ed" id="pf-editor" hidden><input class="in" id="pf-nin" aria-label="Your name" autocomplete="off"><button class="btn pri sm" id="pf-nsave" type="button">Save</button><button class="btn sm" id="pf-ncancel" type="button">Cancel</button></div>
    <div class="pf-em">${esc(m.email)}</div><div class="pf-meta"><span class="tag brand">${esc(ROLES[m.role]?.[0] || humanize(m.role))}</span><span>Last sign-in ${m.last_login_at ? fmtTime(m.last_login_at).toLowerCase() : "never"}</span></div></div></div>
  <div class="pf-list">
   <button class="pf-ri" id="pf-pwb" type="button" aria-expanded="false" aria-controls="pf-pwbox">${ico("lock", 18)}Change password${ch}</button>
   <div class="pf-box" id="pf-pwbox" hidden>
    <label for="pf-cur">Current password</label><input class="in" id="pf-cur" type="password" autocomplete="current-password">
    <label for="pf-p1">New password</label><input class="in" id="pf-p1" type="password" autocomplete="new-password">
    <div class="pf-meter" id="pf-meter" data-s="0" aria-hidden="true"><i></i><i></i><i></i><i></i></div><div class="pf-tip" id="pf-mtext">${PW_HINT}</div>
    <label for="pf-p2">New password again</label><input class="in" id="pf-p2" type="password" autocomplete="new-password">
    <div class="pf-err" id="pf-err" role="alert"></div>
    <div class="row"><button class="btn pri sm" id="pf-chg" type="button">Change password</button><button class="btn sm" id="pf-pcancel" type="button">Cancel</button></div>
    <div class="pf-tip">Your other devices are signed out. This one stays signed in.</div></div>
   <button class="pf-ri" id="pf-othb" type="button" aria-expanded="false" aria-controls="pf-othbox">${ico("cam", 18)}Sign out everywhere else${ch}</button>
   <div class="pf-box" id="pf-othbox" hidden><div class="pf-ask" id="pf-oask">${m.devices > 1 ? `You are signed in on ${m.devices} devices. End the other ${m.devices - 1}?` : "You are signed in on 1 device (this one). Nothing else to end."}</div>
    <div class="row">${m.devices > 1 ? `<button class="btn danger sm" id="pf-oyes" type="button">Yes, sign them out</button>` : ""}<button class="btn sm" id="pf-ono" type="button">${m.devices > 1 ? "Keep them" : "Close"}</button></div></div>
  </div>
  <div class="pf-ft"><button class="pf-ri out" id="signout" type="button">${ico("out", 18)}Sign out</button></div>`;
}
const pfBoxes = [["pf-pwb", "pf-pwbox"], ["pf-othb", "pf-othbox"]];
const pfClose = () => pfBoxes.forEach(([b, x]) => { $("#" + b)?.setAttribute("aria-expanded", "false"); const e = $("#" + x); if (e) e.hidden = true; });
async function openProfile() {
  try { S.prof = await api.get("/admin/v1/me"); } catch (e) { return toast(errText(e), true); }
  renderProfile(); $("#pop").hidden = false; $("#meb").setAttribute("aria-expanded", "true");
}
function closeProfile() { const p = $("#pop"); if (!p || p.hidden) return; p.hidden = true; p.innerHTML = ""; $("#meb").setAttribute("aria-expanded", "false"); }
async function saveMyName() {
  const v = $("#pf-nin").value.trim(); if (!v) { $("#pf-nin").classList.add("bad"); return toast("Enter your name.", true); }
  try { await api.patch("/admin/v1/me", { name: v }); } catch (e) { return toast(errText(e), true); }
  S.me.name = v; S.prof.name = v; shell(); $("#pf-name").textContent = v; $("#pf-av").textContent = initials(v); $("#pf-editor").hidden = true; $("#pf-view").hidden = false; toast("Name saved");
}
async function changeMyPassword() {
  const cur = $("#pf-cur").value, a = $("#pf-p1").value, b = $("#pf-p2").value, bad = (id, msg) => { $("#" + id).classList.add("bad"); $("#pf-err").textContent = msg; $("#" + id).focus(); };
  if (!cur) return bad("pf-cur", "Enter your current password.");
  const [sc, t] = pwStrength(a, S.prof.email); if (sc <= 1) return bad("pf-p1", t);
  if (a === cur) return bad("pf-p1", "Choose a password you have not used before.");
  if (a !== b) return bad("pf-p2", "The two new passwords are not the same.");
  try { await api.post("/admin/v1/me/change-password", { current_password: cur, new_password: a, refresh_token: api.refreshToken }); }
  catch (e) { return bad(e.status === 401 ? "pf-cur" : "pf-p1", e.status === 401 ? "Your current password is not right." : errText(e)); }
  closeProfile(); toast("Password changed. Your other devices were signed out.");
}
document.addEventListener("click", async (ev) => {
  const t = ev.target;
  if (t.closest("#meb")) { return $("#pop").hidden ? openProfile() : closeProfile(); }
  if (!t.closest("#pop")) return closeProfile();
  const id = t.closest("button")?.id; if (!id) return;
  if (id === "pf-edit") { $("#pf-nin").value = S.prof.name; $("#pf-view").hidden = true; $("#pf-editor").hidden = false; $("#pf-nin").focus(); $("#pf-nin").select(); }
  else if (id === "pf-ncancel") { $("#pf-editor").hidden = true; $("#pf-view").hidden = false; }
  else if (id === "pf-nsave") await saveMyName();
  else if (pfBoxes.some(([b]) => b === id)) { const x = $("#" + pfBoxes.find(([b]) => b === id)[1]), open = x.hidden; pfClose(); if (open) { $("#" + id).setAttribute("aria-expanded", "true"); x.hidden = false; x.querySelector("input,button")?.focus(); } }
  else if (id === "pf-pcancel" || id === "pf-ono") pfClose();
  else if (id === "pf-chg") await changeMyPassword();
  else if (id === "pf-oyes") { try { const r = await api.post("/admin/v1/me/sign-out-others", { refresh_token: api.refreshToken }); closeProfile(); toast("Every other sign-in of yours has ended."); } catch (e) { toast(errText(e), true); } }
});
document.addEventListener("input", (ev) => {
  const t = ev.target; if (!t.closest?.("#pop")) return;
  t.classList.remove("bad"); const er = $("#pf-err"); if (er) er.textContent = "";
  if (t.id === "pf-p1") { const [sc, tx] = pwStrength(t.value, S.prof?.email); $("#pf-meter").dataset.s = sc; $("#pf-mtext").textContent = tx; }
});
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !$("#pop").hidden) { closeProfile(); $("#meb").focus(); }
  if (ev.key === "Enter" && ev.target.id === "pf-nin") saveMyName();
});
