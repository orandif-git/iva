// Staff console: sign in (password and authenticator code), see the business at a glance, create customers, invite their people, add recorders.
import { Api, BRAND, LOGO, ago, busy, chip, clear, copyRow, copy, errText, field, fmtDateTime, fmtNum, h, icon, modal, mount, note, qs, toast } from "./lib.js";

const app = document.getElementById("app");
const api = new Api({ refreshUrl: "/admin/v1/auth/refresh", key: "gi_staff_rt", storage: sessionStorage, onSignedOut: () => { state.me = null; signIn(); } });
const state = { me: null, view: 0 };
const can = (...roles) => roles.includes(state.me?.role);
const TENANT = { active: ["Active", "ok"], trial: ["Trial", "acc"], past_due: ["Payment due", "warn"], suspended: ["Suspended", "crit"], cancelled: ["Cancelled", ""] };
const LINK = { online: ["Online", "ok"], offline: ["Offline", "crit"], never: ["Waiting", ""] };
const STATE = { streaming: ["Live", "ok"], reconnecting: ["Reconnecting", "warn"], offline: ["Offline", "crit"], unknown: ["Waiting", ""] };
const rupees = (paise) => paise == null ? "hidden" : "₹" + fmtNum(Math.round(paise / 100));
const top = (title, ...actions) => h("div", { class: "top" }, h("h1", {}, title), h("div", { class: "actions" }, actions));
const stat = (value, label) => h("div", { class: "stat" }, h("b", {}, value), h("span", {}, label));

// ------------------------------------------------------------------------------------------------ sign-in
function signIn() {
  const email = h("input", { type: "email", autocomplete: "username", required: true, autofocus: true });
  const pw = h("input", { type: "password", autocomplete: "current-password", required: true });
  const code = h("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", pattern: "[0-9]{6}", maxlength: 6, required: true });
  const err = h("div"), btn = h("button", { class: "btn primary", type: "submit" }, "Sign in");
  const form = h("form", { onsubmit: busy(btn, async (e) => {
    e.preventDefault(); clear(err);
    try { api.setTokens(await api.call("POST", "/admin/v1/auth/login", { email: email.value, password: pw.value, code: code.value })); pw.value = code.value = ""; boot(); }
    catch (x) { err.append(note(x.message, "err")); code.value = ""; }
  }) }, field("Email", email), field("Password", pw), field("Code from your authenticator app", code, "The 6 digits shown in the app. They change every 30 seconds."), err, btn);
  mount(app, h("div", { class: "auth" }, h("div", { class: "box" }, h("div", { class: "brand" }, h("i", {}, icon(LOGO)), `${BRAND.name} staff`), h("p", { class: "tagline" }, BRAND.tagline), h("div", { class: "card" }, h("h1", {}, "Staff sign in"), form))));
}

