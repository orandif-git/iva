// Staff console screens: Today, Customers, Fleet and More. The rest are in money.js, messaging.js and admin.js.
import { clear, errText, fmtNum, h, mount, qs } from "../lib.js";
import { chips, empty, field, formSheet, ic, kpi, note, openSheet, closeSheet, pill, reasonSheet, sec, seg, skeleton, toast, toggle } from "../app/ui.js";
import { ago, back, CUS_STATUS, fmtDateTime, gh, group, hero, initials, kv, money, REC_STATUS, row, statusPill, title, cap, INV_STATUS } from "./common.js";
import { moneyScreens, invoiceSheet } from "./money.js";
import { messagingScreens } from "./messaging.js";
import { adminScreens } from "./admin.js";

export const TITLES = { today: "Today", customers: "Customers", fleet: "Fleet", billing: "Billing", plans: "Plans", messaging: "Messaging", maintenance: "Maintenance", storage: "Storage", audit: "Audit log", staff: "Staff and roles", more: "More" };

const noaccess = () => empty("lock", "Not available for your role", "Ask an owner if you need this.");

// ------------------------------------------------------------------------------------------------------ Today
const snoozed = () => { try { const d = JSON.parse(localStorage.getItem("gi_staff_snooze") || "{}"); return d.day === new Date().toDateString() ? d.ids || [] : []; } catch { return []; } };
const snooze = (id) => { try { localStorage.setItem("gi_staff_snooze", JSON.stringify({ day: new Date().toDateString(), ids: [...snoozed(), id] })); } catch { /* private mode */ } };

function route(X, item) {
  const t = item.target || {};
  if (t.type === "recorder") X.go("#/fleet/" + t.id);
  else if (t.type === "customer") X.go("#/customers/" + t.id);
  else if (t.type === "invoice") X.go("#/billing");
  else if (t.type === "messaging") X.go("#/messaging");
}

function attentionRow(X, a, redraw) {
  const kind = a.severity === "critical" ? "crit" : a.severity === "warning" ? "warn" : "";
  const icon = { recorder_offline: "server", recorder_never_connected: "server", invoice_overdue: "card", trial_ending: "users", messages_failed: "chat" }[a.kind] || "bell";
  const r = h("button", { class: "row", type: "button", style: null, onclick: () => { if (!r._moved) route(X, a); } }, h("span", { class: "ico " + kind }, ic(icon)), h("div", {}, h("b", {}, a.title), h("small", {}, a.subtitle)), h("time", {}, a.age));
  const wrap = h("div", { class: "swipe" }, h("div", { class: "act", style: null }, "Snooze"), r);
  let x0 = null, dx = 0;
  r.addEventListener("pointerdown", (e) => { if (X.desk()) return; x0 = e.clientX; dx = 0; r._moved = false; });
  r.addEventListener("pointermove", (e) => { if (x0 === null) return; dx = Math.min(0, e.clientX - x0); if (Math.abs(dx) > 6) r._moved = true; wrap.classList.add("sw"); r.style.transition = "none"; r.style.transform = `translateX(${Math.max(dx, -100)}px)`; });
  const end = () => { if (x0 === null) return; x0 = null; r.style.transition = ""; wrap.classList.remove("sw"); if (dx < -70) { snooze(a.id); toast("Snoozed for today"); redraw(); } else r.style.transform = ""; setTimeout(() => { r._moved = false; }, 0); };
  r.addEventListener("pointerup", end); r.addEventListener("pointercancel", end);
  return wrap;
}

