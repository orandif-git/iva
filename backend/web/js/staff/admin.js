// Maintenance, Audit log, and Staff and roles.
import { clear, errText, fmtNum, h, mount, qs } from "../lib.js";
import { chips, empty, field, formSheet, ic, note, openSheet, closeSheet, pill, reasonSheet, toast } from "../app/ui.js";
import { ago, back, cap, fmtDateTime, gh, group, initials, kv, row, title } from "./common.js";

const noaccess = () => empty("lock", "Not available for your role", "Ask an owner if you need this.");
const ACTIONS = { "tenant.create": "Created a customer", "tenant.update": "Edited a customer", "tenant.suspend": "Suspended a customer", "tenant.reactivate": "Reactivated a customer", "plan.change": "Changed a customer's plan",
  "impersonate.start": "Opened a customer's app", "impersonate.end": "Closed a customer's app", "user.invite": "Invited a person", "user.reset_link": "Made a sign-in link", "user.disable": "Disabled a person", "user.enable": "Enabled a person",
  "user.update": "Edited a person", "user.delete": "Deleted a person", "plan.create": "Created a plan", "plan.update": "Changed a plan", "plan.delete": "Deleted a plan", "staff.create": "Added a staff member", "staff.update": "Changed a staff member",
  "staff.disable": "Disabled a staff member", "staff.enable": "Enabled a staff member", "staff.reset_link": "Made a staff sign-in link", "maintenance.retention.run": "Ran data retention" };
const human = (a) => ACTIONS[a] || cap(String(a).replace(/[._]/g, " "));

// ------------------------------------------------------------------------------------------------ Maintenance
async function maintenance(X) {
  if (!X.isOwner) return [title("Maintenance", ""), noaccess()];
  const [pv, runs] = await Promise.all([X.api.get("/admin/v1/maintenance/retention/preview"), X.api.get("/admin/v1/maintenance/runs?limit=12")]);
  const run = () => reasonSheet({ title: "Run data retention now?", text: `Deletes ${fmtNum(pv.footfall_rows)} count rows, ${fmtNum(pv.plate_reads)} plate reads, ${fmtNum(pv.alerts)} alerts and ${fmtNum(pv.pictures_and_clips)} pictures and clips that are past their customers' retention periods. This cannot be undone.`,
    label: "Delete old data", danger: true, run: async () => { await X.api.post("/admin/v1/maintenance/retention/run"); closeSheet(); toast("Retention started"); X.refresh(); } });
  return [back("More", "#/more"), title("Maintenance", "Keeping the database tidy"),
    gh("Data retention"), h("div", { class: "card" }, h("p", { class: "sub" }, "Counts, plate reads and pictures are deleted after each customer's retention period. Preview first."),
      h("div", { class: "group" }, kv("Count rows", fmtNum(pv.footfall_rows)), kv("Plate reads", fmtNum(pv.plate_reads)), kv("Alerts", fmtNum(pv.alerts)), kv("Pictures and clips", fmtNum(pv.pictures_and_clips)),
        kv("Events kept for", `${pv.events_days.shortest} to ${pv.events_days.longest} days`), kv("Pictures kept for", `${pv.pictures_days.shortest} to ${pv.pictures_days.longest} days`)),
      h("div", { class: "two gap" }, h("button", { class: "btn gray", type: "button", onclick: () => X.refresh() }, "Preview again"), h("button", { class: "btn danger", type: "button", onclick: run }, "Run now"))),
    gh("Recent job runs"), group(...(runs.length ? runs.map((r) => h("div", { class: "gi static" }, h("span", { class: "ico " + (r.ok ? "ok" : "crit") }, ic(r.ok ? "check" : "x")), h("div", {}, h("b", {}, cap(r.job)), h("small", {}, `${fmtDateTime(r.ran_at)} · ${Number(r.seconds).toFixed(1)} s`)), pill(r.ok ? "Done" : "Failed", r.ok ? "ok" : "crit"))) : [h("div", { class: "gi static" }, h("div", { class: "muted" }, "No runs yet."))]))];
}

