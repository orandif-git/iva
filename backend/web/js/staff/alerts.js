// The alert catalogue and the webhooks (platform owner). Customers never see either.
// System Alerts keep the system healthy; Smart Alerts are the AI ones and always carry the sparkle.
import { errText, h, mount } from "../lib.js";
import { dataTable } from "../app/table.js";
import { chips, confirmSheet, empty, field, formSheet, ic, note, openSheet, closeSheet, pill, reasonSheet, toast, toggle } from "../app/ui.js";
import { ago, back, cap, gh, group, row, title } from "./common.js";

export const FAMILY = { system: "System Alerts", smart: "Smart Alerts" };
const spark = () => ic("spark", "spark");
export const alertLabel = (a) => h("span", { class: "ttl" }, a.ai ? spark() : null, a.title);
const copy = async (text, label) => { try { await navigator.clipboard.writeText(text); toast(label + " copied"); } catch { toast("Select the text and copy it by hand.", true); } };

/** The catalogue is read once per page. */
export const catalog = async (X) => (X.mem.catalog = (await X.api.get("/admin/v1/alert-catalog")).items);

// ------------------------------------------------------------------------------------------ pick alerts (used by plans)
/** Checkboxes for every alert, grouped. Returns { node, get() }. Alerts that are not built yet can be chosen too: they show up for the customer once they are built. */
export function alertPicker(items, selected) {
  const chosen = new Set(selected);
  const boxes = [];
  const node = h("div", { class: "stack" });
  let last = null;
  for (const a of items) {
    const key = a.family + a.group;
    if (key !== last) { node.append(h("div", { class: "gh" }, a.ai ? [spark(), `Smart Alerts · ${a.group}`] : `System Alerts · ${a.group}`)); last = key; }
    const box = h("input", { type: "checkbox", checked: chosen.has(a.type), "aria-label": a.title, onchange: () => (box.checked ? chosen.add(a.type) : chosen.delete(a.type)) });
    boxes.push([a.type, box]);
    node.append(h("label", { class: "gi check" }, h("div", {}, h("b", {}, a.title), a.built ? null : h("small", {}, "Not built yet")), box));
  }
  const all = () => { boxes.forEach(([t, b]) => { b.checked = true; chosen.add(t); }); };
  const none = () => { boxes.forEach(([t, b]) => { b.checked = false; chosen.delete(t); }); };
  return { node: h("div", {}, h("div", { class: "acts2 gap" }, h("button", { class: "btn gray sm", type: "button", onclick: all }, "Select all"), h("button", { class: "btn gray sm", type: "button", onclick: none }, "Clear")), node), get: () => items.map((a) => a.type).filter((t) => chosen.has(t)) };
}

// ------------------------------------------------------------------------------------------ one customer's alerts (a tab on the customer page)
export async function customerAlerts(X, c, after) {
  const r = await X.api.get(`/admin/v1/tenants/${c.id}/alerts`);
  const rows = r.items.map((a) => row({ title: a.title, sub: `${FAMILY[a.family]} · ${a.group}${a.visible ? "" : " · hidden for everyone right now"}`, icon: a.ai ? "spark" : "bell",
    right: h("span", { class: "rowend" }, pill(a.source === "plan" ? "From plan" : "Added", a.source === "plan" ? "" : "acc"),
      a.source === "added" && X.isOwner ? h("button", { class: "btn gray sm", type: "button", onclick: () => reasonSheet({ title: `Take away ${a.title}?`, text: `${c.name} will no longer see it. Their own settings for it are kept.`, label: "Take away", danger: true,
        run: async (reason) => { await X.api.del(`/admin/v1/tenants/${c.id}/alerts/${a.type}?reason=${encodeURIComponent(reason)}`); closeSheet(); toast("Taken away"); after(); } }) }, "Remove") : null) }));
  return h("div", {}, h("p", { class: "sub" }, `Plan: ${r.plan || "none"}. ${c.name} sees these alerts and no others.`), group(...(rows.length ? rows : [h("div", { class: "gi static" }, h("div", { class: "muted" }, "No alerts yet."))])),
    X.isOwner && r.available.length ? h("div", { class: "acts2" }, h("button", { class: "btn sec2 block", type: "button", onclick: () => addAlerts(X, c, r.available, after) }, ic("plus"), "Add an alert")) : null);
}