async function today(X) {
  const { api } = X;
  const [ov, att, log] = await Promise.all([api.get("/admin/v1/overview"), api.get("/admin/v1/overview/attention"), api.get("/admin/v1/audit?limit=4").catch(() => ({ items: [] }))]);
  const tenants = ov.tenants || {}, total = Object.values(tenants).reduce((a, b) => a + b, 0);
  const rec = ov.recorders || {}, recTotal = Object.values(rec).reduce((a, b) => a + b, 0);
  const states = ov.cameras_by_state || {}, camTotal = Object.values(states).reduce((a, b) => a + b, 0) || 1;
  const open = att.items.filter((a) => !snoozed().includes(a.id));
  const list = h("div");
  const redraw = () => { const o = att.items.filter((a) => !snoozed().includes(a.id)); mount(list, o.length ? h("div", { class: "card feed", style: null }, o.map((a) => attentionRow(X, a, redraw))) : empty("check", "All clear", "Nothing needs a person right now.")); counter.textContent = o.length ? `${o.length} open` : "All clear"; };
  const counter = h("span", { class: "sub" }, "");
  const out = [title("Today", "Jas iVision operations")];
  out.push(h("div", { class: "kpis" },
    h("a", { class: "kpi", href: "#/customers" }, h("span", {}, "Customers"), h("strong", {}, fmtNum(total)), h("small", {}, `${tenants.active || 0} active · ${tenants.trial || 0} trial · ${tenants.past_due || 0} past due`)),
    h("a", { class: "kpi", href: "#/fleet" }, h("span", {}, "Cameras in use"), h("strong", {}, fmtNum(ov.cameras_in_use)), h("small", {}, `of ${fmtNum(ov.cameras_licensed)} licensed`)),
    h("a", { class: "kpi", href: "#/fleet" }, h("span", {}, "Recorders online"), h("strong", {}, `${rec.online || 0}`, h("span", { class: "of" }, ` of ${recTotal}`)), h("small", {}, `${rec.offline || 0} offline · ${rec.never || 0} never connected`)),
    X.isOwner ? h("a", { class: "kpi", href: "#/billing" }, h("span", {}, "Monthly revenue"), h("strong", {}, money(ov.mrr_paise)), h("small", {}, "from active cameras"))
      : h("div", { class: "kpi" }, h("span", {}, "Monthly revenue"), h("strong", { class: "sub" }, ic("lock"), " Owner only"), h("small", {}, "Hidden for your role"))));
  out.push(sec("Needs attention", counter), list);
  redraw();
  const bar = (label, n, color) => h("div", {}, h("div", { class: "bl" }, h("b", {}, label), h("span", { class: "muted" }, fmtNum(n))), h("div", { class: "bt" }, h("i", { style: null, class: color, "data-w": Math.round((n / camTotal) * 100) })));
  const fleetCard = h("div", { class: "card" }, h("div", { class: "pagehead" }, h("h3", {}, "Cameras right now"), pill(`${Math.round(((states.streaming || 0) / camTotal) * 100)}% streaming`, "ok")),
    h("div", { class: "bars", style: null }, bar("Streaming", states.streaming || 0, "ok"), bar("Reconnecting", states.reconnecting || 0, "warn"), bar("Offline", states.offline || 0, "crit"), bar("Waiting for a first picture", states.unknown || 0, "")));
  fleetCard.querySelectorAll("i[data-w]").forEach((i) => { i.style.width = i.getAttribute("data-w") + "%"; i.style.background = i.className === "ok" ? "var(--ok)" : i.className === "warn" ? "var(--warn)" : i.className === "crit" ? "var(--crit)" : "var(--line)"; });
  const feed = h("div", { class: "card feed" }, h("div", { class: "pagehead" }, h("h3", {}, "Latest activity"), h("a", { class: "link", href: "#/audit" }, "All")),
    log.items.length ? log.items.map((a) => h("div", { class: "row" }, h("span", { class: "ico" }, ic("user")), h("div", {}, h("b", {}, humanAction(a.action)), h("small", {}, `${a.actor_name || "System"}${a.reason ? " · " + a.reason : ""}`)), h("time", {}, ago(a.ts)))) : h("p", { class: "sub" }, "Nothing yet."));
  out.push(h("div", { class: "cols", style: null }, fleetCard, feed));
  return out;
}
export const humanAction = (a) => ({ "tenant.create": "Created a customer", "tenant.update": "Edited a customer", "tenant.suspend": "Suspended a customer", "tenant.reactivate": "Reactivated a customer", "plan.change": "Changed a customer's plan",
  "impersonate.start": "Opened a customer's app", "impersonate.end": "Closed a customer's app", "user.invite": "Invited a person", "user.reset_link": "Made a sign-in link", "user.disable": "Disabled a person", "user.enable": "Enabled a person",
  "user.update": "Edited a person", "user.delete": "Deleted a person", "plan.create": "Created a plan", "plan.update": "Changed a plan", "plan.delete": "Deleted a plan", "staff.create": "Added a staff member", "staff.update": "Changed a staff member",
  "staff.disable": "Disabled a staff member", "staff.enable": "Enabled a staff member", "staff.reset_link": "Made a staff sign-in link", "maintenance.retention.run": "Ran data retention",
  "messaging.provider.update": "Changed a messaging provider", "messaging.provider.create": "Added a messaging provider", "messaging.provider.test": "Tested a messaging provider", "messaging.tenant.update": "Changed a customer's messaging" }[a]
  || cap(String(a).replace(/[._]/g, " ")));