// ---------------------------------------------------------------------------------------------------- Audit
async function audit(X) {
  const st = X.mem.aud ||= { q: "", range: "7" };
  const list = h("div", { class: "card feed" }), more = h("div", { class: "center" });
  let cursor = null;
  const since = () => new Date(Date.now() - Number(st.range) * 86400e3).toISOString();
  const load = async (reset) => {
    if (reset) { cursor = null; mount(list, h("p", { class: "sub" }, "Loading…")); }
    try {
      const r = await X.api.get(`/admin/v1/audit${qs({ q: st.q.trim(), since: since(), limit: 40, cursor })}`);
      cursor = r.next_cursor;
      if (reset) clear(list);
      if (reset && !r.items.length) list.append(h("p", { class: "sub" }, "Nothing matches."));
      r.items.forEach((a) => list.append(h("div", { class: "row" }, h("span", { class: "ico" }, ic("user")), h("div", {}, h("b", {}, human(a.action)), h("small", {}, `${a.actor_name || "System"}${a.actor_role ? " (" + a.actor_role + ")" : ""}${a.target_type ? " · " + a.target_type : ""}`), a.reason ? h("small", { class: "why" }, `“${a.reason}”`) : null), h("time", {}, ago(a.ts)))));
      mount(more, cursor ? h("button", { class: "btn gray", type: "button", onclick: () => load(false) }, "Show more") : null);
    } catch (e) { mount(list, note(errText(e), "err")); }
  };
  const q = h("input", { type: "search", placeholder: "Search by person, action or reason", value: st.q, "aria-label": "Search audit log" });
  let t; q.oninput = () => { st.q = q.value; clearTimeout(t); t = setTimeout(() => load(true), 350); };
  load(true);
  return [back("More", "#/more"), title("Audit log", "Every staff action, who did it and why. It cannot be edited."), h("label", { class: "search" }, ic("search"), q),
    h("div", { class: "gap" }, chips([["1", "Today"], ["7", "7 days"], ["30", "30 days"], ["365", "A year"]], st.range, (v) => { st.range = v; load(true); })), list, more];
}

// ------------------------------------------------------------------------------------------------------ Staff
const ROLE_ROWS = [["See customers, plans and device status", 1, 1], ["Create customers, change plans and limits", 1, 0], ["Suspend or reactivate a customer", 1, 0], ["Create, edit or retire plans and prices", 1, 0], ["Edit, disable or delete a customer's people", 1, 0],
  ["Invoices, payments and revenue", 1, 0], ["Messaging providers, templates, caps", 1, 0], ["See the delivery log", 1, 1], ["Send a customer a new sign-in link", 1, 1], ["Open a customer's app (reason, logged)", 1, 1], ["Run data retention", 1, 0], ["Manage staff", 1, 0]];

function link(title_, text, url) {
  openSheet(title_, h("p", {}, text), h("dl", { class: "cred" }, h("dt", {}, "Link"), h("dd", {}, url), h("button", { class: "btn sm gray", type: "button", "aria-label": "Copy link", onclick: async () => { try { await navigator.clipboard.writeText(url); toast("Link copied"); } catch { toast("Select the link and copy it by hand.", true); } } }, ic("copy"))),
    h("div", { class: "acts" }, h("button", { class: "btn", type: "button", onclick: () => closeSheet() }, "Done")));
}

