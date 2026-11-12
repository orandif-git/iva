// WhatsApp summaries (platform owner): the connection to the provider's Direct Send address, when the daily and weekly summaries go out, who gets them and what every line says.
// The page keeps a draft of the settings; "Save" stores it. The preview on the right is written by the server from the draft, so it is exactly what a customer would get.
import { errText, h, mount } from "../lib.js";
import { dataTable } from "../app/table.js";
import { empty, field, ic, pill, toast } from "../app/ui.js";
import { ago, back, gh, title } from "./common.js";

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const REQ = { "": "Everyone", counting: "Needs people counting", plates: "Needs a number-plate camera", queue: "Needs a queue area and the Long queue alert" };
const STATUS = { sent: ["Accepted", "ok"], queued: ["Waiting", "warn"], failed: ["Failed", "crit"], suppressed: ["Not sent", ""], cancelled: ["Cancelled", ""] };
const FILLINS = "{visitors} {change} {weekday} {busy_hour} {busy_people} {peak} {limit} {veh_in} {veh_parked} {queue_area} {queue} {cams_ok} {cams} {recs_ok} {recs} {days} {disks} {clocks} {alerts} {unseen} {hours} {visitors_w} {change_w} {busy_day} {busy_day_n} {veh_w} {problems_n}";

const sw = (checked, onchange, label) => { const i = h("input", { type: "checkbox", checked, role: "switch", "aria-label": label, onchange: () => onchange(i.checked) }); return h("label", { class: "tg" }, i, h("span")); };
const opt = (t, hint, control) => h("div", { class: "gi static" }, h("div", {}, h("b", {}, t), hint ? h("small", {}, hint) : null), control);