function addAlerts(X, c, available, after) {
  const picker = alertPicker(available.filter((a) => a.built), []);
  const why = h("input", { type: "text", placeholder: "For example: customer asked for it", autocomplete: "off", "aria-label": "Reason" });
  formSheet(`Add alerts to ${c.name}`, [picker.node, field("Reason (saved in the audit log)", why)], "Add", async () => {
    const pick = picker.get();
    if (!pick.length) throw new Error("Choose at least one alert.");
    if (why.value.trim().length < 5) throw new Error("Type a reason of a few words.");
    for (const t of pick) await X.api.put(`/admin/v1/tenants/${c.id}/alerts/${t}`, { reason: why.value.trim() });
    closeSheet(); toast(pick.length === 1 ? "Alert added" : `${pick.length} alerts added`); after();
  }, { wide: true });
}

// ------------------------------------------------------------------------------------------ the catalogue
async function alerts(X) {
  const items = await catalog(X);
  const st = X.mem.cat ||= { f: "all" };
  const built = items.filter((a) => a.built), on = built.filter((a) => a.enabled);
  const table = dataTable({
    label: "Alerts", rows: items, rowKey: (a) => a.type, memory: (st.table ||= {}),
    empty: h("p", { class: "sub" }, "No alerts."),
    columns: [
      { key: "title", label: "Alert", primary: true, sort: true, render: (a) => h("div", {}, alertLabel(a), h("small", { class: "muted block" }, a.description)) },
      { key: "family", label: "Type", sort: true, minor: true, render: (a) => h("span", { class: "ttl" }, a.ai ? spark() : null, a.ai ? "Smart" : "System") },
      { key: "needs", label: "What it needs", minor: true, sort: true },
      { key: "status", label: "Status", sort: true, value: (a) => (!a.built ? "z" : a.enabled ? "a" : "b"), render: (a) => (!a.built ? pill("Not built yet", "") : a.enabled ? pill("Live", "ok") : pill("Switched off", "warn")) },
      { key: "customers_using", label: "Customers using", sort: true, align: "end", render: (a) => (a.built ? String(a.customers_using) : "—") },
      { key: "plans", label: "Given by plans", minor: true, value: (a) => a.plans.length, render: (a) => (a.plans.length ? a.plans.map((p) => p.name).join(", ") : "None") },
      { key: "switch", label: "On for customers", render: (a) => {
        if (!a.built) return h("span", { class: "muted" }, "—");
        const t = toggle(a.enabled, () => {}, `${a.title} for all customers`), input = t.querySelector("input");
        input.disabled = !X.isOwner;
        input.onchange = () => {
          const want = input.checked; input.checked = a.enabled;
          reasonSheet({ title: want ? `Turn on “${a.title}”?` : `Turn off “${a.title}” for everyone?`, label: want ? "Turn on" : "Turn off", danger: !want,
            text: want ? "It appears for customers who have it, and their messages start going out." : `${a.customers_using} customer${a.customers_using === 1 ? " uses" : "s use"} this alert. It disappears from their Alerts page and no messages are sent. Their settings are kept.`,
            run: async (reason) => { await X.api.put(`/admin/v1/alert-catalog/${a.type}`, { enabled: want, reason }); closeSheet(); toast(want ? "Turned on" : "Turned off"); X.refresh(); } });
        };
        return t;
      } },
    ],
  });
  const draw = () => table.setRows(items.filter((a) => st.f === "all" || a.family === st.f));
  draw();
  return [title("Alerts", `${on.length} live of ${built.length} built · ${items.length - built.length} planned`),
    h("p", { class: "sub" }, "System Alerts keep the system healthy. Smart Alerts use AI. Customers see an alert only when it is live here and their plan, or you, gave it to them."),
    h("div", { class: "gap" }, chips([["all", "All"], ["system", "System Alerts"], ["smart", "Smart Alerts"]], st.f, (v) => { st.f = v; draw(); })), table,
    X.isOwner ? null : note("Only an owner can switch an alert on or off.", "warn")];
}