function staffSheet(X, s, after) {
  const me = X.state.me.id === s.id;
  const rows = [row({ icon: "link", title: "Make a new sign-in link", sub: "Their old password stops working. The link works once.", onclick: () => reasonSheet({ title: `New sign-in link for ${s.name}?`, text: "Their old password stops working. Send the link by WhatsApp or phone.", label: "Make link", run: async (reason) => { const r = await X.api.call("POST", `/admin/v1/staff/${s.id}/reset-link${qs({ reason })}`, {}); link("Sign-in link", "Copy it now. It is not shown again.", r.link); } }) })];
  if (!me) {
    rows.push(row({ icon: "edit", title: "Change role", sub: s.role === "owner" ? "Owner: can do everything" : "Support: can look and help", onclick: () => {
      const role = h("select", {}, h("option", { value: "owner", selected: s.role === "owner" }, "Owner: can do everything"), h("option", { value: "support", selected: s.role === "support" }, "Support: can look and help"));
      formSheet("Change role", [field("Role", role)], "Save", async () => { await X.api.patch(`/admin/v1/staff/${s.id}`, { role: role.value }); closeSheet(); toast("Role changed"); after(); }); } }));
    rows.push(s.status === "disabled" ? row({ icon: "check", title: "Enable", sub: "Let them sign in again", onclick: () => reasonSheet({ title: `Enable ${s.name}?`, text: "They can sign in again.", label: "Enable", run: async (reason) => { await X.api.call("POST", `/admin/v1/staff/${s.id}/enable${qs({ reason })}`, {}); closeSheet(); toast("Enabled"); after(); } }) })
      : row({ icon: "pause", title: "Disable", sub: "Signs them out and blocks sign-in", danger: true, onclick: () => reasonSheet({ title: `Disable ${s.name}?`, text: "They are signed out at once and cannot sign in until you enable them.", label: "Disable", danger: true, run: async (reason) => { await X.api.call("POST", `/admin/v1/staff/${s.id}/disable${qs({ reason })}`, {}); closeSheet(); toast("Disabled"); after(); } }) }));
  }
  openSheet(s.name, group(kv("Email", s.email), kv("Role", cap(s.role)), kv("Status", s.status === "active" ? (s.has_password ? "Active" : "Waiting to choose a password") : "Disabled"), kv("Last sign-in", s.last_login_at ? ago(s.last_login_at) : "never")), group(...rows), me ? h("p", { class: "sub" }, "This is you. Another owner changes your role or disables your account.") : null);
}

async function staff(X) {
  if (!X.isOwner) return [title("Staff and roles", ""), noaccess()];
  const items = (await X.api.get("/admin/v1/staff")).items;
  const add = () => {
    const name = h("input", { type: "text", maxlength: 120 }), email = h("input", { type: "email", autocomplete: "off" });
    const role = h("select", {}, h("option", { value: "support" }, "Support: can look and help"), h("option", { value: "owner" }, "Owner: can do everything"));
    formSheet("Add a staff member", [field("Name", name), field("Work email", email), field("Role", role), note("They get a one-time link to choose their own password.")], "Create account", async () => {
      const r = await X.api.post("/admin/v1/staff", { name: name.value.trim(), email: email.value.trim(), role: role.value });
      X.refresh(); link("Sign-in link", "Send this to them. It works once and is not shown again.", r.link);
    });
  };
  return [back("More", "#/more"), title("Staff and roles", "Two roles", h("button", { class: "btn sm sec2", type: "button", onclick: add }, ic("plus"), "Add")),
    group(...items.map((s) => row({ initials: initials(s.name), title: s.name, sub: s.email + (s.has_password ? "" : " · waiting to choose a password"), right: h("span", { class: "rowend" }, pill(cap(s.status === "disabled" ? "disabled" : s.role), s.status === "disabled" ? "crit" : "acc"), ic("chev", "chev")), onclick: () => staffSheet(X, s, () => X.refresh()) }))),
    gh("What each role can do"), h("div", { class: "card" }, h("div", { style: null, class: "tablewrap" }, h("table", { class: "roles" }, h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Owner"), h("th", {}, "Support"))),
      h("tbody", {}, ROLE_ROWS.map((r) => h("tr", {}, h("td", {}, r[0]), ...r.slice(1).map((v) => h("td", { class: "c" }, v ? h("span", { class: "yes" }, "●") : h("span", { class: "muted" }, "–")))))))))];
}

export const adminScreens = { maintenance, audit, staff };