// ---------------------------------------------------------------------------------------------------- Customers
const custRow = (c, onOpen, plans = {}) => row({ initials: initials(c.name), title: c.name, sub: `${c.city || "No city"} · ${plans[c.plan_id] || c.plan_id} · ${c.cameras_in_use} of ${c.camera_limit} cameras`, onclick: () => onOpen(c), right: h("span", { style: null, class: "rowend" }, statusPill(CUS_STATUS, c.status), ic("chev", "chev")) });

async function customers(X) {
  const st = X.mem.cus ||= { q: "", filter: "all" };
  const [r, pl] = await Promise.all([X.api.get("/admin/v1/tenants?limit=200"), X.api.get("/admin/v1/plans?include_retired=true")]);
  const items = r.items, plans = Object.fromEntries(pl.items.map((x) => [x.id, x.name]));
  const list = h("div", { class: "group" });
  const draw = () => {
    const q = st.q.toLowerCase(), l = items.filter((c) => (st.filter === "all" || c.status === st.filter) && (!q || (c.name + " " + (c.city || "")).toLowerCase().includes(q)));
    mount(list, l.length ? l.map((c) => custRow(c, () => X.go("#/customers/" + c.id), plans)) : h("div", { class: "gi static" }, h("div", { class: "muted" }, "No customers match.")));
  };
  const search = h("input", { type: "search", placeholder: "Search customers", value: st.q, "aria-label": "Search customers", oninput: (e) => { st.q = e.target.value; draw(); } });
  draw();
  const filter = chips([["all", "All"], ["active", "Active"], ["trial", "Trial"], ["past_due", "Past due"], ["suspended", "Suspended"]], st.filter, (v) => { st.filter = v; draw(); });
  return [title("Customers", `${items.length} customers`, X.isOwner ? h("button", { class: "btn sm sec2", type: "button", onclick: () => newCustomer(X) }, ic("plus"), "New") : null),
    h("label", { class: "search" }, ic("search"), search), h("div", { style: null, class: "gap" }, filter), list];
}

async function newCustomer(X) {
  const plans = (await X.api.get("/admin/v1/plans")).items;
  const name = h("input", { type: "text", maxlength: 120, placeholder: "Sharma Auto Works" }), city = h("input", { type: "text", maxlength: 120, placeholder: "Sanand" });
  const plan = h("select", {}, plans.map((p) => h("option", { value: p.id }, `${p.name} · ${money(p.price_per_camera_paise)} per camera`)));
  const limit = h("input", { type: "number", min: 0, max: 5000, value: 5, inputmode: "numeric" });
  formSheet("New customer", [field("Business name", name), field("City", city), field("Plan", plan), field("Camera limit", limit, "How many cameras they can switch on."), note("They start on a 14 day trial. After this, invite their first administrator from their page.")], "Create customer", async () => {
    if (!name.value.trim()) throw new Error("Type the business name.");
    const c = await X.api.post("/admin/v1/tenants", { name: name.value.trim(), city: city.value.trim() || undefined, plan_id: plan.value, camera_limit: Number(limit.value) });
    closeSheet(); toast("Customer created"); X.go("#/customers/" + c.id);
  });
}