// ------------------------------------------------------------------------------------------ webhooks
function pairs(list) {
  const box = h("div"), rows = [];
  const add = (a = "", b = "") => { const ia = h("input", { class: "mono", "aria-label": "Header name", value: a, placeholder: "Authorization" }), ib = h("input", { class: "mono", "aria-label": "Header value", value: b, placeholder: "Bearer …" }); rows.push([ia, ib]); box.append(h("div", { class: "two gap" }, ia, ib)); };
  list.forEach(([a, b]) => add(a, b));
  return { node: h("div", {}, box, h("button", { class: "btn gray sm", type: "button", onclick: () => add() }, ic("plus"), "Add header")), get: () => Object.fromEntries(rows.map(([a, b]) => [a.value.trim(), b.value.trim()]).filter(([a]) => a)) };
}

const render = (tpl, sample) => tpl.replace(/\{(\w+)\}/g, (m, k) => (k in sample ? JSON.stringify(String(sample[k])).slice(1, -1) : m));

/** Add or change an endpoint: where it goes, which alert it sends, and the payload. */
function endpointSheet(X, ep, d, after) {
  const isNew = !ep;
  const events = d.catalog.filter((a) => a.built && (isNew ? !d.items.some((e) => e.alert_type === a.type) : a.type === ep.alert_type));
  if (isNew && !events.length) return toast("Every alert that is built already has a webhook.", true);
  let type = ep?.alert_type || events[0].type;
  const name = h("input", { type: "text", maxlength: 80, value: ep?.name || events[0].title, placeholder: "For example Camera offline" });
  const url = h("input", { class: "mono", type: "text", value: ep?.url || d.last?.url || "https://", placeholder: "https://…" });
  const hdr = pairs(Object.entries(ep?.headers || d.last?.headers || { "Content-Type": "application/json" }));
  const tpl = h("textarea", { class: "code", rows: 11, spellcheck: "false" }, ep?.payload_template || "");
  const prev = h("pre", { class: "mono" }), codes = h("div", { class: "chips wrapchips" }), mediaNote = h("div", { class: "hint" });
  const meta = (t) => d.catalog.find((a) => a.type === t);
  const draw = () => {
    let out = render(tpl.value, d.sample);
    try { out = JSON.stringify(JSON.parse(out), null, 2); } catch { /* shown as it is */ }
    prev.textContent = out;
    const m = meta(type);
    mount(codes, m.shortcodes.map((c) => h("button", { class: "chip", type: "button", "aria-pressed": "false", onclick: () => { const pos = tpl.selectionStart ?? tpl.value.length; tpl.value = tpl.value.slice(0, pos) + `{${c}}` + tpl.value.slice(pos); draw(); } }, `{${c}}`)));
    mount(mediaNote, m.media ? `This alert carries ${m.media === "image_clip" ? "a picture and a clip" : "a picture"}. The links in image_url and clip_url work for ${d.settings.link_hours} hour${d.settings.link_hours > 1 ? "s" : ""}, never more than 4.` : "This alert has no picture or clip.");
  };
  const defaults = (t) => { const m = meta(t); const lines = ['"phone_number": "{to_phone}"', `"template_name": "${m.event.replace(".", "_")}_v1"`, '"customer": "{customer}"', '"camera": "{camera}"', '"time": "{time}"', '"details": "{details}"', ...(m.media ? ['"image_url": "{image_url}"'] : []), ...(m.media === "image_clip" ? ['"clip_url": "{clip_url}"'] : [])]; return "{\n  " + lines.join(",\n  ") + "\n}"; };
  if (!tpl.value) tpl.value = defaults(type);
  tpl.addEventListener("input", draw);
  const pick = isNew ? h("div", { class: "group" }, events.map((a) => h("label", { class: "gi check" }, h("div", {}, h("b", {}, alertLabel(a)), h("small", { class: "mono" }, a.event)),
    h("input", { type: "radio", name: "ev", checked: a.type === type, onchange: () => { type = a.type; if (name.value === "" || events.some((e) => e.title === name.value)) name.value = a.title; tpl.value = defaults(type); draw(); } })))) : h("div", { class: "gi static" }, h("div", {}, h("b", {}, alertLabel(meta(type))), h("small", { class: "mono" }, meta(type).event)));
  draw();
  formSheet(isNew ? "Add webhook endpoint" : "Edit webhook endpoint", [gh("Destination"), field("Name", name), field("Endpoint URL", url, "https only."), h("div", {}, h("b", {}, "Authentication and headers"), hdr.node),
    gh("Alert event"), pick, gh("Payload"), field("JSON payload template", tpl, "Use only the shortcodes below. They match the alert you picked."), h("div", { class: "gh" }, "Live payload preview (sample data)"), prev,
    h("div", { class: "gh" }, "Shortcodes for this alert (tap to add)"), codes, mediaNote], isNew ? "Create endpoint" : "Save changes", async () => {
    const body = { name: name.value.trim(), url: url.value.trim(), headers: hdr.get(), payload_template: tpl.value };
    if (isNew) await X.api.post("/admin/v1/webhooks", { ...body, alert_type: type }); else await X.api.put(`/admin/v1/webhooks/${ep.id}`, body);
    closeSheet(); toast(isNew ? "Endpoint created" : "Saved"); after();
  }, { wide: true });
}

