// Jas iVision customer app: shell, router, sign-in, theme, install prompt, offline state. Pages come from screens.js.
import { Api, busy, clear, errText, h, mount } from "../lib.js";
import { screens, title } from "./screens.js";
import { theme } from "./theme.js";
import { dropPictures, empty, field, ic, logoPair, lockupPair, mountOverlays, note, openSheet, closeSheet, toast } from "./ui.js";

const host = document.getElementById("app");
const state = { me: null, viewing: null };
const mem = {};
const api = new Api({ refreshUrl: "/v1/auth/refresh", key: "gi_rt", onSignedOut: () => { state.me = null; showSignIn("Please sign in again."); } });

// ------------------------------------------------------------------------------------------------------ install
let deferred = null;
const standalone = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent);
addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); deferred = e; });
addEventListener("appinstalled", () => { deferred = null; });
const install = {
  available: () => !standalone() && (!!deferred || isIOS()),
  async prompt() {
    if (deferred) { deferred.prompt(); try { await deferred.userChoice; } catch { /* dismissed */ } deferred = null; return; }
    openSheet("Install on your iPhone", h("div", { class: "stepn" }, h("em", {}, "1"), h("div", {}, "Tap the Share button ", ic("share"), " in Safari.")),
      h("div", { class: "stepn" }, h("em", {}, "2"), h("div", {}, "Choose ", h("b", {}, "Add to Home Screen"), ".")), h("div", { class: "stepn" }, h("em", {}, "3"), h("div", {}, "Open Jas iVision from your home screen.")));
  },
};

