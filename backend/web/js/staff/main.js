// Jas iVision staff console: shell, sign-in, routing, and the "open the customer's app" hand-off. Screens live in screens.js.
import { Api, busy, clear, errText, h, mount } from "../lib.js";
import { theme } from "../app/theme.js";
import { field, ic, lockupPair, logoPair, mountOverlays, note, openSheet, skeleton, toast } from "../app/ui.js";
import { customerDetail, recorderDetail, SCREENS, TITLES } from "./screens.js";

const host = document.getElementById("app");
const state = { me: null };
const mem = {};
const api = new Api({ refreshUrl: "/admin/v1/auth/refresh", key: "gi_staff_rt", storage: sessionStorage, onSignedOut: () => { state.me = null; showSignIn("Please sign in again."); } });

// What each person can open. The server enforces the same rules; hiding is only for tidiness.
const NAV = [
  ["today", "Today", "today", null], ["customers", "Customers", "users", null], ["fleet", "Fleet", "server", null], ["billing", "Billing", "card", "owner"],
  ["plans", "Plans", "tag", "owner"], ["messaging", "Messaging", "chat", null], ["maintenance", "Maintenance", "wrench", "owner"], ["audit", "Audit log", "log", null], ["staff", "Staff and roles", "shield", "owner"],
];
const TABS = ["today", "customers", "fleet", "billing"];
const MORE = ["plans", "messaging", "maintenance", "audit", "staff"];
const allowed = (need) => !need || state.me?.role === need;
const desk = () => matchMedia("(min-width: 780px)").matches;

let ui = null, token = 0, leaveFns = [];
const X = {
  api, state, mem, theme,
  get isOwner() { return state.me?.role === "owner"; },
  go: (hash) => { location.hash = hash; },
  refresh: () => render(true),
  onLeave: (fn) => leaveFns.push(fn),
  openDetail, closeDetail, signOut, viewAs,
  desk,
};

// ------------------------------------------------------------------------------------------- detail pane / sheet
let detailBox = null;
function openDetail(kind, id) {
  const ds = { tab: "ov" };
  const box = h("div", { class: "stack" });
  const draw = async () => {
    mount(box, skeleton(3));
    try { mount(box, await (kind === "customer" ? customerDetail : recorderDetail)(X, id, ds, draw)); }
    catch (e) { mount(box, note(errText(e), "err")); }
  };
  detailBox = box;
  if (desk()) {
    ui.insp.hidden = false;
    mount(ui.insp, h("div", { class: "ih" }, h("h2", {}, kind === "customer" ? "Customer" : "Recorder"), h("button", { class: "xbtn", type: "button", "aria-label": "Close", onclick: closeDetail }, ic("x"))), h("div", { class: "ib2" }, box));
    ui.root.classList.add("has-insp");
  } else {
    openSheet(kind === "customer" ? "Customer" : "Recorder", box);
  }
  X.redrawDetail = draw;
  return draw();
}
function closeDetail() { detailBox = null; X.redrawDetail = null; if (ui) { ui.root.classList.remove("has-insp"); clear(ui.insp); } }