/** One endpoint for every alert that is built and has none, all pointing at the same address. */
function standardSheet(X, d, after) {
  const url = h("input", { class: "mono", type: "text", value: d.items[0]?.url || "https://", placeholder: "https://…" });
  const hdr = pairs(Object.entries(d.items[0]?.headers || { "Content-Type": "application/json" }));
  const missing = d.catalog.filter((a) => a.built && !d.items.some((e) => e.alert_type === a.type));
  formSheet("Create the standard endpoints", [h("p", {}, `This makes a webhook for the ${missing.length} alert${missing.length === 1 ? "" : "s"} that ${missing.length === 1 ? "has" : "have"} none yet: ${missing.map((a) => a.title).join(", ")}. They all use this address and these headers, and each gets its standard payload. You can change any of them afterwards.`),
    field("Endpoint URL", url, "https only."), h("div", {}, h("b", {}, "Headers"), hdr.node)], "Create endpoints", async () => {
    const r = await X.api.post("/admin/v1/webhooks/standard", { url: url.value.trim(), headers: hdr.get() });
    closeSheet(); toast(`${r.created.length} endpoint${r.created.length === 1 ? "" : "s"} created`); after();
  }, { wide: true });
}


// ------------------------------------------------------------------------------------------ email for scheduled reports
/** Alerts never go by email, but scheduled reports still do, through one SMTP server. */
function emailSheet(X, p, after) {
  const c = p?.config || {};
  const host = h("input", { type: "text", value: c.host || "", placeholder: "smtp.example.com" }), port = h("input", { type: "number", value: c.port || 587, inputmode: "numeric" });
  const sec = h("select", {}, ["starttls", "ssl"].map((k) => h("option", { value: k, selected: (c.security || "starttls") === k }, k === "ssl" ? "SSL (usually port 465)" : "STARTTLS (usually port 587)")));
  const user = h("input", { type: "text", value: c.username || "", autocomplete: "off" }), pw = h("input", { type: "password", autocomplete: "new-password", placeholder: p ? "Leave empty to keep the password" : "" });
  const from = h("input", { type: "text", value: c.from_email || "", placeholder: "reports@example.com" }), fromName = h("input", { type: "text", value: c.from_name || "Jas iVision" });
  const why = h("input", { type: "text", placeholder: "For example: set up report email", autocomplete: "off", "aria-label": "Reason" });
  formSheet("Email for scheduled reports", [h("p", { class: "sub" }, "Used only to email scheduled reports. Alerts are sent by WhatsApp."), field("Server", host), field("Port", port), field("Security", sec), field("User name", user), field("Password", pw),
    field("Send from (address)", from), field("Send from (name)", fromName), field("Reason (saved in the audit log)", why)], "Save", async () => {
    if (why.value.trim().length < 5) throw new Error("Type a reason of a few words.");
    const config = { host: host.value.trim(), port: Number(port.value), security: sec.value, from_email: from.value.trim(), from_name: fromName.value.trim(), ...(user.value.trim() ? { username: user.value.trim() } : {}) };
    const secrets = pw.value ? { password: pw.value } : undefined;
    if (p) await X.api.put(`/admin/v1/messaging/providers/${p.id}`, { config, ...(secrets ? { secrets } : {}), reason: why.value.trim() });
    else {
      const r = await X.api.post("/admin/v1/messaging/providers", { channel: "email", name: "Report email", kind: "smtp", config, secrets: secrets || {}, reason: why.value.trim() });
      await X.api.post(`/admin/v1/messaging/providers/${r.id}/activate?reason=${encodeURIComponent(why.value.trim())}`);
    }
    closeSheet(); toast("Saved"); after();
  });
}

