// The customer's alert rules: System Alerts and Smart Alerts (the AI ones, always shown with the sparkle).
// A customer only ever gets the alerts their plan includes, so this lists whatever the server sends and nothing else.
// Alerts go out by WhatsApp, so "who is told" means people with a mobile number.
import { errText, h, mount } from "../lib.js";
import { closeSheet, empty, field, ic, note, openSheet, toast, toggle } from "./ui.js";

const PARAMS = { people: ["Alert above", "people", 1, 100000], max_hours: ["Alert after", "hours", 1, 168] };

/** A title with the AI sparkle in front when the alert is a Smart Alert. */
export const alertName = (t) => h("span", { class: "ttl" }, t.ai ? ic("spark", "spark") : null, t.title);

const save = (X, t, body) => X.api.put("/v1/alert-types/" + t.type, { enabled: t.enabled, params: t.params, recipient_ids: t.recipient_ids, quiet_start: t.quiet_start, quiet_end: t.quiet_end, ...body });

/** The rules of one family, grouped, each with a switch. Tapping a row opens its settings. */
export function ruleGroups(X, types, redraw) {
  if (!types.length) return empty("bell", "Nothing here yet", "Alerts your plan includes will appear here.");
  const out = [];
  let last = null;
  for (const t of types) {
    if (t.group !== last) { out.push(h("div", { class: "gh" }, t.group)); last = t.group; }
    const sw = toggle(t.enabled, async (i) => {
      if (!X.isAdmin) { i.checked = t.enabled; return toast("Only an administrator can change alerts.", true); }
      try {
        const r = await save(X, t, { enabled: i.checked });
        Object.assign(t, r);
        toast(r.warnings?.[0] || (i.checked ? "Alert turned on." : "Alert turned off."));
      } catch (e) { i.checked = t.enabled; toast(errText(e), true); }
    }, t.title);
    out.push(h("div", { class: "group" }, h("div", { class: "gi" }, h("button", { class: "rulemain", type: "button", onclick: () => ruleSheet(X, t, redraw), "aria-label": `${t.title} settings` },
      h("b", {}, alertName(t)), h("small", {}, t.description)), sw)));
  }
  return h("div", { class: "stack" }, out);
}

/** The settings of one alert: its own numbers, who is told, and quiet hours. */
export async function ruleSheet(X, t, redraw) {
  let people = [];
  try { people = (await X.api.get("/v1/recipients")).filter((p) => p.phone && p.active); } catch (e) { return toast(errText(e), true); }
  const chosen = new Set(t.recipient_ids);
  const fields = [];
  const inputs = {};
  for (const [k, spec] of Object.entries(t.settings || {})) {
    if (spec.type === "boolean") {
      const tg = toggle(t.params[k] ?? spec.default, () => {}, "Send a short video clip");
      inputs[k] = () => tg.querySelector("input").checked;
      fields.push(h("div", { class: "gi static" }, h("div", {}, h("b", {}, "Send a short video clip"), h("small", {}, "A few seconds of video with the alert.")), tg));
    } else {
      const [label, unit, min, max] = PARAMS[k] || [k, "", spec.min, spec.max];
      const input = h("input", { type: "number", min, max, step: spec.type === "integer" ? 1 : "any", inputmode: "decimal", value: t.params[k] ?? spec.default });
      inputs[k] = () => Number(input.value);
      fields.push(field(`${label} (${unit})`, input));
    }
  }
  const who = h("div", { class: "group" }, people.length ? people.map((p) => {
    const box = h("input", { type: "checkbox", checked: chosen.has(p.id), "aria-label": p.name, onchange: () => (box.checked ? chosen.add(p.id) : chosen.delete(p.id)) });
    return h("label", { class: "gi check" }, h("div", {}, h("b", {}, p.name), h("small", {}, p.phone_masked)), box);
  }) : h("div", { class: "gi static" }, h("div", { class: "muted" }, "No one has a mobile number yet.")));
  const qs = h("input", { type: "time", value: t.quiet_start || "" }), qe = h("input", { type: "time", value: t.quiet_end || "" });
  const err = h("div"), btn = h("button", { class: "btn", type: "button" }, "Save");
  btn.onclick = async () => {
    btn.disabled = true;
    try {
      const params = Object.fromEntries(Object.entries(inputs).map(([k, get]) => [k, get()]));
      const r = await save(X, t, { params, recipient_ids: [...chosen], quiet_start: qs.value || null, quiet_end: qe.value || null });
      Object.assign(t, r);
      closeSheet(); toast(r.warnings?.[0] || "Saved."); redraw();
    } catch (e) { mount(err, note(errText(e), "err")); btn.disabled = false; }
  };
  openSheet(t.title, h("p", { class: "sub" }, t.description), fields,
    h("div", { class: "gh" }, "Who is told"), who, h("p", { class: "sub" }, "Alerts are sent by WhatsApp to the mobile number of each person you choose."),
    X.isAdmin ? h("button", { class: "btn gray sm", type: "button", onclick: () => personSheet(X, () => ruleSheet(X, t, redraw)) }, ic("plus"), "Add a person") : null,
    h("div", { class: "gh" }, "Quiet hours"), h("div", { class: "two" }, field("From", qs), field("Until", qe)), h("p", { class: "sub" }, "No messages in these hours. The alert is still kept in the app."),
    err, h("div", { class: "acts" }, X.isAdmin ? btn : null, h("button", { class: "btn gray", type: "button", onclick: () => closeSheet() }, X.isAdmin ? "Cancel" : "Close")));
}

/** Add a person who can be told. The customer confirms the person agreed to receive WhatsApp messages. */
export function personSheet(X, done) {
  const name = h("input", { type: "text", maxlength: 80, placeholder: "For example Ravi" }), phone = h("input", { type: "tel", inputmode: "tel", placeholder: "98765 43210" });
  const ok = h("input", { type: "checkbox", "aria-label": "This person agreed to receive WhatsApp messages" });
  const err = h("div"), btn = h("button", { class: "btn", type: "button" }, "Add person");
  btn.onclick = async () => {
    btn.disabled = true;
    try { await X.api.post("/v1/recipients", { name: name.value.trim(), phone: phone.value.trim(), consent_confirmed: ok.checked }); closeSheet(); toast("Person added."); done(); }
    catch (e) { mount(err, note(errText(e), "err")); btn.disabled = false; }
  };
  openSheet("Add a person", field("Name", name), field("Mobile number", phone, "They will get alerts by WhatsApp."),
    h("label", { class: "gi check" }, h("div", {}, h("b", {}, "This person agreed to receive WhatsApp messages from us")), ok), err, h("div", { class: "acts" }, btn, h("button", { class: "btn gray", type: "button", onclick: () => closeSheet() }, "Cancel")));
}
