// Messaging: the WhatsApp and email providers, WhatsApp message templates, and the delivery log.
import { clear, errText, h, mount, qs } from "../lib.js";
import { chips, empty, field, formSheet, ic, note, openSheet, closeSheet, pill, reasonSheet, seg, sheetWide, toast, toggle } from "../app/ui.js";
import { ago, back, cap, gh, group, kv, row, title } from "./common.js";

const SAMPLES = ["Blocked vehicle TN72AB1234", "Main Gate", "05 Nov, 10:30 AM", "Seen entering"], VARS = ["event", "location", "time", "details"];
const TS = { draft: ["Draft", ""], submitted: ["Waiting for approval", "warn"], approved: ["Approved", "ok"], rejected: ["Rejected", "crit"], paused: ["Paused", ""] };
const DS = { sent: "ok", delivered: "ok", read: "ok", failed: "crit", queued: "warn", suppressed: "", cancelled: "" };
const TOKENS = ["recipient.phone_digits", "recipient.name", "template.name", "template.language", "vars", "media.url", "link.token", "message.id", "alert.title"];
const area = (value, rows) => h("textarea", { class: "code", rows, spellcheck: "false" }, value);

function pairs(list, aria) {
  const box = h("div"), rows = [];
  const add = (a = "", b = "") => { const ia = h("input", { class: "mono", "aria-label": aria[0], value: a }), ib = h("input", { class: "mono", "aria-label": aria[1], value: b }); rows.push([ia, ib]); box.append(h("div", { class: "two", style: null }, ia, ib)); box.lastChild.style.marginBottom = "8px"; };
  list.forEach(([a, b]) => add(a, b));
  return { node: h("div", {}, box, h("button", { class: "btn gray sm", type: "button", onclick: () => add() }, ic("plus"), "Add")), get: () => rows.map(([a, b]) => [a.value.trim(), b.value.trim()]).filter(([a]) => a) };
}