/** The customer page, used in the inspector (desktop) and in a sheet (phone). */
export async function customerDetail(X, id, ds, redraw) {
  const { api } = X;
  const [c, hl, plans] = await Promise.all([api.get(`/admin/v1/tenants/${id}`), api.get(`/admin/v1/tenants/${id}/health`), api.get("/admin/v1/plans?include_retired=true")]);
  const plan = plans.items.find((p) => p.id === c.plan_id);
  const after = async () => { redraw(); X.refresh(); };
  const tabs = [["ov", "Overview"], ["pp", "People"], ["dv", "Devices"], ["ms", "Messaging"], ...(X.isOwner ? [["bl", "Billing"]] : [])];
  const tabBar = seg(tabs, ds.tab, (v) => { ds.tab = v; redraw(); });
  let body;
  if (ds.tab === "ov") {
    body = group(kv("Status", statusPill(CUS_STATUS, c.status)), kv("Plan", `${plan ? plan.name : c.plan_id}`, c.price_override_paise != null ? `Keeps an older price: ${money(c.price_override_paise)} per camera` : plan ? `${money(plan.price_per_camera_paise)} per camera per month` : null),
      kv("Camera limit", `${c.cameras_in_use} of ${c.camera_limit} in use`), kv("Cameras streaming", `${hl.cameras_streaming} of ${hl.cameras_enabled}`), kv("Recorders online", `${c.recorders_online} of ${c.recorders_total}`),
      kv("Last picture", hl.last_frame_at ? ago(hl.last_frame_at) : "never"), kv("Last sign-in", hl.last_sign_in_at ? ago(hl.last_sign_in_at) : "never"), kv("Alerts, last 7 days", String(hl.alerts_7d)), kv("Failed messages, last 7 days", String(hl.messages_failed_7d)),
      kv("Customer since", fmtDateTime(c.created_at)), ...(c.status === "suspended" ? [kv("Suspended", c.suspended_reason || "—")] : []), ...(c.status === "trial" && c.trial_ends_at ? [kv("Trial ends", fmtDateTime(c.trial_ends_at))] : []));
  } else if (ds.tab === "pp") body = await peopleTab(X, c, after);
  else if (ds.tab === "dv") {
    const recs = await api.get(`/admin/v1/recorders?tenant_id=${id}`);
    body = h("div", {}, group(...(recs.length ? recs.map((r) => row({ icon: "server", title: r.name || r.device_id, sub: `${r.device_id} · ${r.channel_count} cameras`, onclick: () => { X.go("#/fleet/" + r.id); closeSheet(); }, right: h("span", { class: "rowend" }, statusPill(REC_STATUS, r.status), ic("chev", "chev")) })) : [h("div", { class: "gi static" }, h("div", { class: "muted" }, "No recorders yet."))])),
      h("p", { class: "sub", style: null }, "Customers connect and manage their own recorders. You can look at the details here."));
  } else if (ds.tab === "ms") body = await messagingTab(X, c, after);
  else if (ds.tab === "bl") {
    const inv = await api.get(`/admin/v1/billing/invoices?tenant_id=${id}`);
    body = group(...(inv.length ? inv.map((i) => row({ icon: "doc", title: money(i.total_paise), sub: `${i.number || "Draft"} · ${i.period_month.slice(0, 7)}`, right: h("span", { class: "rowend" }, statusPill(INV_STATUS, i.overdue ? "overdue" : i.status), ic("chev", "chev")), onclick: () => invoiceSheet(X, i, after) })) : [h("div", { class: "gi static" }, h("div", { class: "muted" }, "No invoices yet."))]));
  }
  const actions = [];
  if (X.isOwner || X.state.me.role === "support") actions.push(row({ icon: "eye", title: `Open ${c.name.split(" ")[0]}'s app`, sub: "See what they see, view only. A reason is required and logged.", onclick: () => openCustomerApp(X, c) }));
  if (X.isOwner) {
    actions.push(row({ icon: "edit", title: "Edit business details", sub: c.city || "", onclick: () => editDetails(X, c, after) }));
    actions.push(row({ icon: "card", title: "Plan and camera limit", sub: `${plan ? plan.name : c.plan_id} · ${c.camera_limit} cameras`, onclick: () => planSheet(X, c, plans.items, after) }));
    actions.push(c.status === "suspended" ? row({ icon: "check", title: "Reactivate", sub: "Let them sign in and bring cameras back", onclick: () => reactivate(X, c, after) })
      : row({ icon: "pause", title: "Suspend", sub: "Stops sign-in, analysis and alerts. Nothing is deleted.", danger: true, onclick: () => suspend(X, c, after) }));
  }
  return [hero("users", c.name, `${c.city || "No city"} · ${CUS_STATUS[c.status]?.[0] || c.status}`, null, initials(c.name)), tabBar, body, gh("Actions"), group(...actions)];
}