// ---------------------------------------------------------------------------------------------- routing
function parse() {
  const parts = (location.hash || "#/today").replace(/^#\//, "").split("/");
  return { screen: parts[0] || "today", id: parts[1] || null };
}

async function render(keep = false) {
  if (!ui || !state.me) return;
  const mine = ++token;
  leaveFns.forEach((f) => f()); leaveFns = [];
  const { screen, id } = parse();
  const key = screen === "today" || SCREENS[screen] ? screen : "today";
  const nav = NAV.find((n) => n[0] === key);
  if (nav && !allowed(nav[3])) { location.hash = "#/today"; return; }
  ui.mark(key);
  if (!keep && (!id || (key !== "customers" && key !== "fleet"))) closeDetail();      // leaving a list closes its detail pane
  const y = ui.scroll.scrollTop;
  if (!keep) mount(ui.page, skeleton(3));
  let nodes;
  try { nodes = await SCREENS[key](X); }
  catch (e) { nodes = [h("div", { class: "pagehead" }, h("div", {}, h("h1", { class: "lt" }, "Something went wrong"))), note(errText(e), "err"), h("button", { class: "btn", type: "button", onclick: () => render() }, "Try again")]; }
  if (mine !== token) return;
  const banners = X.viewing ? [] : [];
  mount(ui.page, banners, nodes);
  ui.scroll.scrollTop = keep ? y : 0;
  const t = ui.page.querySelector("h1");
  document.title = (t ? t.textContent + " · " : "") + "Jas iVision Staff";
  ui.cbar.textContent = t ? t.textContent : "";
  if (id && !keep && (key === "customers" || key === "fleet")) openDetail(key === "customers" ? "customer" : "recorder", id);
}

// ------------------------------------------------------------------------------------------------- shell
function shell() {
  const page = h("div", { class: "page" }), cbar = h("div", { class: "cbar", "aria-hidden": "true" });
  const scroll = h("main", { class: "scroll", id: "main" }, page);
  const btn = (k, label, icon) => h("button", { type: "button", "data-tab": k, onclick: () => X.go("#/" + k), "aria-label": label }, ic(icon), h("span", { class: "l" }, label));
  const navItems = NAV.filter((n) => allowed(n[3]));
  const phoneTabs = TABS.map((k) => navItems.find((n) => n[0] === k)).filter(Boolean);
  const tabbar = h("nav", { class: "tabbar", "aria-label": "Main" }, phoneTabs.map((n) => btn(n[0], n[1], n[2])), btn("more", "More", "more"));
  const insp = h("aside", { class: "insp", hidden: true });
  const sb = h("aside", { class: "sb" }, h("a", { href: "#/today", "aria-label": "Jas iVision staff home" }, logoPair("logo")),
    h("div", { class: "site" }, h("span", { class: "ico" }, ic("server")), h("span", {}, h("b", {}, "Production"), h("small", {}, location.host))),
    h("nav", { "aria-label": "Sidebar" }, navItems.map((n) => btn(n[0], n[1], n[2]))),
    h("div", { class: "foot" }, h("div", {}, `${state.me.name} · ${state.me.role}`)));
  const tb = h("header", { class: "tb" }, h("button", { class: "kbd", type: "button", onclick: openPalette }, ic("search"), "Search customers, recorders, pages", h("kbd", {}, "Ctrl K")), h("span", { class: "grow" }),
    h("button", { class: "avatar", type: "button", "aria-label": "Account", onclick: () => X.go("#/more") }, (state.me.name || "?").trim().slice(0, 1).toUpperCase()));
  const root = h("div", { class: "ax" }, sb, h("div", { class: "main" }, tb, cbar, scroll, tabbar), insp);
  mount(host, root);
  mountOverlays(root);
  scroll.addEventListener("scroll", () => cbar.classList.toggle("on", scroll.scrollTop > 56), { passive: true });
  ui = {
    root, page, scroll, cbar, insp,
    mark: (key) => {
      const tab = TABS.includes(key) ? key : "more";
      root.querySelectorAll(".tabbar [data-tab]").forEach((b) => (b.getAttribute("data-tab") === tab ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current")));
      root.querySelectorAll(".sb [data-tab]").forEach((b) => (b.getAttribute("data-tab") === key ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current")));
    },
  };
  // "More" is a real page on the phone listing the rest; on a desktop the sidebar already has everything.
  root.querySelector('.tabbar [data-tab="more"]').onclick = () => X.go("#/more");
}

// ----------------------------------------------------------------------------------------------- search
async function openPalette() {
  const input = h("input", { type: "search", placeholder: "Search customers, recorders or pages", autocomplete: "off", "aria-label": "Search" }), list = h("div");
  const pages = NAV.filter((n) => allowed(n[3])).map((n) => ({ label: "Go to " + n[1], icon: n[2], href: "#/" + n[0] }));
  let items = pages, sel = 0;
  const draw = () => mount(list, items.slice(0, 8).map((it, n) => h("div", { class: "palrow", "aria-selected": String(n === sel), onclick: () => pick(it) }, ic(it.icon), it.label)));
  const pick = (it) => { document.querySelector(".layer .xbtn")?.click(); X.go(it.href); };
  const filter = () => { const q = input.value.trim().toLowerCase(); items = ALL.filter((b) => b.label.toLowerCase().includes(q)); sel = 0; draw(); };
  let ALL = pages;
  Promise.all([api.get("/admin/v1/tenants?limit=200").catch(() => ({ items: [] })), api.get("/admin/v1/recorders?limit=200").catch(() => [])]).then(([t, r]) => {
    ALL = [...pages, ...t.items.map((c) => ({ label: c.name, icon: "users", href: "#/customers/" + c.id })), ...r.map((x) => ({ label: `${x.name || x.device_id} · ${x.tenant_name}`, icon: "server", href: "#/fleet/" + x.id }))];
    filter();
  });
  input.oninput = filter;
  input.onkeydown = (e) => {
    if (e.key === "ArrowDown") { sel = Math.min(sel + 1, Math.min(items.length, 8) - 1); draw(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { sel = Math.max(0, sel - 1); draw(); e.preventDefault(); }
    else if (e.key === "Enter" && items[sel]) pick(items[sel]);
  };
  openSheet("Search", h("div", { class: "field" }, input), list);
  draw();
}
addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k" && ui) { e.preventDefault(); openPalette(); } });

// ------------------------------------------------------------------------------- open the customer's app
const waiting = new Set();
let payload = null;
addEventListener("message", (e) => {
  if (e.origin !== location.origin || e.data?.type !== "gi-viewas-ready") return;
  if (payload) e.source.postMessage({ type: "gi-viewas", ...payload }, location.origin);
  else waiting.add(e.source);
});
/** Opens the customer's app in a new tab with a view-only session. The window is opened first, inside the click, so the browser allows it. */
async function viewAs(tenantId, reason) {
  const w = window.open("/?viewas=1", "_blank");
  if (!w) throw new Error("Your browser blocked the new window. Allow pop-ups for this site and try again.");
  try {
    const s = await api.post(`/admin/v1/tenants/${tenantId}/impersonate`, { reason, minutes: 30, notify: true });
    const t = await api.get(`/admin/v1/tenants/${tenantId}`);
    payload = { token: s.token, session_id: s.session_id, expires_at: s.expires_at, tenant: t.name };
    waiting.forEach((src) => { try { src.postMessage({ type: "gi-viewas", ...payload }, location.origin); } catch { /* window closed */ } });
    waiting.clear();
    setTimeout(() => { payload = null; }, 15000);                       // the hand-off is only kept for a moment
    return s;
  } catch (e) { w.close(); throw e; }
}
addEventListener("message", async (e) => {
  if (e.origin !== location.origin || e.data?.type !== "gi-viewas-stop" || !e.data.session_id) return;
  try { await api.del("/admin/v1/impersonations/" + e.data.session_id); } catch { /* already ended */ }
});

// ------------------------------------------------------------------------------------------------- sign-in
function authPage(...body) {
  mount(host, h("div", { class: "ax" }, h("div", { class: "authwrap" }, h("div", { class: "authbox" }, h("div", {}, lockupPair(), h("p", { class: "tag" }, "Staff console"), ...body)))));
  ui = null;
}

async function showSignIn(message) {
  let mfa = false;
  try { mfa = (await (await fetch("/admin/v1/auth/options")).json()).mfa_required; } catch { /* default to password only */ }
  const email = h("input", { type: "email", autocomplete: "username", required: true, autofocus: true });
  const pw = h("input", { type: "password", autocomplete: "current-password", required: true });
  const code = h("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 8, placeholder: "6-digit code" });
  const err = h("div"), btn = h("button", { class: "btn block", type: "submit" }, "Sign in");
  const form = h("form", { class: "card", onsubmit: busy(btn, async (e) => {
    e.preventDefault(); clear(err);
    try {
      api.setTokens(await api.call("POST", "/admin/v1/auth/login", { email: email.value, password: pw.value, ...(mfa ? { code: code.value } : {}) }));
      pw.value = ""; code.value = "";
      boot();
    } catch (x) { err.append(note(x.message, "err")); }
  }) }, message ? note(message, "ok") : null, field("Work email", email), field("Password", pw), mfa ? field("Authenticator code", code) : null, err, btn);
  authPage(form, h("p", { class: "sub" }, "Staff sessions end when you close this window."));
}

function showSetPassword() {
  const tk = new URLSearchParams(location.search).get("token") || "";
  history.replaceState(null, "", "/staff/set-password");
  const a = h("input", { type: "password", autocomplete: "new-password", required: true, minlength: 10 }), b = h("input", { type: "password", autocomplete: "new-password", required: true });
  const err = h("div"), btn = h("button", { class: "btn block", type: "submit" }, "Save password");
  const form = h("form", { class: "card", onsubmit: busy(btn, async (e) => {
    e.preventDefault(); clear(err);
    if (a.value !== b.value) return err.append(note("The two passwords are not the same.", "err"));
    try { await api.call("POST", "/admin/v1/auth/set-password", { token: tk, password: a.value }); history.replaceState(null, "", "/staff/"); showSignIn("Your password is saved. Sign in with it now."); }
    catch (x) { err.append(note(x.message, "err")); }
  }) }, h("h3", {}, "Choose your password"), field("New password", a, "At least 10 characters. A few words together is easy to remember and hard to guess."), field("Type it again", b), err, btn);
  authPage(tk ? form : h("div", { class: "card" }, note("This link is not complete. Open the full link you were sent, or ask an owner for a new one.", "warn")));
}

async function signOut() {
  try { if (api.refreshToken) await api.call("POST", "/admin/v1/auth/logout", { refresh_token: api.refreshToken }); } catch { /* signing out anyway */ }
  api.clear(); state.me = null; closeDetail(); Object.keys(mem).forEach((k) => delete mem[k]);
  location.hash = "";
  showSignIn();
}

async function boot() {
  if (!api.signedIn()) return showSignIn();
  try { state.me = await api.get("/admin/v1/me"); }
  catch (e) {
    if (e.status === 401) return showSignIn();
    return authPage(note(errText(e), "err"), h("button", { class: "btn", type: "button", onclick: boot }, "Try again"));
  }
  if (!ui) shell();
  render();
}
addEventListener("hashchange", () => render());
matchMedia("(min-width: 780px)").addEventListener?.("change", () => { closeDetail(); });

if ("serviceWorker" in navigator) addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => { /* the console works without it */ }));
if (location.pathname === "/staff/set-password") showSetPassword(); else boot();
export { TITLES, toast };