// ----------------------------------------------------------------------------------------------- providers
function providerSheet(X, p, after) {
  const isNew = !p;
  const c = p ? JSON.parse(JSON.stringify(p.config || {})) : {};
  const name = h("input", { type: "text", value: p?.name || "", maxlength: 80, placeholder: "For example WhatsApp provider" });
  const rate = h("input", { type: "number", min: 0.1, max: 100, step: "0.1", value: p?.rate_per_second ?? 5, inputmode: "decimal" });
  const channel = h("select", { disabled: !isNew }, h("option", { value: "whatsapp", selected: p?.channel === "whatsapp" }, "WhatsApp (HTTP recipe)"), h("option", { value: "email", selected: p?.channel === "email" }, "Email (SMTP)"));
  const kindNow = () => (isNew ? (channel.value === "email" ? "smtp" : "http") : p.kind);
  const dyn = h("div", { class: "stack" });
  const live = { secrets: {} };                              // keys typed or replaced in this sheet
  let get = () => ({});

  const drawHttp = () => {
    const method = h("select", {}, h("option", { value: "POST", selected: (c.method || "POST") === "POST" }, "POST"), h("option", { value: "PUT", selected: c.method === "PUT" }, "PUT"));
    const url = h("input", { class: "mono", type: "text", value: c.url || "https://", placeholder: "https://provider.example.com/v1/messages" });
    const hdr = pairs(Object.entries(c.headers || { Authorization: "Bearer {{secret.api_key}}", "Content-Type": "application/json" }), ["Header name", "Header value"]);
    const body = area(JSON.stringify(c.body ?? { to: "{{recipient.phone_digits}}" }, null, 2), 14);
    const chk = h("div", { class: "note ok" }, ic("check"), " Checked: the payload is valid JSON.");
    body.addEventListener("input", () => { try { JSON.parse(body.value); chk.className = "note ok"; mount(chk, ic("check"), " Checked: the payload is valid JSON."); } catch (e) { chk.className = "note err"; mount(chk, ic("x"), " " + e.message); } });
    const tokens = h("div", { class: "chips", style: null }, TOKENS.map((t) => h("button", { class: "chip", type: "button", "aria-pressed": "false", onclick: () => { const tok = t === "vars" ? '{ "$each": "vars", "do": "{{item}}" }' : "{{" + t + "}}", pos = body.selectionStart ?? body.value.length; body.value = body.value.slice(0, pos) + tok + body.value.slice(pos); body.dispatchEvent(new Event("input")); toast("Inserted"); } }, t)));
    tokens.style.flexWrap = "wrap"; tokens.style.overflow = "visible";
    const okStatus = h("input", { type: "text", value: (c.success?.status || [200, 201, 202]).join(", ") }), idPath = h("input", { class: "mono", type: "text", value: c.message_id_path || "" }), errPath = h("input", { class: "mono", type: "text", value: c.error_path || "" });
    const retry = h("input", { type: "text", value: (c.retry_statuses || []).join(", "), placeholder: "for example 409" });
    const w = c.webhook || { auth: { type: "hmac_sha256", header: "X-Signature", secret: "webhook_secret" }, id_path: "id", status_path: "status", status_map: { sent: "sent", delivered: "delivered", read: "read", failed: "failed" } };
    const wType = h("select", {}, h("option", { value: "hmac_sha256", selected: w.auth.type === "hmac_sha256" }, "Signature (HMAC SHA-256)"), h("option", { value: "header_equals", selected: w.auth.type === "header_equals" }, "A fixed header value"));
    const wHeader = h("input", { class: "mono", type: "text", value: w.auth.header || "" }), wSecret = h("input", { class: "mono", type: "text", value: w.auth.secret || "" });
    const wId = h("input", { class: "mono", type: "text", value: w.id_path || "" }), wStatus = h("input", { class: "mono", type: "text", value: w.status_path || "" });
    const map = pairs(Object.entries(w.status_map || {}), ["Provider's word", "Our status"]);
    const useHook = toggle(!!c.webhook, () => {}, "Delivery reports");
    mount(dyn,
      gh("Endpoint"), h("div", { class: "card" }, field("Method", method), field("URL", url, "https only. Anything in {{double braces}} is filled in for each message.")),
      gh("Headers"), h("div", { class: "card" }, hdr.node, h("div", { class: "hint" }, "Reference a stored key as {{secret.api_key}}.")),
      gh("Payload (JSON)"), h("div", { class: "card" }, field("What we send", body), h("div", { class: "hint" }, "Tap a detail to insert it at the cursor."), tokens, h("div", { style: null, class: "gap" }, chk)),
      gh("How we know it worked"), h("div", { class: "card" }, field("Success statuses", okStatus, "Comma separated HTTP statuses."), field("Message ID is at", idPath, "Where the provider puts its message id in the reply."), field("Error text is at", errPath, "Where to read an error message."), field("Also retry on statuses", retry, "429, 408 and every 5xx are always retried.")),
      gh("Delivery reports (webhook)"), h("div", { class: "card" }, h("div", { class: "gi static" }, h("div", {}, h("b", {}, "Receive delivery reports"), h("small", {}, "Lets the provider tell us when a message was delivered or read")), useHook),
        p ? h("dl", { class: "cred" }, h("dt", {}, "Give this address to the provider"), h("dd", {}, p.webhook_url), h("button", { class: "btn sm gray", type: "button", "aria-label": "Copy address", onclick: async () => { try { await navigator.clipboard.writeText(p.webhook_url); toast("Address copied"); } catch { toast("Select it and copy by hand.", true); } } }, ic("copy"))) : h("p", { class: "sub" }, "The address to give the provider appears after you save."),
        field("Check each report with", wType), field("Header name", wHeader), field("Stored key to check with", wSecret, "The name of a key stored below."), field("Message ID is at", wId), field("Status is at", wStatus), h("div", { class: "gh" }, "Provider's word to our status"), map.node));
    get = () => {
      const cfg = { ...c, method: method.value, url: url.value.trim() };
      cfg.headers = Object.fromEntries(hdr.get());
      try { cfg.body = JSON.parse(body.value); } catch { throw new Error("The payload is not valid JSON. Fix it before saving."); }
      const st = okStatus.value.split(",").map((x) => parseInt(x, 10)).filter((x) => x > 0);
      cfg.success = { ...(c.success || {}), status: st.length ? st : [200, 201, 202] };
      const set = (k, v) => { if (v) cfg[k] = v; else delete cfg[k]; };
      set("message_id_path", idPath.value.trim()); set("error_path", errPath.value.trim());
      const rs = retry.value.split(",").map((x) => parseInt(x, 10)).filter((x) => x > 0); if (rs.length) cfg.retry_statuses = rs; else delete cfg.retry_statuses;
      if (useHook.querySelector("input").checked) cfg.webhook = { ...(c.webhook || {}), auth: { ...(c.webhook?.auth || {}), type: wType.value, header: wHeader.value.trim(), secret: wSecret.value.trim() }, id_path: wId.value.trim(), status_path: wStatus.value.trim(), status_map: Object.fromEntries(map.get()) };
      else delete cfg.webhook;
      return cfg;
    };
  };

  const drawSmtp = () => {
    const host = h("input", { class: "mono", type: "text", value: c.host || "", placeholder: "smtp.provider.com" }), port = h("input", { class: "mono", type: "number", value: c.port || 587, inputmode: "numeric" });
    const sec = h("select", {}, [["starttls", "STARTTLS (usually port 587)"], ["ssl", "SSL (usually port 465)"]].map(([v, l]) => h("option", { value: v, selected: (c.security || "starttls") === v }, l)));
    const user = h("input", { class: "mono", type: "text", value: c.username || "" }), fn = h("input", { type: "text", value: c.from_name || "Jas iVision" }), fe = h("input", { type: "email", value: c.from_email || "" });
    mount(dyn, gh("Mail server"), h("div", { class: "card" }, field("Server", host), h("div", { class: "two" }, field("Port", port), field("Security", sec)), field("Username", user, "The password is stored as the key named password, below.")),
      gh("Sender"), h("div", { class: "card" }, field("From name", fn), field("From address", fe, "Use an address on a domain set up for sending, so mail is not marked as spam.")));
    get = () => ({ ...c, host: host.value.trim(), port: Number(port.value), security: sec.value, username: user.value.trim(), from_name: fn.value.trim(), from_email: fe.value.trim() });
  };
  const draw = () => (kindNow() === "http" ? drawHttp() : drawSmtp());
  channel.addEventListener("change", draw);
  draw();

  const secrets = h("div"), names = new Set(p?.secret_names || []);
  const drawSecrets = () => mount(secrets, h("div", { class: "group" }, [...names].map((k) => h("div", { class: "gi static" }, h("span", { class: "ico" }, ic("key")), h("div", {}, h("b", { class: "mono" }, k), h("small", {}, live.secrets[k] ? "New value, saved when you press Save" : "•••••••••••• never shown again")),
    h("button", { class: "btn sm gray", type: "button", onclick: () => secretForm(k) }, "Replace")))), h("div", { class: "acts2" }, h("button", { class: "btn gray block", type: "button", onclick: () => secretForm("") }, ic("plus"), "Add a key")));
  const secretForm = (k) => {
    const n = h("input", { class: "mono", type: "text", value: k, disabled: !!k, placeholder: "api_key" }), v = h("input", { type: "password", autocomplete: "off" });
    const saved = sheetState(); formSheet(k ? `Replace ${k}` : "Add a key", [k ? null : field("Name", n, "Lowercase letters, numbers and underscores."), field("Value", v, "Pasted once, then stored encrypted. Never shown again.")], "Keep this key", async () => {
      const key = (k || n.value.trim()); if (!/^[a-z][a-z0-9_]{0,39}$/.test(key)) throw new Error("Use lowercase letters, numbers and underscores for the name."); if (!v.value) throw new Error("Paste the value.");
      live.secrets[key] = v.value; names.add(key); closeSheet(); saved(); });
  };
  const sheetState = () => () => openProvider();

  // test
  const to = h("input", { type: "text", placeholder: kindNow() === "smtp" ? "you@company.in" : "+91 98765 43210", autocomplete: "off" }), res = h("div");
  const tplSel = h("select", {}, X.mem.tpl?.filter((t) => t.status === "approved").map((t) => h("option", { value: t.key }, t.key)) || []);
  const test = async (send) => {
    if (isNew) { clear(res); res.append(note("Save the provider first, then try it.", "warn")); return; }
    clear(res);
    try {
      const r = await X.api.post(`/admin/v1/messaging/providers/${p.id}/test`, { to: to.value.trim(), send, ...(kindNow() === "http" && tplSel.value ? { template_key: tplSel.value } : {}) });
      if (r.preview) res.append(h("div", { class: "note" }, h("b", {}, "The request we would send"), " (nothing is sent):", h("pre", { class: "mono" }, JSON.stringify(r.preview, null, 2))));
      else res.append(note(r.ok ? `Sent. The provider accepted it${r.message_id ? ` (message id ${r.message_id})` : ""}. Check the phone or inbox.` : `Not accepted: ${r.error || "the provider refused it"}`, r.ok ? "ok" : "err"));
    } catch (e) { res.append(note(errText(e), "err")); }
  };

  const why = h("input", { type: "text", placeholder: "For example: new provider contract", autocomplete: "off", "aria-label": "Reason" });
  const err = h("div"), save = h("button", { class: "btn", type: "button", disabled: true }, isNew ? "Create provider" : "Save changes");
  const toggleBtn = isNew ? null : h("button", { class: "btn gray", type: "button", disabled: true }, p.active ? "Turn off" : "Use this provider");
  const del = isNew ? null : h("button", { class: "btn danger", type: "button", disabled: true }, "Delete");
  why.oninput = () => { const ok = why.value.trim().length >= 5; save.disabled = !ok; if (toggleBtn) toggleBtn.disabled = !ok; if (del) del.disabled = !ok; };
  const run = (fn) => async () => { clear(err); try { await fn(); } catch (e) { err.append(note(e.message || errText(e), "err")); } };
  save.onclick = run(async () => {
    const config = get();
    const sec = Object.keys(live.secrets).length ? { secrets: live.secrets } : {};
    if (isNew) await X.api.post("/admin/v1/messaging/providers", { channel: channel.value, name: name.value.trim(), kind: kindNow(), config, ...sec, rate_per_second: Number(rate.value), reason: why.value.trim() });
    else await X.api.put(`/admin/v1/messaging/providers/${p.id}`, { name: name.value.trim(), config, ...sec, rate_per_second: Number(rate.value), reason: why.value.trim() });
    closeSheet(); toast(isNew ? "Provider created" : "Provider saved"); after();
  });
  if (toggleBtn) toggleBtn.onclick = run(async () => { await X.api.call("POST", `/admin/v1/messaging/providers/${p.id}/${p.active ? "deactivate" : "activate"}${qs({ reason: why.value.trim() })}`, {}); closeSheet(); toast(p.active ? "Turned off" : "Now in use"); after(); });
  if (del) del.onclick = run(async () => { await X.api.del(`/admin/v1/messaging/providers/${p.id}${qs({ reason: why.value.trim() })}`); closeSheet(); toast("Provider deleted"); after(); });

  function openProvider() {
    drawSecrets();
    openSheet(isNew ? "Add a provider" : `${cap(p.channel)} provider`, h("div", { class: "stack" },
      isNew ? null : h("div", { class: "hero" }, h("span", { class: "ico" }, ic(p.channel === "email" ? "doc" : "chat")), h("div", { class: "grow" }, h("div", { class: "t" }, p.name), h("div", { class: "s" }, `${cap(p.channel)} · ${p.kind === "http" ? "HTTP recipe" : "SMTP"}`)), pill(p.active ? "In use" : "Off", p.active ? "ok" : "")),
      gh("Basics"), h("div", { class: "card" }, field("Channel", channel), field("Name", name), field("Messages per second", rate, "Slows sending so the provider does not block us.")),
      dyn, gh("Stored keys"), secrets,
      gh("Try it"), h("div", { class: "card" }, field(kindNow() === "smtp" ? "Send to (email address)" : "Send to (phone number)", to), kindNow() === "http" ? field("Template", tplSel) : null, h("div", { class: "two" }, h("button", { class: "btn gray", type: "button", onclick: () => test(false) }, "Preview the request"), h("button", { class: "btn sec2", type: "button", onclick: () => test(true) }, "Send a test")), h("div", { class: "gap" }, res)),
      h("div", { class: "card" }, field("Reason (saved in the audit log)", why), err, h("div", { class: "two" }, save, toggleBtn || h("span")), del ? h("div", { class: "acts2" }, del) : null,
        isNew ? null : h("p", { class: "sub" }, "A provider that is in use, or that already sent messages, cannot be deleted. Turning on another provider turns this one off."))));
    sheetWide();
  }
  openProvider();
}