async function peopleTab(X, c, after) {
  const users = (await X.api.get(`/admin/v1/tenants/${c.id}/users`)).items;
  const st = { Active: "ok", invited: "warn", disabled: "crit", active: "ok" };
  const label = { active: "Active", invited: "Invited", disabled: "Disabled" };
  return h("div", {}, group(...(users.length ? users.map((u) => row({ initials: initials(u.name), title: u.name, sub: `${u.role === "admin" ? "Administrator" : "Regular"} · ${u.email}`, right: h("span", { class: "rowend" }, pill(label[u.status] || u.status, st[u.status] || ""), ic("chev", "chev")), onclick: () => personSheet(X, c, u, after) })) : [h("div", { class: "gi static" }, h("div", { class: "muted" }, "No one yet. Invite the first administrator."))])),
    h("div", { class: "acts2" }, h("button", { class: "btn sec2 block", type: "button", onclick: () => invite(X, c, after) }, ic("plus"), "Invite a person")));
}

function linkSheet(title, link, text) {
  openSheet(title, h("p", {}, text), h("dl", { class: "cred" }, h("dt", {}, "Link"), h("dd", {}, link), h("button", { class: "btn sm gray", type: "button", "aria-label": "Copy link", onclick: async () => { try { await navigator.clipboard.writeText(link); toast("Link copied"); } catch { toast("Select the link and copy it by hand.", true); } } }, ic("copy"))),
    h("div", { class: "acts" }, h("button", { class: "btn", type: "button", onclick: () => closeSheet() }, "Done")));
}

function invite(X, c, after) {
  const name = h("input", { type: "text", maxlength: 120 }), email = h("input", { type: "email", autocomplete: "off" }), phone = h("input", { type: "tel", maxlength: 20 });
  const role = h("select", {}, h("option", { value: "admin" }, "Administrator"), h("option", { value: "regular" }, "Regular"));
  formSheet("Invite a person", [field("Name", name), field("Email", email), field("Mobile number (optional)", phone), field("Role", role, "Administrators connect recorders and manage cameras.")], "Create invitation", async () => {
    const r = await X.api.post(`/admin/v1/tenants/${c.id}/users`, { name: name.value.trim(), email: email.value.trim(), role: role.value, ...(phone.value.trim() ? { phone: phone.value.trim() } : {}) });
    after(); linkSheet("Invitation link", r.link, "Send this to them, for example on WhatsApp. It works once. It is not shown again.");
  });
}

function personSheet(X, c, u, after) {
  const back = () => after();
  const rows = [row({ icon: "link", title: "Make a new sign-in link", sub: "Their old password stops working. The link works once.", onclick: () => reasonSheet({ title: `New sign-in link for ${u.name}?`, text: "Their old password stops working. Send them the link by WhatsApp or phone.", label: "Make link", run: async (reason) => { const r = await X.api.call("POST", `/admin/v1/tenants/${c.id}/users/${u.id}/reset-link${qs({ reason })}`, {}); linkSheet("Sign-in link", r.link, "Copy it now. It is not shown again and expires in 24 hours."); } }) })];
  if (X.isOwner) {
    rows.push(row({ icon: "edit", title: "Edit details", sub: "Name, email and role", onclick: () => editPerson(X, c, u, after) }));
    rows.push(u.status === "disabled" ? row({ icon: "check", title: "Enable", sub: "Let them sign in again", onclick: () => reasonSheet({ title: `Enable ${u.name}?`, text: "They can sign in again with a new sign-in link.", label: "Enable", run: async (reason) => { await X.api.call("POST", `/admin/v1/tenants/${c.id}/users/${u.id}/enable${qs({ reason })}`, {}); closeSheet(); toast(`${u.name} enabled`); back(); } }) })
      : row({ icon: "pause", title: "Disable", sub: "Signs them out everywhere", onclick: () => reasonSheet({ title: `Disable ${u.name}?`, text: "They are signed out everywhere and cannot sign in until you enable them again.", label: "Disable", danger: true, run: async (reason) => { await X.api.call("POST", `/admin/v1/tenants/${c.id}/users/${u.id}/disable${qs({ reason })}`, {}); closeSheet(); toast(`${u.name} disabled`); back(); } }) }));
    rows.push(row({ icon: "trash", title: "Delete person", sub: "Removes their access for good", danger: true, onclick: () => reasonSheet({ title: `Delete ${u.name}?`, text: "Their access is removed for good. What they did in the app, such as alerts they acknowledged, stays on record under their name.", label: "Delete person", danger: true, run: async (reason) => { await X.api.del(`/admin/v1/tenants/${c.id}/users/${u.id}${qs({ reason })}`); closeSheet(); toast("Person deleted"); back(); } }) }));
  }
  openSheet(u.name, group(kv("Email", u.email), kv("Role", u.role === "admin" ? "Administrator" : "Regular"), kv("Status", u.status), kv("Last sign-in", u.last_login_at ? ago(u.last_login_at) : "never")), group(...rows), X.isOwner ? null : h("p", { class: "sub" }, "Editing, disabling and deleting people is for owners."));
}