// ------------------------------------------------------------------------------------------------------ routing
const TABS = [["today", "Today", "today", "#/today"], ["cameras", "Cameras", "cam", "#/cameras"], ["activity", "Activity", "activity", "#/activity"], ["alerts", "Alerts", "bell", "#/alerts"], ["more", "More", "more", "#/more"]];
const ROUTES = [
  [/^#\/(today)?$/, "today", "today"], [/^#\/cameras$/, "cameras", "cameras"], [/^#\/cameras\/([\w-]+)$/, "cameraPage", "cameras"], [/^#\/activity$/, "activity", "activity"],
  [/^#\/alerts$/, "alerts", "alerts"], [/^#\/more$/, "more", "more"], [/^#\/recorders$/, "recorders", "more"], [/^#\/recorders\/new$/, "newRecorder", "more"],
  [/^#\/recorders\/([\w-]+)$/, "recorderPage", "more"], [/^#\/reports$/, "reports", "more"],
];
let ui = null, token = 0, leaveFns = [];
const X = {
  api, state, mem, theme, install,
  get isAdmin() { return state.me?.user.role === "admin"; },
  go: (hash) => { location.hash = hash; },
  refresh: () => render(true),
  onLeave: (fn) => leaveFns.push(fn),
  signOut,
};

function stopViewing() {
  try { window.opener?.postMessage({ type: "gi-viewas-stop", session_id: state.viewing?.session_id }, location.origin); } catch { /* the console tab is gone */ }
  api.clear(); state.me = null;
  window.close();
  authPage(note("The view-only session has ended. You can close this tab."));
}

async function signOut() {
  if (state.viewing) return stopViewing();
  try { if (api.refreshToken) await api.call("POST", "/v1/auth/logout", { refresh_token: api.refreshToken }); } catch { /* signing out anyway */ }
  api.clear(); state.me = null; dropPictures();
  try { navigator.serviceWorker?.controller?.postMessage("clear-api-cache"); } catch { /* no worker */ }
  Object.keys(mem).forEach((k) => delete mem[k]);
  location.hash = "";
  showSignIn();
}

async function render(keepScroll = false) {
  if (!ui) return;
  const mine = ++token;
  leaveFns.forEach((f) => f()); leaveFns = [];
  const hash = location.hash || "#/today";
  const hit = ROUTES.map(([re, fn, tab]) => [hash.match(re), fn, tab]).find(([m]) => m);
  if (!hit) { location.hash = "#/today"; return; }
  const [m, fn, tab] = hit;
  ui.mark(tab);
  const t0 = Date.now(), prev = ui.scroll.scrollTop;
  if (!keepScroll) mount(ui.page, h("div", { class: "skel blk" }));
  let nodes;
  try { nodes = await screens[fn](X, ...m.slice(1).filter(Boolean)); }
  catch (e) { nodes = [title("Something went wrong"), note(errText(e), "err"), h("button", { class: "btn", type: "button", onclick: () => render() }, "Try again")]; if (e.status === 0) ui.setOffline(true); }
  if (mine !== token) return;                       // the person moved on while this page was loading
  const banners = [];
  if (state.viewing) banners.push(h("div", { class: "banner info" }, ic("eye"), h("div", { class: "grow" }, "You are looking at ", h("b", {}, state.viewing.tenant), " as Jas iVision staff. View only. Everything you open is logged."), h("button", { type: "button", onclick: stopViewing }, "Stop")));
  if (!navigator.onLine || api.lastStale >= t0) banners.push(h("div", { class: "banner" }, ic("nowifi"), h("div", { class: "grow" }, h("b", {}, "You are offline. "), "Showing what was saved on this device.")));
  mount(ui.page, banners, nodes);
  ui.scroll.scrollTop = keepScroll ? prev : 0;
  document.title = (ui.page.querySelector("h1")?.textContent || "Jas iVision") + " \u00b7 Jas iVision";
  ui.cbar.textContent = ui.page.querySelector("h1")?.textContent || "";
}

// ------------------------------------------------------------------------------------------------------ shell
function shell() {
  const page = h("div", { class: "page" }), cbar = h("div", { class: "cbar", "aria-hidden": "true" });
  const ptr = h("div", { class: "ptr" }, "Release to refresh");
  const scroll = h("main", { class: "scroll", id: "main" }, page);
  const navBtn = (k, label, icon, href, cls) => h("button", { type: "button", "data-tab": k, onclick: () => X.go(href), "aria-label": label }, ic(icon), h("span", { class: "l" }, label));
  const tabbar = h("nav", { class: "tabbar", "aria-label": "Main" }, TABS.map(([k, l, i, href]) => navBtn(k, l, i, href)));
  const cams = state.me.tenant;
  const sb = h("aside", { class: "sb" }, h("a", { href: "#/today", "aria-label": "Jas iVision home" }, logoPair("logo")),
    h("div", { class: "site" }, h("div", {}, h("b", {}, cams.name), h("small", {}, "Plan " + cams.plan_name))),
    h("nav", { "aria-label": "Sidebar" }, [...TABS, ["recorders", "Recorders", "server", "#/recorders"], ["reports", "Reports", "doc", "#/reports"]].map(([k, l, i, href]) => navBtn(k, l, i, href))),
    h("div", { class: "foot" }, h("div", {}, `Cameras ${cams.cameras_in_use} of ${cams.camera_limit}`), h("div", { class: "meter" }, h("i", { class: "m" }))));
  const meter = sb.querySelector(".meter i");
  meter.style.width = Math.min(100, Math.round((cams.cameras_in_use / Math.max(1, cams.camera_limit)) * 100)) + "%";
  const palBtn = h("button", { class: "kbd", type: "button", onclick: () => openPalette() }, ic("search"), "Search or jump to\u2026", h("kbd", {}, "Ctrl K"));
  const tb = h("header", { class: "tb" }, palBtn, h("span", { class: "grow" }), h("button", { class: "ib", type: "button", "aria-label": "Alerts", onclick: () => X.go("#/alerts") }, ic("bell")),
    h("button", { class: "avatar", type: "button", "aria-label": "Account", onclick: () => X.go("#/more") }, (state.me.user.name || "?").trim().slice(0, 1).toUpperCase()));
  tb.querySelector(".grow").setAttribute("class", "grow");
  const root = h("div", { class: "ax" }, sb, h("div", { class: "main" }, tb, cbar, ptr, scroll, tabbar));
  mount(host, root);
  mountOverlays(root);
  scroll.addEventListener("scroll", () => cbar.classList.toggle("on", scroll.scrollTop > 56), { passive: true });
  // pull down to refresh (touch only)
  let y0 = null;
  scroll.addEventListener("touchstart", (e) => { y0 = scroll.scrollTop === 0 ? e.touches[0].clientY : null; }, { passive: true });
  scroll.addEventListener("touchmove", (e) => { if (y0 !== null) ptr.classList.toggle("on", e.touches[0].clientY - y0 > 80); }, { passive: true });
  scroll.addEventListener("touchend", () => { if (ptr.classList.contains("on")) { ptr.classList.remove("on"); render(true); } y0 = null; });
  ui = {
    root, page, scroll, cbar,
    mark: (tab) => root.querySelectorAll("[data-tab]").forEach((b) => (b.getAttribute("data-tab") === tab ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current"))),
    setOffline: () => {},
  };
}

// ---------------------------------------------------------------------------------------------------- palette
function openPalette() {
  const input = h("input", { type: "search", placeholder: "Search cameras, recorders or pages", autocomplete: "off", "aria-label": "Search" }), list = h("div");
  const base = [...TABS.map(([, l, i, href]) => ({ label: l, icon: i, href })), { label: "Recorders", icon: "server", href: "#/recorders" }, { label: "Reports", icon: "doc", href: "#/reports" }];
  let items = base, sel = 0;
  const draw = () => mount(list, items.slice(0, 8).map((it, n) => h("div", { class: "palrow", "aria-selected": String(n === sel), onclick: () => pick(it) }, ic(it.icon), it.label)));
  const pick = (it) => { closeSheet(); X.go(it.href); };
  Promise.all([api.get("/v1/cameras").catch(() => []), api.get("/v1/recorders").catch(() => [])]).then(([cams, recs]) => {
    base.push(...cams.map((c) => ({ label: c.name, icon: "cam", href: "#/cameras/" + c.id })), ...recs.map((r) => ({ label: r.name || r.device_id, icon: "server", href: "#/recorders/" + r.id })));
    filter();
  });
  const filter = () => { const q = input.value.trim().toLowerCase(); items = base.filter((b) => b.label.toLowerCase().includes(q)); sel = 0; draw(); };
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

// ------------------------------------------------------------------------------------------------------ sign-in
function authPage(...body) {
  mount(host, h("div", { class: "ax" }, h("div", { class: "authwrap" }, h("div", { class: "authbox" }, h("div", {}, lockupPair(), ...body)))));
  ui = null;
}

function showSignIn(message) {
  if (state.viewing) return authPage(note("The view-only session has ended. You can close this tab.", "warn"));
  const email = h("input", { type: "email", autocomplete: "username", required: true, autofocus: true });
  const pw = h("input", { type: "password", autocomplete: "current-password", required: true });
  const org = h("input", { type: "text", autocomplete: "organization", placeholder: "for example sharma-auto" });
  const code = h("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 8, placeholder: "6-digit code" });
  const orgField = field("Organisation short name", org, "Your email is used in more than one organisation. Ask your administrator for the short name.");
  const codeField = field("Authenticator code", code, "Staff sign in with the 6-digit code from their authenticator app.");
  orgField.classList.add("hide"); codeField.classList.add("hide");
  const err = h("div"), btn = h("button", { class: "btn block", type: "submit" }, "Sign in");
  const form = h("form", { class: "card", onsubmit: busy(btn, async (e) => {
    e.preventDefault(); clear(err);
    try {
      const r = await api.call("POST", "/v1/auth/sign-in", { email: email.value, password: pw.value, ...(org.value ? { tenant: org.value } : {}), ...(code.value ? { code: code.value } : {}) });
      if (r.next === "code") { codeField.classList.remove("hide"); code.focus(); err.append(note("Enter the 6-digit code from your authenticator app.", "warn")); return; }
      pw.value = ""; code.value = "";
      if (r.kind === "staff") {                                   // staff go to the staff console, in this same tab
        sessionStorage.setItem("gi_staff_rt", r.refresh_token);
        location.href = "/staff/";
        return;
      }
      api.setTokens(r);
      boot();
    } catch (x) {
      if (x.code === "choose_account") orgField.classList.remove("hide");
      err.append(note(x.message, "err"));
    }
  }) }, message ? note(message, "ok") : null, field("Email", email), field("Password", pw), orgField, codeField, err, btn);
  authPage(form, h("p", { class: "sub" }, "Forgotten your password? Ask your administrator or Jas\u00a0iVision support for a new link."));
}

function showSetPassword() {
  const token = new URLSearchParams(location.search).get("token") || "";
  history.replaceState(null, "", "/set-password");
  const a = h("input", { type: "password", autocomplete: "new-password", required: true, minlength: 10 }), b = h("input", { type: "password", autocomplete: "new-password", required: true });
  const err = h("div"), btn = h("button", { class: "btn block", type: "submit" }, "Save password");
  const form = h("form", { class: "card", onsubmit: busy(btn, async (e) => {
    e.preventDefault(); clear(err);
    if (a.value !== b.value) return err.append(note("The two passwords are not the same.", "err"));
    try { await api.call("POST", "/v1/auth/set-password", { token, password: a.value }); history.replaceState(null, "", "/"); showSignIn("Your password is saved. Sign in with it now."); }
    catch (x) { err.append(note(x.message, "err")); }
  }) }, h("h3", {}, "Choose your password"), field("New password", a, "At least 10 characters. A few words together is easy to remember and hard to guess."), field("Type it again", b), err, btn);
  authPage(token ? form : h("div", { class: "card" }, note("This link is not complete. Open the full link you were sent, or ask for a new one.", "warn")));
}

// ------------------------------------------------------------------------------------------------------ start
async function boot() {
  if (!api.signedIn()) return showSignIn();
  try { state.me = await api.get("/v1/me"); }
  catch (e) {
    if (e.status === 401) return showSignIn();
    return authPage(note(errText(e), "err"), h("button", { class: "btn", type: "button", onclick: boot }, "Try again"));
  }
  if (!ui) shell();
  render();
}
addEventListener("hashchange", () => render());
addEventListener("online", () => render(true));
addEventListener("offline", () => render(true));

if ("serviceWorker" in navigator) addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => { /* the app works without it */ }));
/** A staff member opened this tab from the console: wait for the view-only session to be handed over, then show the customer's app. */
function waitForViewAs() {
  authPage(h("div", { class: "card" }, h("p", {}, "Opening the customer's app…")));
  const timer = setTimeout(() => authPage(note("Could not open the customer's app. Close this tab and try again from the console.", "err")), 10000);
  addEventListener("message", (e) => {
    if (e.origin !== location.origin || e.data?.type !== "gi-viewas") return;
    clearTimeout(timer);
    state.viewing = { tenant: e.data.tenant, session_id: e.data.session_id };
    api.access = e.data.token;
    api.headers = { "X-View-As": "1" };                 // the service worker never saves what a staff member looks at
    history.replaceState(null, "", "/#/today");
    boot();
  });
  try { window.opener.postMessage({ type: "gi-viewas-ready" }, location.origin); } catch { /* no console tab */ }
}

if (location.pathname === "/set-password") showSetPassword();
else if (new URLSearchParams(location.search).get("viewas") && window.opener) waitForViewAs();
else boot();
