// Jas iVision Platform Admin (the panel our own staff use): shell, sign-in, routing, and the "open the customer's app" hand-off. Screens live in screens.js.
import { Api, busy, clear, errText, h, mount, qs } from "../lib.js";
import { theme } from "../app/theme.js";
import { openPalette, searchShortcuts, sidebar, tabbar } from "../app/shell.js";
import { field, ic, lockupPair, logoPair, mountOverlays, note, openSheet, skeleton, toast } from "../app/ui.js";
import { customerDetail, recorderDetail, SCREENS } from "./screens.js";
import { groupsFor, navItem, pagesFor, TABS, TITLES } from "./routes.js";
import { watchEvents } from "./live.js";

const host = document.getElementById("app");
const state = { me: null };
const mem = {};
const api = new Api({ refreshUrl: "/admin/v1/auth/refresh", key: "gi_staff_rt", storage: sessionStorage, onSignedOut: () => { state.me = null; showSignIn(); } });

// Every page is described once in routes.js: the sidebar, the phone tabs, the search box and the More page all read it.
const desk = () => matchMedia("(min-width: 780px)").matches;

let ui = null, token = 0, leaveFns = [], feed = null;

// Live counts: a new alert is worth a word, and the Today and Alerts pages redraw when the numbers move. Nothing is redrawn
// while someone is typing (a search or a reason), so the feed never takes the keyboard away from them.
function startFeed() {
  feed?.stop();
  feed = watchEvents(api, { onCounts: (c) => {
    const was = state.counts;
    state.counts = c;
    if (was && c.alerts_open > was.alerts_open) toast("A new alert needs attention");
    const screen = parse().screen;
    const typing = /^(input|textarea|select)$/i.test(document.activeElement?.tagName || "");
    if (!typing && document.visibilityState === "visible" && (screen === "today" || screen === "alerts")) render(true);
  } });
}
const X = {
  api, state, mem, theme,
  get isOwner() { return state.me?.role === "owner"; },
  go: (hash) => { location.hash = hash; },
  refresh: () => render(true),
  onLeave: (fn) => leaveFns.push(fn),
  openDetail, closeDetail, signOut,
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
  if (key !== "more" && !navItem(key, X.isOwner)) { location.hash = "#/today"; return; }
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
  document.title = (t ? t.textContent + " · " : "") + "Jas iVision Platform Admin";
  ui.cbar.textContent = t ? t.textContent : "";
  if (id && !keep && (key === "customers" || key === "fleet")) openDetail(key === "customers" ? "customer" : "recorder", id);
}

// ------------------------------------------------------------------------------------------------- shell
function shell() {
  const page = h("div", { class: "page" }), cbar = h("div", { class: "cbar", "aria-hidden": "true" });
  const scroll = h("main", { class: "scroll", id: "main" }, page);
  const item = (k) => navItem(k, X.isOwner);
  const groups = groupsFor(X.isOwner);
  const go = (href) => X.go(href);
  const tabbarEl = tabbar(go, [...TABS.map(item).filter(Boolean), { key: "more", label: "More", icon: "more" }]);
  const insp = h("aside", { class: "insp", hidden: true });
  const side = sidebar({ go, groups, storeKey: "gi_staff_rail",
    head: [h("a", { href: "#/today", "aria-label": "Jas iVision Platform Admin home" }, logoPair("logo")), h("div", { class: "site" }, h("span", { class: "ico" }, ic("server")), h("span", {}, h("b", {}, "Production"), h("small", {}, location.host)))],
    foot: [h("div", { class: "usage" }, `${state.me.name} \u00b7 ${state.me.role}`)] });
  const sb = side.el;
  const tb = h("header", { class: "tb" }, h("button", { class: "kbd", type: "button", onclick: () => search() }, ic("search"), h("span", { class: "kl" }, "Search customers, recorders, pages"), h("kbd", {}, "Ctrl K")), h("span", { class: "grow" }),
    h("button", { class: "avatar", type: "button", "aria-label": "Account", onclick: () => X.go("#/more") }, (state.me.name || "?").trim().slice(0, 1).toUpperCase()));
  const root = h("div", { class: "ax" }, sb, h("div", { class: "main" }, tb, cbar, scroll, tabbarEl), insp);
  mount(host, root);
  mountOverlays(root);
  scroll.addEventListener("scroll", () => cbar.classList.toggle("on", scroll.scrollTop > 56), { passive: true });
  ui = {
    root, page, scroll, cbar, insp,
    mark: (key) => {
      const tab = TABS.includes(key) ? key : "more";
      side.mark(key);
      tabbarEl.querySelectorAll("[data-tab]").forEach((b) => (b.getAttribute("data-tab") === tab ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current")));
    },
  };
  // "More" is a real page on the phone listing the rest; on a desktop the sidebar already has everything.
  tabbarEl.querySelector('[data-tab="more"]').onclick = () => X.go("#/more");
}

// ----------------------------------------------------------------------------------------------- search
// Staff can search pages, every customer and every recorder (the lists the pages already use).
function search() {
  openPalette({
    placeholder: "Search customers, recorders or pages", recentKey: "gi_staff_recent", go: (href) => X.go(href),
    pages: pagesFor(X.isOwner),
    lookup: async (q) => {
      const r = await api.get("/admin/v1/search" + qs({ q }));
      return [{ section: "Customers", icon: "users", items: r.customers.map((c) => ({ label: c.name, sub: c.city || null, href: "#/customers/" + c.id })) },
        { section: "Recorders", icon: "server", items: r.recorders.map((x) => ({ label: x.name || x.device_id, sub: x.tenant_name, href: "#/fleet/" + x.id })) }]
        .filter((s) => s.items.length);
    },
  });
}
searchShortcuts(() => !!ui, search);

// ------------------------------------------------------------------------------------------------- sign-in
function authPage(...body) {
  mount(host, h("div", { class: "ax" }, h("div", { class: "authwrap" }, h("div", { class: "authbox" }, h("div", {}, lockupPair(), h("p", { class: "tag" }, "Platform Admin"), ...body)))));
  ui = null; feed?.stop(); feed = null;
}

// There is one sign-in page for everyone, at "/". A staff member who signs in there is handed on to this panel already signed in, so with no sign-in
// (or one that has ended) the person is sent there.
function showSignIn() { location.replace("/"); }

function showSetPassword() {
  const tk = new URLSearchParams(location.search).get("token") || "";
  history.replaceState(null, "", "/platform/set-password");
  const a = h("input", { type: "password", autocomplete: "new-password", required: true, minlength: 10 }), b = h("input", { type: "password", autocomplete: "new-password", required: true });
  const err = h("div"), btn = h("button", { class: "btn block", type: "submit" }, "Save password");
  const form = h("form", { class: "card", onsubmit: busy(btn, async (e) => {
    e.preventDefault(); clear(err);
    if (a.value !== b.value) return err.append(note("The two passwords are not the same.", "err"));
    try { await api.call("POST", "/admin/v1/auth/set-password", { token: tk, password: a.value }); showSignIn(); }
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
  startFeed();
}
addEventListener("hashchange", () => render());
matchMedia("(min-width: 780px)").addEventListener?.("change", () => { closeDetail(); });

if ("serviceWorker" in navigator) addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => { /* the console works without it */ }));
if (location.pathname === "/platform/set-password") showSetPassword(); else boot();
export { TITLES, toast };