function editPerson(X, c, u, after) {
  const name = h("input", { type: "text", value: u.name }), email = h("input", { type: "email", value: u.email }), phone = h("input", { type: "tel", value: u.phone || "" });
  const role = h("select", {}, h("option", { value: "admin", selected: u.role === "admin" }, "Administrator"), h("option", { value: "regular", selected: u.role === "regular" }, "Regular"));
  formSheet("Edit details", [field("Name", name), field("Email", email, "Changing the email signs them out everywhere."), field("Mobile number", phone), field("Role", role)], "Save", async () => {
    await X.api.patch(`/admin/v1/tenants/${c.id}/users/${u.id}`, { name: name.value.trim(), email: email.value.trim(), role: role.value, ...(phone.value.trim() ? { phone: phone.value.trim() } : {}) });
    closeSheet(); toast("Details saved"); after();
  });
}

function editDetails(X, c, after) {
  const name = h("input", { type: "text", value: c.name, maxlength: 120 }), city = h("input", { type: "text", value: c.city || "", maxlength: 120 });
  formSheet("Business details", [field("Business name", name), field("City", city)], "Save", async () => { await X.api.patch(`/admin/v1/tenants/${c.id}`, { name: name.value.trim(), city: city.value.trim() || undefined }); closeSheet(); toast("Details saved"); after(); });
}

function planSheet(X, c, plans, after) {
  let pick = c.plan_id;
  const limit = h("input", { type: "number", min: 0, max: 5000, value: c.camera_limit, inputmode: "numeric" });
  const list = h("div", { class: "group" });
  const draw = () => mount(list, plans.filter((p) => p.active || p.id === c.plan_id).map((p) => h("button", { class: "gi", type: "button", onclick: () => { pick = p.id; draw(); } },
    h("div", {}, h("b", {}, p.name), h("small", {}, `${money(p.price_per_camera_paise)} per camera · ${p.stream_mode === "snapshots" ? "pictures every few seconds" : "live counting"}${p.anpr ? " · number plates" : ""}`)), pick === p.id ? ic("check") : h("span"))));
  draw();
  formSheet("Plan and camera limit", [list, field("Camera limit", limit, `${c.cameras_in_use} cameras are in use, so the limit cannot go below that.`), note("Takes effect now. The next invoice is pro-rated. Moving to another plan ends any older price they were keeping.")], "Save", async () => {
    if (pick !== c.plan_id) { await new Promise((ok, no) => reasonSheet({ title: "Change plan?", text: "This changes what they pay and what they can use.", label: "Change plan", run: async (reason) => { try { await X.api.post(`/admin/v1/tenants/${c.id}/plan`, { plan_id: pick, reason }); ok(); } catch (e) { no(e); throw e; } } })); }
    if (Number(limit.value) !== c.camera_limit) await X.api.patch(`/admin/v1/tenants/${c.id}`, { camera_limit: Number(limit.value) });
    closeSheet(); toast("Saved"); after();
  });
}

const suspend = (X, c, after) => reasonSheet({ title: `Suspend ${c.name}?`, text: "Their people cannot sign in, cameras stop being analysed and no alerts are sent. Nothing is deleted and you can reactivate any time.", label: "Suspend", danger: true,
  run: async (reason) => { await X.api.post(`/admin/v1/tenants/${c.id}/suspend`, { reason }); closeSheet(); toast(`${c.name} suspended`); after(); } });
const reactivate = (X, c, after) => reasonSheet({ title: `Reactivate ${c.name}?`, text: "They can sign in again and their cameras start being analysed.", label: "Reactivate",
  run: async (reason) => { await X.api.post(`/admin/v1/tenants/${c.id}/reactivate`, { reason }); closeSheet(); toast(`${c.name} reactivated`); after(); } });