export async function emailCard(X, after) {
  const list = (await X.api.get("/admin/v1/messaging/providers")).filter((p) => p.channel === "email");
  const p = list.find((x) => x.active) || list[0];
  return h("div", { class: "card stack" }, h("div", { class: "gi static" }, h("div", {}, h("b", {}, "Email for scheduled reports"), h("small", {}, p ? `${p.config.host}:${p.config.port} · ${p.active ? "in use" : "off"}` : "Not set up. Scheduled reports cannot be emailed until it is.")),
    h("button", { class: "btn gray sm", type: "button", onclick: () => emailSheet(X, p, after) }, p ? "Change" : "Set up")));
}

const DS = { sent: ["Sent", "ok"], queued: ["Waiting", "warn"], failed: ["Failed", "crit"], suppressed: ["Not sent", ""], cancelled: ["Cancelled", ""] };

async function webhooks(X) {
  if (!X.isOwner) return [back("More", "#/more"), title("Webhooks", ""), empty("lock", "Only an owner sees this page", "Webhooks hold tokens and carry customers' alerts.")];
  const st = X.mem.hook ||= { f: "all", ep: "" };
  const [w, cat, log] = await Promise.all([X.api.get("/admin/v1/webhooks"), X.api.get("/admin/v1/alert-catalog"),
    X.api.get(`/admin/v1/webhooks/deliveries?limit=100${st.f !== "all" ? "&status=" + st.f : ""}${st.ep ? "&endpoint_id=" + st.ep : ""}`)]);
  const d = { items: w.items, settings: w.settings, sample: w.sample, catalog: cat.items, last: w.items[w.items.length - 1] };
  const after = () => X.refresh();
  const builtMissing = cat.items.some((a) => a.built && !w.items.some((e) => e.alert_type === a.type));
  const out = [back("More", "#/more"), title("Webhooks", "Every alert sends one event to your system, once for each person to tell. Your system sends the WhatsApp message.",
    h("div", { class: "rowend" }, builtMissing && w.items.length ? h("button", { class: "btn sm gray", type: "button", onclick: () => standardSheet(X, d, after) }, "Add the rest") : null, h("button", { class: "btn sm sec2", type: "button", onclick: () => endpointSheet(X, null, d, after) }, ic("plus"), "Add endpoint")))];

  // settings: how long picture links last, and the signing secret
  const life = h("div", { class: "seg2", role: "group", "aria-label": "How long picture and clip links work" }, [1, 2, 3, 4].map((n) => h("button", { type: "button", "aria-pressed": String(w.settings.link_hours === n), onclick: async () => { try { await X.api.put("/admin/v1/webhooks/settings", { link_hours: n }); toast(`Picture and clip links now last ${n} hour${n > 1 ? "s" : ""}`); after(); } catch (e) { toast(errText(e), true); } } }, `${n} h`)));
  out.push(h("div", { class: "card stack" }, h("div", {}, h("b", {}, "Picture and clip links last"), h("p", { class: "sub" }, "At most 4 hours. After that the link stops working.")), life,
    h("div", { class: "gi static" }, h("div", {}, h("b", {}, "Signing secret"), h("small", {}, w.settings.has_secret ? "Every event is signed so your system can check it came from us." : "None yet. Make one so your system can check events.")),
      h("button", { class: "btn gray sm", type: "button", onclick: () => confirmSheet("Make a new signing secret?", "The old one stops working at once. Copy the new one to your system straight away. It is shown only once.", "Make new secret", async () => {
        const r = await X.api.post("/admin/v1/webhooks/settings/secret"); closeSheet();
        openSheet("Your new signing secret", h("p", {}, "Copy it now. It is not shown again."), h("dl", { class: "cred" }, h("dt", {}, "Secret"), h("dd", {}, r.secret), h("button", { class: "btn sm gray", type: "button", "aria-label": "Copy secret", onclick: () => copy(r.secret, "Secret") }, ic("copy"))), h("div", { class: "acts" }, h("button", { class: "btn", type: "button", onclick: () => { closeSheet(); after(); } }, "Done")));
      }, { danger: false }) }, w.settings.has_secret ? "New secret" : "Make secret"))));

  // endpoints
  out.push(gh("Webhook endpoints"));
  if (!w.items.length) out.push(empty("link", "No webhooks yet", "Create the standard set: one endpoint for each alert that is built, all sending to your system.", h("button", { class: "btn", type: "button", onclick: () => standardSheet(X, d, after) }, "Create standard endpoints")));
  else out.push(h("div", { class: "card ep-list" }, w.items.map((e) => {
    const t = toggle(e.active, () => {}, `${e.name} on or off`), input = t.querySelector("input");
    input.onchange = async () => { try { await X.api.put(`/admin/v1/webhooks/${e.id}`, { active: input.checked }); toast(input.checked ? "Turned on" : "Turned off"); after(); } catch (x) { input.checked = e.active; toast(errText(x), true); } };
    return h("div", { class: "ep" }, h("div", { class: "grow" }, h("div", { class: "ept" }, e.name), h("div", { class: "mono epu" }, e.url), h("span", { class: "evtag" }, e.ai ? spark() : null, e.event)),
      pill(e.active ? "Active" : "Off", e.active ? "ok" : ""), t, h("span", { class: "muted epn" }, `${e.deliveries.toLocaleString("en-IN")} deliveries`),
      h("button", { class: "btn sm gray", type: "button", onclick: async () => { try { const r = await X.api.post(`/admin/v1/webhooks/${e.id}/send-now`); toast(r.ok ? `Test sent. Your system answered ${r.status}.` : `Test failed: ${r.error}`, !r.ok); } catch (x) { toast(errText(x), true); } } }, ic("share"), "Send now"),
      h("button", { class: "ib", type: "button", "aria-label": `Edit ${e.name}`, onclick: () => endpointSheet(X, e, d, after) }, ic("edit")),
      h("button", { class: "ib dng", type: "button", "aria-label": `Delete ${e.name}`, onclick: () => confirmSheet(`Delete ${e.name}?`, "That alert will no longer send anything until you add a webhook for it again.", "Delete", async () => { await X.api.del(`/admin/v1/webhooks/${e.id}`); closeSheet(); toast("Deleted"); after(); }) }, ic("trash")));
  })));

  // delivery log
  out.push(gh("Delivery log"), h("div", { class: "gap" }, chips([["all", "All"], ["failed", "Failed"], ["queued", "Waiting"], ["suppressed", "Not sent"]], st.f, (v) => { st.f = v; after(); })),
    h("div", { class: "gap" }, h("select", { "aria-label": "Show one endpoint", onchange: (e) => { st.ep = e.target.value; after(); } }, h("option", { value: "" }, "All endpoints"), w.items.map((e) => h("option", { value: e.id, selected: st.ep === e.id }, e.name)))));
  out.push(dataTable({
    label: "Delivery log", rows: log.items, rowKey: (n) => n.id, memory: (st.table ||= {}), empty: h("p", { class: "sub" }, "Nothing sent yet. Events appear here when alerts go out."),
    columns: [
      { key: "endpoint", label: "Endpoint", primary: true, sort: true, render: (n) => n.endpoint || n.event || "—" },
      { key: "event", label: "Event", sort: true, minor: true, render: (n) => h("span", { class: "mono" }, n.event || "—") },
      { key: "tenant", label: "Customer", sort: true, minor: true },
      { key: "status", label: "Status", sort: true, render: (n) => pill(DS[n.status][0], DS[n.status][1]) },
      { key: "attempts", label: "Att.", sort: true, align: "end", minor: true },
      { key: "response", label: "Response", minor: true, value: (n) => n.response_status, render: (n) => (n.response_status ? String(n.response_status) : n.error ? n.error.slice(0, 40) : "—") },
      { key: "created_at", label: "Time", sort: true, value: (n) => new Date(n.created_at).getTime(), render: (n) => ago(n.created_at) },
      { key: "act", label: "", render: (n) => (n.status === "failed" ? h("button", { class: "btn sm gray", type: "button", onclick: async () => { try { await X.api.post(`/admin/v1/webhooks/deliveries/${n.id}/resend`); toast("Queued to send again"); after(); } catch (e) { toast(errText(e), true); } } }, "Send again") : null) },
    ],
  }), h("p", { class: "sub" }, "A failed event is tried again for about an hour and three quarters, then marked failed. You can send a failed one again from here."));
  out.push(gh("Email"), await emailCard(X, after));
  return out;
}

export const alertScreens = { alerts, webhooks };