// ------------------------------------------------------------------------------------------------ shell and routes
const NAV = [["#/", "Overview"], ["#/customers", "Customers"]];
function shell() {
  const out = h("button", { class: "btn sm" }, "Sign out");
  out.onclick = async () => { try { if (api.refreshToken) await api.call("POST", "/admin/v1/auth/logout", { refresh_token: api.refreshToken }); } catch { /* signing out anyway */ } api.clear(); state.me = null; location.hash = ""; signIn(); };
  mount(app, h("div", { class: "shell" }, h("aside", { class: "side" }, h("div", { class: "brand" }, h("i", {}, icon(LOGO)), "Staff"),
    h("div", { class: "org" }, `${state.me.name} · ${state.me.role}`), h("nav", { class: "nav" }, NAV.map(([href, l]) => h("a", { href }, l)), h("span", {}, out))), h("main", { class: "main", id: "main" })));
}
const ROUTES = [[/^#\/?$/, overview], [/^#\/customers$/, customers], [/^#\/customers\/([\w-]+)$/, customer]];
async function render() {
  const main = document.getElementById("main");
  if (!main) return;
  const mine = ++state.view;
  const base = "#/" + ((location.hash || "#/").split("/")[1] || "");
  document.querySelectorAll(".nav a").forEach((a) => (a.getAttribute("href") === base ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  for (const [re, fn] of ROUTES) {
    const m = (location.hash || "#/").match(re);
    if (!m) continue;
    mount(main, h("p", { class: "muted" }, "Loading…"));
    try { const out = await fn(...m.slice(1)); if (mine === state.view) mount(main, out); }
    catch (e) { if (mine === state.view) mount(main, note(errText(e), "err"), h("button", { class: "btn", onclick: render }, "Try again")); }
    return;
  }
  location.hash = "#/";
}
async function boot() {
  if (!api.signedIn()) return signIn();
  try { state.me = await api.get("/admin/v1/me"); } catch (e) { return e.status === 401 ? signIn() : mount(app, h("div", { class: "auth" }, h("div", { class: "box" }, note(errText(e), "err")))); }
  shell(); render();
}
addEventListener("hashchange", render);

// ------------------------------------------------------------------------------------------------ overview
async function overview() {
  const o = await api.get("/admin/v1/overview");
  const t = o.tenants || {}, r = o.recorders || {};
  return [top("Overview"), h("div", { class: "grid" }, stat(fmtNum((t.active || 0) + (t.past_due || 0)), "Paying customers"), stat(fmtNum(t.trial || 0), "On trial"), stat(fmtNum(t.suspended || 0), "Suspended"),
    stat(`${fmtNum(r.online || 0)} of ${fmtNum((r.online || 0) + (r.offline || 0) + (r.never || 0))}`, "Recorders online"), stat(`${fmtNum(o.cameras_in_use)} of ${fmtNum(o.cameras_licensed)}`, "Cameras in use"),
    stat(rupees(o.mrr_paise), "Monthly revenue")),
    (r.never || 0) ? note(`${r.never} recorder(s) have been created but never connected.`) : null];
}

// ------------------------------------------------------------------------------------------------ customers
async function customers() {
  const q = h("input", { type: "search", placeholder: "Search by name or city", maxlength: 80 });
  const status = h("select", {}, h("option", { value: "" }, "All statuses"), Object.entries(TENANT).map(([k, [l]]) => h("option", { value: k }, l)));
  const body = h("div");
  const load = async () => {
    try {
      const r = await api.get("/admin/v1/tenants" + qs({ q: q.value.trim(), status: status.value, limit: 100 }));
      mount(body, r.items.length ? h("div", { class: "card" }, h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Customer", "City", "Plan", "Status", "Cameras", "Recorders online"].map((x) => h("th", {}, x)))),
        h("tbody", {}, r.items.map((c) => { const [l, k] = TENANT[c.status] || [c.status, ""]; return h("tr", {}, h("td", {}, h("a", { href: "#/customers/" + c.id }, c.name)), h("td", {}, c.city || ""), h("td", {}, c.plan_id), h("td", {}, chip(l, k)),
          h("td", { class: "num" }, `${c.cameras_in_use} / ${c.camera_limit}`), h("td", { class: "num" }, `${c.recorders_online} / ${c.recorders_total}`)); }))))) : h("div", { class: "empty" }, "No customers match."));
    } catch (e) { mount(body, note(errText(e), "err")); }
  };
  q.oninput = () => { clearTimeout(q._t); q._t = setTimeout(load, 300); };
  status.onchange = load;
  load();
  return [top("Customers", can("owner", "technician") ? h("button", { class: "btn primary", onclick: newCustomer }, "New customer") : null), h("div", { class: "row" }, h("div", {}, q), h("div", {}, status)), h("p"), body];
}

async function newCustomer() {
  const plans = (await api.get("/admin/v1/plans")).items;
  const name = h("input", { type: "text", maxlength: 120, required: true }), city = h("input", { type: "text", maxlength: 120 });
  const plan = h("select", {}, plans.map((p) => h("option", { value: p.id }, `${p.name} (₹${fmtNum(p.price_per_camera_paise / 100)} per camera)`)));
  const limit = h("input", { type: "number", min: 0, max: 5000, value: 5 }), err = h("div");
  modal("New customer", h("div", {}, field("Business name", name), field("City", city), field("Plan", plan), field("Cameras allowed", limit, "You can change this later."), err,
    h("p", { class: "hint" }, "The customer starts on a 14-day trial. Next you invite their first user.")),
    { actions: [{ label: "Cancel", run: (x) => x() }, { label: "Create", kind: "primary", run: async (close) => {
      clear(err);
      try { const c = await api.post("/admin/v1/tenants", { name: name.value, city: city.value || null, plan_id: plan.value, camera_limit: Number(limit.value) }); close(); location.hash = "#/customers/" + c.id; }
      catch (x) { err.append(note(errText(x), "err")); }
    } }] });
}

// ------------------------------------------------------------------------------------------------ one customer
const linkModal = (title, link, expires) => modal(title, h("div", {}, h("p", {}, "Send this link to the person (for example on WhatsApp). It works once."),
  h("dl", { class: "cred" }, copyRow("Link", link)), h("p", { class: "hint" }, "Valid until " + fmtDateTime(expires) + ". Anyone with the link can set the password, so send it only to them."), ), { actions: [{ label: "Done", kind: "primary", run: (x) => x() }] });

async function customer(id) {
  const [t, users, recs, cams] = await Promise.all([api.get("/admin/v1/tenants/" + id), api.get(`/admin/v1/tenants/${id}/users`), api.get("/admin/v1/recorders" + qs({ tenant_id: id })), api.get(`/admin/v1/tenants/${id}/cameras`)]);
  const [l, k] = TENANT[t.status] || [t.status, ""];
  const reload = () => render();
  const reason = (title, path, label) => { const r = h("input", { type: "text", maxlength: 500, placeholder: "Why? (at least 5 characters)" });
    modal(title, field("Reason", r), { actions: [{ label: "Cancel", run: (x) => x() }, { label: label, kind: "danger", run: async (close) => { try { await api.post(path, { reason: r.value }); close(); toast("Done."); reload(); } catch (e) { toast(errText(e), true); } } }] }); };
  const invite = () => {
    const email = h("input", { type: "email", required: true }), name = h("input", { type: "text", required: true, maxlength: 120 });
    const role = h("select", {}, h("option", { value: "admin" }, "Administrator (can change everything)"), h("option", { value: "regular" }, "Viewer (can look only)")), err = h("div");
    modal("Invite a user", h("div", {}, field("Name", name), field("Email", email), field("Role", role), err), { actions: [{ label: "Cancel", run: (x) => x() }, { label: "Create invitation", kind: "primary", run: async (close) => {
      clear(err);
      try { const r = await api.post(`/admin/v1/tenants/${id}/users`, { email: email.value, name: name.value, role: role.value }); close(); linkModal("Invitation link", r.link, r.expires_at); reload(); }
      catch (x) { err.append(note(errText(x), "err")); }
    } }] });
  };
  const resetLink = async (u) => { try { const r = await api.post(`/admin/v1/tenants/${id}/users/${u.id}/reset-link`); linkModal(u.status === "invited" ? "Invitation link" : "Password reset link", r.link, r.expires_at); } catch (e) { toast(errText(e), true); } };
  const disable = (u) => modal("Disable " + u.name + "?", h("p", {}, "They are signed out at once and cannot sign in until you invite them again."), { actions: [{ label: "Cancel", run: (x) => x() },
    { label: "Disable", kind: "danger", run: async (close) => { try { await api.post(`/admin/v1/tenants/${id}/users/${u.id}/disable`); close(); reload(); } catch (e) { toast(errText(e), true); } } }] });
  const addRecorder = async () => { try {
    const c = await api.post("/admin/v1/recorders", { tenant_id: id, brand: "dahua" });
    modal("Dahua recorder created", h("div", {}, h("dl", { class: "cred" }, copyRow("Server address", c.server), copyRow("Port", String(c.port)), copyRow("Sub-Device ID", c.device_id), copyRow("Viewer user", c.viewer_user || "giview"), copyRow("Viewer password", c.key)),
      note("The password is shown only once. Give these details to the customer or type them into the recorder yourself.", "warn")), { actions: [{ label: "Done", kind: "primary", run: (x) => { x(); reload(); } }] });
  } catch (e) { toast(errText(e), true); } };
  const userRows = users.items.map((u) => h("tr", {}, h("td", {}, u.name), h("td", { class: "muted" }, u.email), h("td", {}, u.role === "admin" ? "Administrator" : "Viewer"),
    h("td", {}, u.status === "active" ? chip("Active", "ok") : u.status === "invited" ? chip("Invited", "warn") : chip("Disabled", "crit")), h("td", { class: "muted" }, u.last_login_at ? ago(u.last_login_at) : "never"),
    h("td", {}, can("owner", "technician", "support") && u.status !== "disabled" ? [h("button", { class: "btn sm", onclick: () => resetLink(u) }, u.status === "invited" ? "New invite link" : "Reset password"), " ",
      can("owner", "support") ? h("button", { class: "btn sm danger", onclick: () => disable(u) }, "Disable") : null] : null)));
  return [top(t.name, chip(l, k), t.status === "suspended" ? (can("owner") ? h("button", { class: "btn", onclick: () => reason("Reactivate " + t.name, `/admin/v1/tenants/${id}/reactivate`, "Reactivate") }, "Reactivate") : null)
    : (can("owner") ? h("button", { class: "btn danger", onclick: () => reason("Suspend " + t.name, `/admin/v1/tenants/${id}/suspend`, "Suspend") }, "Suspend") : null)),
  h("div", { class: "grid" }, stat(t.plan_id, "Plan"), stat(`${t.cameras_in_use} of ${t.camera_limit}`, "Cameras"), stat(`${t.recorders_online} of ${t.recorders_total}`, "Recorders online"), stat(t.city || "—", "City")),
  t.suspended_reason ? note("Suspended: " + t.suspended_reason, "warn") : null,
  h("div", { class: "card" }, h("div", { class: "row sp" }, h("h2", {}, "People"), can("owner", "technician", "support") ? h("button", { class: "btn sm primary", onclick: invite }, "Invite a user") : null),
    users.items.length ? h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Name", "Email", "Role", "Status", "Last sign-in", ""].map((x) => h("th", {}, x)))), h("tbody", {}, userRows))) : h("div", { class: "empty" }, "No users yet. Invite the first one so they can sign in.")),
  h("div", { class: "card" }, h("div", { class: "row sp" }, h("h2", {}, "Recorders"), can("owner", "technician") ? h("button", { class: "btn sm primary", onclick: addRecorder }, "Add Dahua recorder") : null),
    recs.length ? h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Device ID", "Brand", "Status", "Channels", "Last seen", "Address"].map((x) => h("th", {}, x)))),
      h("tbody", {}, recs.map((r) => { const [a, b] = LINK[r.status] || LINK.never; return h("tr", {}, h("td", { class: "mono" }, r.device_id), h("td", {}, r.brand), h("td", {}, chip(a, b)), h("td", { class: "num" }, r.channel_count), h("td", { class: "muted" }, ago(r.last_heartbeat_at)), h("td", { class: "mono muted" }, r.last_ip || "")); })))) : h("div", { class: "empty" }, "No recorders yet.")),
  h("div", { class: "card" }, h("h2", {}, "Cameras"), cams.cameras.length ? h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Camera", "Counts", "State", "Analysis", "Dropped", "Last activity", "Problem"].map((x) => h("th", {}, x)))),
    h("tbody", {}, cams.cameras.map((c) => { const [a, b] = c.enabled ? STATE[c.stream_state] || STATE.unknown : ["Paused", ""]; return h("tr", {}, h("td", {}, c.name), h("td", {}, [c.use_footfall ? chip("People", "acc") : null, " ", c.use_anpr ? chip("Plates", "acc") : null]),
      h("td", {}, chip(a, b)), h("td", { class: "num" }, c.analysis_fps ? c.analysis_fps.toFixed(1) + " fps" : "—"), h("td", { class: "num" }, c.frames_dropped ?? "—"), h("td", { class: "muted" }, ago(c.last_frame_at)), h("td", { class: "muted small" }, c.stream_error || "")); })))) : h("div", { class: "empty" }, "No cameras yet."))];
}

boot();