function openCustomerApp(X, c) {
  // The window must be opened by the click that confirms, so the button below starts it.
  const why = h("input", { type: "text", placeholder: "For example: customer says plate reads are missing", autocomplete: "off", "aria-label": "Reason" });
  const err = h("div"), btn = h("button", { class: "btn", type: "button", disabled: true }, "Open customer app");
  why.oninput = () => { btn.disabled = why.value.trim().length < 5; };
  btn.onclick = async () => { btn.disabled = true; clear(err); try { await X.viewAs(c.id, why.value.trim()); closeSheet(); toast("Opened in a new tab. View only for 30 minutes."); } catch (e) { err.append(note(errText(e), "err")); btn.disabled = false; } };
  openSheet(`Open ${c.name}'s app?`, h("p", {}, "You will see exactly what they see, view only, for up to 30 minutes. Every page you open is logged under your name."), field("Reason (saved in the audit log)", why), err, h("div", { class: "acts" }, btn, h("button", { class: "btn gray", type: "button", onclick: () => closeSheet() }, "Cancel")));
}

async function messagingTab(X, c, after) {
  const m = await X.api.get(`/admin/v1/messaging/tenants/${c.id}`);
  const cur = { whatsapp: m.whatsapp, email: m.email, clips: m.clips, monthly_cap: m.monthly_cap };
  const save = (key, input) => async () => {
    const next = { ...cur, [key]: input.checked };
    reasonSheet({ title: "Change messaging?", text: `This changes what ${c.name} can send.`, label: "Save", run: async (reason) => { await X.api.put(`/admin/v1/messaging/tenants/${c.id}`, { ...next, reason }); cur[key] = input.checked; closeSheet(); toast("Saved"); after(); } });
    // if the sheet is dismissed the switch goes back
    input.checked = cur[key];
  };
  const sw = (key, label, sub) => { const t = toggle(cur[key], () => {}, label); const input = t.querySelector("input"); input.disabled = !X.isOwner; input.onchange = () => { const want = input.checked; reasonSheet({ title: `${want ? "Turn on" : "Turn off"} ${label.toLowerCase()}?`, text: `This changes what ${c.name} can send.`, label: "Save", run: async (reason) => { await X.api.put(`/admin/v1/messaging/tenants/${c.id}`, { ...cur, [key]: want, reason }); cur[key] = want; closeSheet(); toast("Saved"); after(); } }); input.checked = cur[key]; }; return h("div", { class: "gi static" }, h("div", {}, h("b", {}, label), h("small", {}, sub)), t); };
  void save;
  return h("div", {}, group(sw("whatsapp", "WhatsApp alerts", "Messages through the platform WhatsApp number"), sw("email", "Email alerts", "Alerts and daily reports by email"), sw("clips", "Video clips on alerts", "A short clip with blocked-vehicle alerts"),
    kv("Monthly WhatsApp cap", m.monthly_cap == null ? "No cap" : String(m.monthly_cap), `${m.whatsapp_this_month} sent this month`)),
    X.isOwner ? h("div", { class: "acts2" }, h("button", { class: "btn gray block", type: "button", onclick: () => capSheet(X, c, cur, after) }, "Change monthly cap")) : h("p", { class: "sub" }, "Only an owner can change these."),
    group(row({ icon: "chat", title: "Delivery log for this customer", sub: "What was sent and what failed", onclick: () => { X.mem.logTenant = c.id; closeSheet(); X.go("#/messaging"); } })),
    h("p", { class: "sub" }, "Who receives each alert is managed by the customer in their own app."));
}

function capSheet(X, c, cur, after) {
  const cap = h("input", { type: "number", min: 0, value: cur.monthly_cap ?? "", placeholder: "No cap", inputmode: "numeric" });
  reasonSheet({ title: "Monthly WhatsApp cap", text: "The most WhatsApp messages this customer can send in a month. Leave empty for no cap.", label: "Save", extra: field("Messages per month", cap),
    run: async (reason) => { await X.api.put(`/admin/v1/messaging/tenants/${c.id}`, { ...cur, monthly_cap: cap.value === "" ? null : Number(cap.value), reason }); closeSheet(); toast("Saved"); after(); } });
}