async function summaries(X) {
  if (!X.isOwner) return [back("More", "#/more"), title("WhatsApp summaries", ""), empty("lock", "Only an owner sees this page", "It holds the provider's key and carries customers' figures.")];
  const [s, log] = await Promise.all([X.api.get("/admin/v1/summaries"), X.api.get("/admin/v1/summaries/sends?limit=40")]);
  const st = (X.mem.sum ||= { kind: "daily", cust: "", f: "all", to: "", table: {} });
  let draft = structuredClone(s.config), saved = JSON.stringify(s.config), prevTimer = 0, seq = 0;
  st.cust ||= s.customers[0]?.id || "";
  const dirtyPill = h("span", { class: "pillx" }, "No changes"), saveBtn = h("button", { class: "btn", type: "button" }, "Save"), discardBtn = h("button", { class: "btn gray", type: "button" }, "Discard");
  const bubble = h("div", { class: "wabub" }), count = h("div", { class: "sub" }), reqBox = h("pre", { class: "mono", hidden: true });

  const touch = () => { const d = JSON.stringify(draft) !== saved; dirtyPill.textContent = d ? "Unsaved changes" : "No changes"; dirtyPill.className = "pillx " + (d ? "warn" : ""); saveBtn.disabled = !d; clearTimeout(prevTimer); prevTimer = setTimeout(preview, 450); };
  async function preview() {
    if (!st.cust) return mount(bubble, h("div", { class: "muted" }, "There is no customer to preview yet."));
    const mine = ++seq;
    try {
      const r = await X.api.post("/admin/v1/summaries/preview", { tenant_id: st.cust, kind: st.kind, config: draft });
      if (mine !== seq) return;
      mount(bubble, h("div", { class: "watext" }, r.text));
      mount(count, `${r.chars} of ${r.max} characters${r.dropped ? ` · ${r.dropped} line${r.dropped > 1 ? "s" : ""} left out to fit` : ""}`);
      reqBox.textContent = `POST ${draft.url || "(address)"}\nAuthorization: Bearer ••••\n\n${JSON.stringify(r.request, null, 2)}`;
    } catch (e) { if (mine === seq) mount(bubble, h("div", { class: "muted" }, errText(e))); }
  }
  // a text, number, time or choice bound to one setting
  const bind = (key, attrs = {}, kind = "text") => {
    const el = attrs.options ? h("select", {}, attrs.options.map(([v, l]) => h("option", { value: v, selected: String(draft[key]) === String(v) }, l))) : h("input", { type: kind, value: draft[key] ?? "", ...attrs, class: attrs.mono ? "mono" : null });
    const read = () => (kind === "number" ? (el.value === "" ? "" : Number(el.value)) : (attrs.options && typeof draft[key] === "number" ? Number(el.value) : el.value));
    el.addEventListener("input", () => { draft[key] = read(); touch(); }); el.addEventListener("change", () => { draft[key] = read(); touch(); });
    return el;
  };
  const flag = (key, t, hint) => opt(t, hint, sw(!!draft[key], (v) => { draft[key] = v; touch(); }, t));
  const two = (...n) => h("div", { class: "two gap" }, n);

  // ---- connection
  const keyBox = h("div", { class: "keyline" });
  const keyView = () => {
    mount(keyBox, [h("code", { class: "keyshow" }, s.key.set ? `••••••••••••${s.key.hint}` : "No key yet"), h("button", { class: "btn gray sm", type: "button", onclick: keyEdit }, s.key.set ? "Replace key" : "Add key")]);
  };
  const keyEdit = () => {
    const i = h("input", { type: "text", class: "mono", placeholder: "Paste the key", autocomplete: "off", "aria-label": "New API key" });
    mount(keyBox, [i, h("button", { class: "btn sm", type: "button", onclick: async () => { try { const r = await X.api.put("/admin/v1/summaries/key", { key: i.value.trim() }); s.key = r.key; s.ready = r.ready; toast("Key saved. It is stored encrypted and cannot be shown again."); keyView(); } catch (e) { toast(errText(e), true); } } }, "Use this key"),
      h("button", { class: "btn gray sm", type: "button", onclick: keyView }, "Cancel")]);
    i.focus();
  };
  keyView();
  const reachOut = h("span", { class: "sub", role: "status" });
  const connection = h("div", { class: "card stack" }, h("div", {}, h("b", {}, "Connection"), h("p", { class: "sub" }, "Where summaries are sent. The key is kept encrypted and is never shown again.")),
    field("Address", bind("url", { mono: true, placeholder: "https://…", spellcheck: "false" }), "https only."),
    h("div", { class: "field" }, h("label", {}, "API key"), keyBox, h("div", { class: "hint" }, s.key.set && s.key.changed_by ? `Sent as Authorization: Bearer … Last changed ${ago(s.key.changed_at)} by ${s.key.changed_by}.` : "Sent as Authorization: Bearer …")),
    two(field("Source label", bind("source", { maxlength: 60 }), "Shows on your WhatsApp provider's records."), field("Message category", bind("category", { options: [["utility", "utility"], ["authentication", "authentication"]] }))),
    two(field("Wait for an answer (seconds)", bind("timeout", { min: 3, max: 60 }, "number")), field("Try again after a failure (times)", bind("retries", { min: 0, max: 6 }, "number"), "Never after the provider has accepted it, so nobody gets two.")),
    two(field("Gap between messages (seconds)", bind("gap", { min: 0, step: 0.5 }, "number"), "Spreads out customers who share a send hour."), field("Longest message (characters)", bind("maxchars", { min: 300, max: 4000 }, "number"), "Lines are dropped from the end to fit.")),
    flag("enabled", "Send summaries to WhatsApp", "Off: customers still see them in the app and on their phone."),
    h("div", { class: "rowend" }, h("button", { class: "btn gray sm", type: "button", onclick: async () => { mount(reachOut, "Checking…"); try { const r = await X.api.post("/admin/v1/summaries/reach"); mount(reachOut, r.reached ? [pill(`Reached the provider (answered ${r.status}) in ${r.ms} ms`, "ok"), " The key is checked only by a test message."] : pill(r.error, "crit")); } catch (e) { mount(reachOut, pill(errText(e), "crit")); } } }, "Check the address"), reachOut));

  // ---- when and who
  const timing = h("div", { class: "card stack" }, h("div", {}, h("b", {}, "When it is sent"), h("p", { class: "sub" }, "Each customer picks their own hour in their alert settings. These are the defaults and the rules.")),
    flag("daily_on", "Daily summary", "Yesterday's numbers and today's system health."), flag("weekly_on", "Weekly summary", "The week's totals and a comparison with the week before."),
    two(field("Default daily hour", bind("daily_hour", {}, "time")), field("Weekly day", bind("weekly_day", { options: DAYS.map((d, i) => [i, d]) }))),
    two(field("Default weekly hour", bind("weekly_hour", {}, "time")), field("Time zone", bind("tz", { options: [["customer", "Each customer's own"], ["Asia/Kolkata", "India (IST)"]] }))),
    flag("replace_daily", "On the weekly day, send only the weekly one", "A customer with the weekly summary off still gets the daily one."),
    flag("cust_edit", "Customers can change their hour and switch it off", "In their alert settings in the app."), flag("default_on", "On for every customer by default", "A customer who switched it off keeps it off."));
  const who = h("div", { class: "card stack" }, h("div", {}, h("b", {}, "Who receives it"), h("p", { class: "sub" }, "The numbers come from each customer's People list. A customer's own choice wins.")),
    field("When the customer has chosen nobody", bind("recips", { options: [["owner", "The account owner"], ["admins", "All owners and admins"], ["none", "Nobody until the customer chooses"]] })),
    flag("skip_inactive", "Skip customers whose account is not active", "Trial, active and past due are included."), flag("skip_no_number", "Skip a customer with no number", "Their app and phone still get the summary."));

  // ---- what it says
  const linesBox = h("div");
  const drawLines = () => {
    const arr = draft.lines[st.kind];
    const block = (g, label, sub) => {
      const idx = arr.map((l, i) => i).filter((i) => arr[i].g === g);
      return [h("div", { class: "gh" }, label, h("span", { class: "muted" }, " · " + sub)), ...idx.map((i, pos) => {
        const l = arr[i];
        const t = h("input", { type: "text", value: l.t, "aria-label": "Wording", spellcheck: "false", oninput: () => { l.t = t.value; touch(); } });
        const swap = (a, b) => { [arr[a], arr[b]] = [arr[b], arr[a]]; drawLines(); touch(); };
        return h("div", { class: "sumline" + (l.on ? "" : " off") }, sw(l.on, (v) => { l.on = v; drawLines(); touch(); }, "Show this line"), h("div", { class: "grow" }, t, h("span", { class: "reqtag" }, REQ[l.req])),
          h("div", { class: "mv" }, h("button", { class: "ib", type: "button", "aria-label": "Move up", disabled: pos === 0, onclick: () => swap(i, idx[pos - 1]) }, "▲"), h("button", { class: "ib", type: "button", "aria-label": "Move down", disabled: pos === idx.length - 1, onclick: () => swap(i, idx[pos + 1]) }, "▼")));
      })];
    };
    mount(linesBox, [block("business", "Business", "only for customers who have it set up"), block("system", "System", "sent to everyone")]);
  };
  drawLines();
  const kindTabs = h("div", { class: "seg2", role: "group", "aria-label": "Which summary" });
  const drawTabs = () => mount(kindTabs, [["daily", "Daily"], ["weekly", "Weekly"]].map(([k, l]) => h("button", { type: "button", "aria-pressed": String(st.kind === k), onclick: () => { st.kind = k; drawTabs(); drawLines(); preview(); } }, l)));
  drawTabs();
  const heads = [0, 1, 2].map((i) => h("input", { type: "text", value: draft.heads[i], maxlength: 30, "aria-label": `Heading ${i + 1}`, oninput: (e) => { draft.heads[i] = e.target.value; touch(); } }));
  const says = h("div", { class: "card stack" }, h("div", {}, h("b", {}, "What it says"), h("p", { class: "sub" }, "A line appears only when the customer has what it needs, set up and switched on. Switch off, reword or move any line.")),
    h("div", { class: "rowend" }, kindTabs, h("button", { class: "btn gray sm", type: "button", onclick: async () => { const d = (await X.api.get("/admin/v1/summaries")).defaults; draft.lines[st.kind] = structuredClone(d[st.kind]); drawLines(); touch(); toast("Wording restored. Save to keep it."); } }, "Restore wording")),
    two(field("Title line", bind("title", { maxlength: 120 }), "Fill-ins: {kind} {date} {customer}"), field("Date style", bind("date_style", { options: [["num", "12-11-2026 (fewer templates at Meta)"], ["words", "Thu 12 Nov"]] }))),
    two(field("Headings", h("div", { class: "three" }, heads)), field("Bullet", bind("bullet", { options: [["- ", "- dash"], ["• ", "• dot"]] }))),
    linesBox, h("div", { class: "hint" }, "Fill-ins you can use: " + FILLINS),
    two(field("Most problems listed under the third heading", bind("max_problems", { min: 1, max: 8 }, "number"), "The rest show as \"+2 more\"."), field("When nothing needs a look", bind("all_fine", { maxlength: 120 }))),
    flag("show_limit", "Show the limit next to the busiest count", "\"21 people (limit 25)\". Only when their Occupancy limit alert is on."), field("Closing line", bind("closing", { maxlength: 160 })));
  const push = h("div", { class: "card stack" }, h("div", {}, h("b", {}, "Phone notification"), h("p", { class: "sub" }, "A short version for the lock screen. The full summary is on the app's Home screen.")),
    flag("push_on", "Send a phone notification with the summary"), flag("push_ta", "Add the Tamil line under the English one"));

  // ---- recent sends and the test
  const rows = log.items;
  const filterRows = () => rows.filter((r) => st.f === "all" || r.status === st.f);
  const sends = h("div", { class: "card stack" }, h("div", {}, h("b", {}, "Recent sends"), h("p", { class: "sub" }, "What was written for WhatsApp, to whom, and what the provider said. \"Accepted\" means the provider took it; it arrives a little later.")),
    h("div", { class: "chips" }, [["all", "All"], ["failed", "Failed"], ["queued", "Waiting"], ["sent", "Accepted"]].map(([k, l]) => h("button", { class: "chip", type: "button", "aria-pressed": String(st.f === k), onclick: () => { st.f = k; X.refresh(); } }, l))),
    dataTable({ label: "Recent summaries", rows: filterRows(), rowKey: (r) => r.id, memory: st.table, empty: h("p", { class: "sub" }, "Nothing sent yet. Summaries appear here when they go out."),
      columns: [
        { key: "tenant", label: "Customer", primary: true, sort: true }, { key: "kind", label: "Kind", sort: true, minor: true }, { key: "recipient", label: "To", minor: true, render: (r) => h("span", { class: "mono" }, r.recipient) },
        { key: "status", label: "Status", sort: true, render: (r) => h("span", {}, pill(STATUS[r.status][0], STATUS[r.status][1]), r.error ? h("div", { class: "hint" }, r.error.slice(0, 80)) : null) },
        { key: "id", label: "Provider ID", minor: true, render: (r) => h("span", { class: "mono" }, r.provider_message_id || "—") },
        { key: "created_at", label: "When", sort: true, value: (r) => new Date(r.created_at).getTime(), render: (r) => ago(r.created_at) },
        { key: "act", label: "", render: (r) => (r.status === "failed" ? h("button", { class: "btn sm gray", type: "button", onclick: async () => { try { await X.api.post(`/admin/v1/webhooks/deliveries/${r.id}/resend`); toast("Queued to send again"); X.refresh(); } catch (e) { toast(errText(e), true); } } }, "Send again") : null) },
      ] }));
  const to = h("input", { type: "text", inputmode: "numeric", value: st.to, placeholder: "919841542211", "aria-label": "Number to send the test to", oninput: () => { st.to = to.value; } });
  const testRes = h("div", { role: "status" });
  const custSel = (onchange) => h("select", { "aria-label": "Customer" }, s.customers.map((c) => h("option", { value: c.id, selected: c.id === st.cust }, c.name)));
  const testCust = custSel();
  testCust.onchange = () => { st.cust = testCust.value; previewSel.value = st.cust; preview(); };
  const test = h("div", { class: "card stack" }, h("div", {}, h("b", {}, "Send a test"), h("p", { class: "sub" }, "Writes the chosen customer's summary and sends it to this number now. Nothing is saved for the customer and they are not told. Starts with \"TEST\".")),
    two(field("Send to", to, "With the country code, for example 919841542211."), field("Using the customer", testCust)),
    h("div", { class: "rowend" }, h("button", { class: "btn", type: "button", onclick: async (e) => {
      const b = e.currentTarget; b.disabled = true; mount(testRes, "Sending…");
      try { const r = await X.api.post("/admin/v1/summaries/test", { tenant_id: st.cust, kind: st.kind, to: to.value.trim(), config: draft }); mount(testRes, r.ok ? pill(`The provider accepted it${r.message_id ? " · " + r.message_id : ""}. It usually arrives within 2 minutes.`, "ok") : pill(r.error || "The provider refused it.", "crit")); }
      catch (x) { mount(testRes, pill(errText(x), "crit")); } finally { b.disabled = false; }
    } }, "Send test"), testRes));

  // ---- the preview
  const previewSel = h("select", { "aria-label": "Preview as this customer" }, s.customers.map((c) => h("option", { value: c.id, selected: c.id === st.cust }, c.name)));
  previewSel.onchange = () => { st.cust = previewSel.value; testCust.value = st.cust; preview(); };
  const aside = h("aside", { class: "sumprev", "aria-label": "Preview" }, h("div", { class: "card stack" }, h("b", {}, "Preview"), h("p", { class: "sub" }, "Exactly what the owner reads, for a real customer, from the settings as they are now."),
    field("As this customer", previewSel), h("div", { class: "wafone" }, bubble), count,
    h("div", { class: "two gap" }, h("button", { class: "btn gray sm", type: "button", onclick: async () => { const t = bubble.textContent; try { await navigator.clipboard.writeText(t); toast("Copied"); } catch { toast("Select the text and copy it by hand.", true); } } }, ic("copy"), "Copy text"),
      h("button", { class: "btn gray sm", type: "button", onclick: () => { reqBox.hidden = !reqBox.hidden; } }, "Show the request")), reqBox));

  const status = s.ready.daily || s.ready.weekly ? pill("Ready to send", "ok") : pill(s.key.set ? "Not switched on" : "Needs the address and key", "warn");
  saveBtn.onclick = async () => { saveBtn.disabled = true; try { const r = await X.api.put("/admin/v1/summaries", { config: draft }); draft = structuredClone(r.config); saved = JSON.stringify(r.config); s.config = r.config; s.ready = r.ready; touch(); toast("Saved. New settings apply from the next send."); } catch (e) { toast(errText(e), true); saveBtn.disabled = false; } };
  discardBtn.onclick = () => { draft = JSON.parse(saved); X.refresh(); };
  touch();
  preview();
  const bar = h("div", { class: "sumbar" }, dirtyPill, h("div", { class: "rowend" }, discardBtn, saveBtn));
  return [back("More", "#/more"), title("WhatsApp summaries", "The daily and weekly summary goes straight to the owner's WhatsApp, written for each customer from what they have set up. Alerts keep using their webhooks.", status),
    h("div", { class: "sumcols" }, h("div", { class: "stack" }, connection, timing, who, says, push, sends, test), aside), bar];
}

export const summaryScreens = { summaries };