// ------------------------------------------------------------------------------------------------ templates
const bubble = (t) => {
  const parts = t.body_text.split(/(\{\{\d\}\})/).map((s) => { const m = s.match(/^\{\{(\d)\}\}$/); return m ? h("b", {}, SAMPLES[m[1] - 1] || s) : s; });
  const lines = []; parts.forEach((p) => { if (typeof p === "string") p.split("\n").forEach((l, i) => { if (i) lines.push(h("br")); lines.push(l); }); else lines.push(p); });
  return h("div", { class: "bubble" }, t.media_type !== "none" ? h("div", { class: "pic" }, t.media_type === "image" ? "Picture shown here" : "Video clip shown here") : null, ...lines, t.link_button ? h("div", { class: "btn2" }, "View evidence") : null);
};

function templateSheet(X, t, after) {
  const isNew = !t;
  const cur = t ? { ...t } : { key: "", provider_template: "", language: "en", category: "utility", status: "draft", media_type: "none", link_button: false, description: "", body_text: "Jas iVision notice.\n\nEvent: {{1}}\nLocation: {{2}}\n\nPlease check on site.", variables: ["event", "location"] };
  const prev = h("div", { class: "card" }, bubble(cur));
  const key = h("input", { class: "mono", type: "text", value: cur.key, disabled: !isNew, placeholder: "alert_text" }), pt = h("input", { class: "mono", type: "text", value: cur.provider_template, disabled: !isNew, placeholder: "gi_alert_text" });
  const body = h("textarea", { class: "code", rows: 9, spellcheck: "false", disabled: !isNew }, cur.body_text);
  const media = h("select", { disabled: !isNew }, [["none", "Nothing"], ["image", "Picture"], ["video", "Video clip"]].map(([v, l]) => h("option", { value: v, selected: cur.media_type === v }, l)));
  const cat = h("select", { disabled: !isNew }, ["utility", "authentication", "marketing"].map((v) => h("option", { value: v, selected: cur.category === v }, cap(v))));
  const btn = toggle(cur.link_button, () => {}, "View evidence button"); btn.querySelector("input").disabled = !isNew;
  const chk = h("div", { class: "note ok" });
  const vars = () => { const n = (body.value.match(/\{\{(\d+)\}\}/g) || []).length; return VARS.slice(0, n); };
  const check = () => {
    const b = body.value, nums = (b.match(/\{\{(\d+)\}\}/g) || []).map((x) => x.replace(/\D/g, "")), ok = nums.every((n, i) => n === String(i + 1)), edge = /^\s*\{\{/.test(b) || /\}\}\s*$/.test(b), promo = /\b(offer|discount|sale|buy now|free)\b/i.test(b);
    const msg = !ok ? "Use {{1}}, {{2}} ... once each, in order." : edge ? "A blank cannot be at the very start or end of the message." : promo ? "This sounds promotional. WhatsApp will reject a Utility template." : nums.length > VARS.length ? "At most four blanks are supported." : "";
    chk.className = "note " + (msg ? "err" : "ok"); mount(chk, ic(msg ? "x" : "check"), " " + (msg || "Checked: follows WhatsApp's rules."));
    cur.body_text = b; cur.media_type = media.value; cur.link_button = btn.querySelector("input").checked; mount(prev, bubble(cur));
  };
  [body, media].forEach((e) => e.addEventListener("input", check)); btn.querySelector("input").addEventListener("change", check); check();
  const status = isNew ? null : seg(Object.keys(TS).map((k) => [k, TS[k][0].replace("Waiting for approval", "Waiting")]), cur.status, (v) => { cur.status = v; });
  const why = h("input", { type: "text", placeholder: "For example: approved by WhatsApp today", autocomplete: "off", "aria-label": "Reason" });
  const err = h("div"), save = h("button", { class: "btn", type: "button", disabled: true }, isNew ? "Create template" : "Save status"), del = isNew ? null : h("button", { class: "btn danger", type: "button", disabled: true }, "Delete");
  why.oninput = () => { const ok = why.value.trim().length >= 5; save.disabled = !ok; if (del) del.disabled = !ok; };
  const run = (fn) => async () => { clear(err); try { await fn(); } catch (e) { err.append(note(e.message || errText(e), "err")); } };
  save.onclick = run(async () => {
    if (isNew) { if (chk.classList.contains("err")) throw new Error("Fix the message first."); await X.api.post("/admin/v1/messaging/templates", { key: key.value.trim(), provider_template: pt.value.trim(), language: "en", category: cat.value, body_text: body.value, media_type: media.value, link_button: btn.querySelector("input").checked, variables: vars() }); }
    else await X.api.call("PUT", `/admin/v1/messaging/templates/${t.id}/status${qs({ status: cur.status, reason: why.value.trim() })}`);
    closeSheet(); toast(isNew ? "Template created as a draft" : "Saved"); after();
  });
  if (del) del.onclick = run(async () => { await X.api.del(`/admin/v1/messaging/templates/${t.id}${qs({ reason: why.value.trim() })}`); closeSheet(); toast("Template deleted"); after(); });
  const sub = isNew ? null : h("button", { class: "btn gray block", type: "button", onclick: async () => { try { const s = await X.api.get(`/admin/v1/messaging/templates/${t.id}/submission`); submissionSheet(s); } catch (e) { toast(errText(e), true); } } }, ic("copy"), "See what to type into the provider's form");
  openSheet(isNew ? "New template" : t.key, h("div", { class: "stack" },
    isNew ? null : h("div", { class: "hero" }, h("span", { class: "ico" }, ic("doc")), h("div", { class: "grow" }, h("div", { class: "t" }, t.key), h("div", { class: "s" }, `${t.provider_template} · ${t.language}`)), pill(...[TS[t.status][0], TS[t.status][1]])),
    gh("How it looks on WhatsApp"), prev, gh("Message"), h("div", { class: "card" }, field("Our name for it", key, isNew ? "Lowercase letters, numbers and underscores." : null), field("Name at the provider", pt, "The exact name you create in the provider's form."), field("Message text", body, isNew ? "Use {{1}}, {{2}} ... once each, in order. Not at the very start or end." : "An approved template is never edited. New wording is a new template."),
      h("div", { class: "two" }, field("Top of the message", media), field("Category", cat)), h("div", { class: "gi static" }, h("div", {}, h("b", {}, "“View evidence” button"), h("small", {}, "Opens the full evidence page")), btn), chk),
    gh("What fills each blank"), group(...VARS.map((v, i) => kv(`{{${i + 1}}}`, `Alert ${v}`, `for example “${SAMPLES[i]}”`))),
    isNew ? null : [gh("Approval"), h("div", { class: "card" }, status, h("p", { class: "sub" }, "After you create it in the provider's form, set what the provider says. Only Approved templates are ever sent."), sub)],
    h("div", { class: "card" }, field("Reason (saved in the audit log)", why), err, h("div", { class: "two" }, del || h("button", { class: "btn gray", type: "button", onclick: () => closeSheet() }, "Cancel"), save))));
  sheetWide();
}