// ------------------------------------------------------------------------------------------------------ Fleet
async function fleet(X) {
  const st = X.mem.fleet ||= { f: "all" };
  const recs = await X.api.get("/admin/v1/recorders?limit=500");
  const online = recs.filter((r) => r.status === "online").length;
  const list = h("div", { class: "group" });
  const draw = () => { const l = recs.filter((r) => st.f === "all" || r.status === st.f); mount(list, l.length ? l.map((r) => row({ icon: "server", title: r.name || r.device_id, sub: `${r.tenant_name} · ${r.device_id}`, right: h("span", { class: "rowend" }, statusPill(REC_STATUS, r.status), ic("chev", "chev")), onclick: () => X.go("#/fleet/" + r.id) })) : h("div", { class: "gi static" }, h("div", { class: "muted" }, "None."))); };
  draw();
  const filter = chips([["all", "All"], ["online", "Online"], ["offline", "Offline"], ["never", "Waiting"]], st.f, (v) => { st.f = v; draw(); });
  return [title("Fleet", `${recs.length} recorders · ${online} online · read only`), h("div", { class: "gap" }, filter), list, h("p", { class: "sub" }, "Customers connect and manage their own recorders. This list is for diagnosing problems.")];
}

export async function recorderDetail(X, id) {
  const [recs, conn] = await Promise.all([X.api.get("/admin/v1/recorders?limit=500"), X.api.get(`/admin/v1/recorders/${id}/connection`)]);
  const r = recs.find((x) => x.id === id);
  if (!r) return [note("That recorder was not found.", "err")];
  const hint = r.status === "offline" ? note(`Last heard ${ago(r.last_heartbeat_at)}. Usual causes: the recorder is off, its internet is down, or its password was changed.`, "warn")
    : r.status === "never" ? note("This recorder has never reached us. The customer should check Network, Register and the viewer user on the recorder.", "warn") : null;
  const copy = (label, value) => [h("dt", {}, label), h("dd", {}, value), h("button", { class: "btn sm gray", type: "button", "aria-label": "Copy " + label, onclick: async () => { try { await navigator.clipboard.writeText(value); toast(label + " copied"); } catch { toast("Select the text and copy it by hand.", true); } } }, ic("copy"))];
  return [hero("server", r.name || r.device_id, `${r.tenant_name} · ${REC_STATUS[r.status]?.[0] || r.status}`), hint,
    group(kv("Cameras found", String(r.channel_count)), kv("Last heard", r.last_heartbeat_at ? ago(r.last_heartbeat_at) : "never"), kv("Firmware", r.firmware || "—"), kv("Public address", r.last_ip || "—"), kv("Brand", `${cap(r.brand)} · ${conn.protocol}`)),
    gh("Connection details"), h("dl", { class: "cred" }, copy("Server", conn.server), copy("Port", String(conn.port)), copy("Sub-Device ID", conn.device_id), copy("Viewer user", conn.viewer_user || "giview")),
    h("p", { class: "sub" }, "The password is never shown again. The customer can make a new one."),
    group(row({ icon: "users", title: `Go to ${r.tenant_name}`, sub: "Their plan, people and devices", onclick: () => { closeSheet(); X.go("#/customers/" + r.tenant_id); } })), h("p", { class: "sub" }, "Recorders are changed by the customer, not here.")];
}

// ------------------------------------------------------------------------------------------------------- More
async function more(X) {
  const me = X.state.me;
  const items = [["plans", "Plans", "tag", "Prices and what each plan includes", X.isOwner], ["storage", "Storage", "cloud", "Where evidence pictures and clips are kept", X.isOwner], ["messaging", "Messaging", "chat", "Email and WhatsApp", true], ["maintenance", "Maintenance", "wrench", "Data retention and job runs", X.isOwner], ["audit", "Audit log", "log", "Every staff action", true], ["staff", "Staff and roles", "shield", "Who can do what", X.isOwner]].filter((i) => i[4]);
  const cur = X.theme.get();
  return [title("More", `${me.name} · ${me.role}`), group(...items.map((i) => row({ icon: i[2], title: i[1], sub: i[3], href: "#/" + i[0] }))),
    gh("Appearance"), h("div", { class: "card" }, seg([["light", "Light"], ["auto", "Auto"], ["dark", "Dark"]], cur, (v) => X.theme.set(v))),
    gh("Account"), group(row({ icon: "x", title: "Sign out", sub: "Staff sessions also end when the window closes", danger: true, onclick: () => X.signOut() }))];
}

export const SCREENS = { today, customers, fleet, more, ...moneyScreens, ...messagingScreens, ...adminScreens };
export { noaccess, back, chips, skeleton, sec };