function submissionSheet(s) {
  openSheet("Type this into the provider's form", h("div", { class: "stack" }, group(kv("Name", s.name), kv("Language", s.language), kv("Category", s.category), kv("Top of message", s.header || "None"), ...(s.button ? [kv("Button", `${s.button.type} · ${s.button.text}`, `${s.button.url} · sample ${s.button.sample}`)] : [])),
    h("div", { class: "card" }, h("pre", { class: "mono" }, s.body)), gh("Samples the provider asks for"), group(...(s.samples || []).map((v, i) => kv(`{{${i + 1}}}`, v))),
    h("button", { class: "btn block", type: "button", onclick: async () => { try { await navigator.clipboard.writeText(s.body); toast("Message text copied"); } catch { toast("Select the text and copy it by hand.", true); } } }, ic("copy"), "Copy the message text")));
}

// -------------------------------------------------------------------------------------------------- screen
async function messaging(X) {
  const owner = X.isOwner, st = X.mem.log ||= { f: "all" };
  const reqs = [X.api.get(`/admin/v1/messaging/notifications${qs({ limit: 60, status: st.f === "failed" ? "failed" : undefined, tenant_id: X.mem.logTenant })}`), X.api.get("/admin/v1/messaging/summary")];
  if (owner) reqs.push(X.api.get("/admin/v1/messaging/providers"), X.api.get("/admin/v1/messaging/templates"));
  const [log, sum, providers = [], templates = []] = await Promise.all(reqs);
  X.mem.tpl = templates.map((t) => ({ key: t.key, status: t.status }));
  const out = [back("More", "#/more"), title("Messaging", owner ? "Email and WhatsApp for every customer" : "Delivery log, read only")];
  if (owner) {
    out.push(gh("Providers"), group(...(providers.length ? providers.map((p) => row({ icon: p.channel === "email" ? "doc" : "chat", title: `${cap(p.channel)} · ${p.name}`, sub: `${p.kind === "http" ? "HTTP recipe" : "SMTP"} · ${p.rate_per_second} per second · keys: ${p.secret_names.join(", ") || "none yet"}`,
        right: h("span", { class: "rowend" }, pill(p.active ? "In use" : "Off", p.active ? "ok" : ""), ic("chev", "chev")), onclick: () => providerSheet(X, p, () => X.refresh()) })) : [h("div", { class: "gi static" }, h("div", { class: "muted" }, "No provider yet. Add one to start sending."))])),
      h("div", { class: "acts2" }, h("button", { class: "btn sec2 block", type: "button", onclick: () => providerSheet(X, null, () => X.refresh()) }, ic("plus"), "Add a provider")),
      gh("WhatsApp message templates"), group(...(templates.length ? templates.map((t) => row({ icon: "doc", title: t.key, sub: `${t.provider_template} · ${t.language} · ${t.media_type === "none" ? "no picture" : t.media_type}`, right: h("span", { class: "rowend" }, pill(TS[t.status][0], TS[t.status][1]), ic("chev", "chev")), onclick: () => templateSheet(X, t, () => X.refresh()) })) : [h("div", { class: "gi static" }, h("div", { class: "muted" }, "No templates yet."))])),
      h("div", { class: "two gap" }, h("button", { class: "btn sec2", type: "button", onclick: () => templateSheet(X, null, () => X.refresh()) }, ic("plus"), "New template"),
        h("button", { class: "btn gray", type: "button", onclick: async () => { try { const r = await X.api.post("/admin/v1/messaging/templates/install-defaults"); toast(r.added.length ? `${r.added.length} standard templates added as drafts` : "The standard templates are already there"); X.refresh(); } catch (e) { toast(errText(e), true); } } }, "Add standard templates")),
      h("p", { class: "sub" }, "Only approved templates are ever sent. WhatsApp on or off, caps and clips are set per customer, on each customer's Messaging tab."));
  } else out.push(note("Providers and templates are managed by an owner.", "warn"));
  const day = sum.last_24_hours || {}, tot = (c, s) => (day[c] || {})[s] || 0;
  out.push(gh("Delivery log"), h("p", { class: "sub" }, `Last 24 hours: WhatsApp ${tot("whatsapp", "sent") + tot("whatsapp", "delivered") + tot("whatsapp", "read")} sent, ${tot("whatsapp", "failed")} failed · Email ${tot("email", "sent") + tot("email", "delivered")} sent, ${tot("email", "failed")} failed`));
  out.push(h("div", { class: "gap" }, chips([["all", "All"], ["failed", "Failed"]], st.f, (v) => { st.f = v; X.refresh(); })));
  if (X.mem.logTenant) out.push(h("div", { class: "gap" }, h("button", { class: "btn gray sm", type: "button", onclick: () => { delete X.mem.logTenant; X.refresh(); } }, ic("x"), "Showing one customer. Show everyone")));
  out.push(log.length ? h("div", { class: "card feed" }, log.map((n) => h("div", { class: "row" }, h("span", { class: "ico " + (DS[n.status] === "crit" ? "crit" : DS[n.status] === "warn" ? "warn" : "ok") }, ic(n.status === "failed" ? "bell" : "check")),
    h("div", {}, h("b", {}, n.title || n.template || "Message"), h("small", {}, `${n.tenant} · ${cap(n.channel)} · ${cap(n.status)}${n.error ? " · " + n.error : ""}`)), h("time", {}, ago(n.created_at)))))
    : empty("chat", "Nothing sent yet", "Messages appear here when alerts go out."));
  return out;
}

export const messagingScreens = { messaging };
