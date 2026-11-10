// Marked areas on a camera picture: people in an area, a queue, and a staff area where phone use is watched.
// The customer draws the shape, names it and sets its own limits. An alert reaches people only when the condition is met, and only if the alert is on
// (Alerts > Smart Alerts). Only the kinds of area the customer's plan includes are offered.
import { errText, h, mount } from "../lib.js";
import { confirmSheet, field, ic, loadPicture, note, toast } from "./ui.js";

const COLOR = { area_count: "#35C6F4", queue_area: "#FFD23F", staff_area: "#FF7A59" };
const WORDS = {
  area_count: { name: "People in an area", hint: "Counts the people standing in the shape. Tells you when there are too many.", labels: { limit: ["Alert when there are at least", "people"], minutes: ["for", "minutes"] } },
  queue_area: { name: "Queue", hint: "Counts the people waiting and how long the longest wait has been.", labels: { length: ["Alert when the queue has at least", "people"], minutes: ["for", "minutes"], wait_minutes: ["or when someone has waited", "minutes (0 to ignore waiting time)"] } },
  staff_area: { name: "Staff area (phone use)", hint: "Watches for a phone in use where only staff stand, for example behind the counter. It names the area, never a person.", labels: { seconds: ["Alert when a phone has been in use for", "seconds"] } },
};
const REPEAT = ["repeat_minutes", "Do not tell me again for", "minutes"];
const round = (v) => Math.round(Math.min(1, Math.max(0, v)) * 1000) / 1000;
const summary = (a) => {
  const p = a.params;
  if (a.kind === "area_count") return `At least ${p.limit} people for ${p.minutes} min`;
  if (a.kind === "queue_area") return `At least ${p.length} people for ${p.minutes} min${p.wait_minutes ? `, or a wait of ${p.wait_minutes} min` : ""}`;
  return `A phone in use for ${p.seconds} s`;
};

/** The "Marked areas" card for one camera, or null when this customer has no kind of area to draw. */
export async function areasCard(X, cam) {
  const { api } = X;
  const [kinds, saved] = await Promise.all([api.get("/v1/area-kinds"), api.get(`/v1/cameras/${cam.id}/areas`)]);
  if (!kinds.length && !saved.length) return null;
  const areas = saved.map((a) => ({ ...a }));
  const ed = { cur: null, mode: false };                       // the area being added or changed
  const frame = h("div", { class: "scene editor" }), holder = h("div", { class: "pic" });
  const over = h("svg:svg", { class: "over", viewBox: "0 0 1 1", preserveAspectRatio: "none" });
  const form = h("div", { class: "stack" }), list = h("div", { class: "group" }), help = h("p", { class: "sub" });
  const pt = (e) => { const r = over.getBoundingClientRect(); return [round((e.clientX - r.left) / r.width), round((e.clientY - r.top) / r.height)]; };

  const paint = () => {
    mount(over);
    const shapes = [...areas.filter((a) => !ed.cur || a.id !== ed.cur.id), ...(ed.cur ? [ed.cur] : [])];
    for (const a of shapes) {
      if (!a.points.length) continue;
      const c = COLOR[a.kind], pts = a.points.map((p) => p.join(",")).join(" ");
      over.append(a.points.length >= 3 ? h("svg:polygon", { points: pts, fill: c + "30", stroke: c, "stroke-width": ".006" }) : h("svg:polyline", { points: pts, fill: "none", stroke: c, "stroke-width": ".006" }),
        ...a.points.map((p) => h("svg:circle", { cx: p[0], cy: p[1], r: 0.012, fill: c })), h("svg:text", { x: a.points[0][0], y: Math.max(0.04, a.points[0][1] - 0.015), fill: "#fff", "font-size": ".035" }, a.name || ""));
    }
  };
  over.addEventListener("pointerdown", (e) => { if (ed.mode && ed.cur) { ed.cur.points.push(pt(e)); paint(); drawHelp(); } });
  const drawHelp = () => { help.textContent = ed.mode ? `Tap each corner of the area on the picture (${ed.cur.points.length} so far). At least three.` : ed.cur ? "" : "Add an area to count people, watch a queue, or watch for phone use in a staff area."; };

  const showPicture = (fresh) => {
    mount(holder, h("div", { class: "none" }, "Getting a picture…"));
    loadPicture(api, cam.id, { fresh }).then((url) => mount(holder, h("img", { src: url, alt: `Latest picture from ${cam.name}`, draggable: "false", onload: (e) => { frame.style.aspectRatio = `${e.target.naturalWidth} / ${e.target.naturalHeight}`; } })), (e) => mount(holder, h("div", { class: "none" }, errText(e))));
  };

  const body = (a) => ({ ...(a.id ? { id: a.id } : {}), kind: a.kind, name: a.name, points: a.points, params: a.params });
  const send = async (next, done) => {
    try { await api.put(`/v1/cameras/${cam.id}/areas`, { areas: next.map(body) }); toast(done); X.refresh(); } catch (e) { toast(errText(e), true); throw e; }
  };

  const drawList = () => mount(list, areas.length ? areas.map((a) => h("div", { class: "gi static" }, h("div", {}, h("b", {}, a.name), h("small", {}, `${WORDS[a.kind].name} · ${summary(a)}`)),
    h("span", { class: "rowend" }, h("button", { class: "ib", type: "button", "aria-label": `Change ${a.name}`, onclick: () => edit(a) }, ic("edit")),
      h("button", { class: "ib dng", type: "button", "aria-label": `Remove ${a.name}`, onclick: () => confirmSheet(`Remove ${a.name}?`, "Its alerts stop. Past alerts stay in your list.", "Remove", () => send(areas.filter((x) => x.id !== a.id), "Area removed")) }, ic("trash")))))
    : h("div", { class: "gi static" }, h("div", { class: "muted" }, "No areas yet.")));

  function edit(a) {
    ed.cur = a ? { ...a, points: a.points.map((p) => [...p]), params: { ...a.params } } : { kind: kinds[0].kind, name: "", points: [], params: {} };
    ed.mode = !a;
    drawForm(); paint(); drawHelp();
  }

  function drawForm() {
    const a = ed.cur;
    if (!a) return mount(form, h("button", { class: "btn sec2", type: "button", onclick: () => edit(null) }, ic("plus"), "Add an area"));
    const kind = kinds.find((k) => k.kind === a.kind) || kinds[0];
    const isNew = !a.id;
    const picker = isNew && kinds.length > 1 ? h("div", { class: "group" }, kinds.map((k) => h("label", { class: "gi check" }, h("div", {}, h("b", {}, WORDS[k.kind].name), h("small", {}, WORDS[k.kind].hint)),
      h("input", { type: "radio", name: "kind", checked: k.kind === a.kind, onchange: () => { a.kind = k.kind; a.params = {}; drawForm(); paint(); } })))) : h("p", { class: "sub" }, `${WORDS[a.kind].name}. ${WORDS[a.kind].hint}`);
    const name = h("input", { type: "text", maxlength: 60, value: a.name, placeholder: "For example Billing counter", oninput: () => { a.name = name.value; paint(); } });
    const inputs = {};
    const fields = Object.entries(kind.settings).filter(([k]) => k !== "repeat_minutes").map(([k, spec]) => {
      const [lead, unit] = WORDS[a.kind].labels[k] || [k, ""];
      const input = h("input", { type: "number", inputmode: "numeric", min: spec.min, max: spec.max, value: a.params[k] ?? spec.default ?? "", placeholder: spec.default === null ? "Required" : "" });
      inputs[k] = () => (input.value === "" ? undefined : Number(input.value));
      return field(`${lead} (${unit})`, input);
    });
    const rep = kind.settings.repeat_minutes, repIn = h("input", { type: "number", inputmode: "numeric", min: rep.min, max: rep.max, value: a.params.repeat_minutes ?? rep.default });
    const ok = a.kind === "staff_area" ? h("input", { type: "checkbox", checked: !!a.params.notice_confirmed, "aria-label": "My staff have been told" }) : null;
    const err = h("div"), save = h("button", { class: "btn", type: "button" }, isNew ? "Save area" : "Save changes");
    save.onclick = async () => {
      if (a.points.length < 3) return mount(err, note("Tap at least three corners on the picture to mark the area.", "err"));
      if (!a.name.trim()) return mount(err, note("Give the area a name.", "err"));
      const params = Object.fromEntries(Object.entries(inputs).map(([k, get]) => [k, get()]).filter(([, v]) => v !== undefined));
      params.repeat_minutes = Number(repIn.value);
      if (ok) params.notice_confirmed = ok.checked;
      save.disabled = true;
      const next = a.id ? areas.map((x) => (x.id === a.id ? { ...a, name: a.name.trim(), params } : x)) : [...areas, { ...a, name: a.name.trim(), params }];
      try { await send(next, "Saved. The camera uses it within a minute."); } catch (e) { mount(err, note(errText(e), "err")); save.disabled = false; }
    };
    mount(form, h("div", { class: "card stack" }, picker, field("Name", name),
      h("div", { class: "acts" }, h("button", { class: "btn gray sm", type: "button", onclick: () => { a.points = []; ed.mode = true; paint(); drawHelp(); } }, ic("line"), a.points.length ? "Draw the shape again" : "Draw the shape"),
        ed.mode && a.points.length >= 3 ? h("button", { class: "btn gray sm", type: "button", onclick: () => { ed.mode = false; drawHelp(); } }, "Done drawing") : null),
      ...fields, field(`${REPEAT[1]} (${REPEAT[2]})`, repIn, "After an alert for this area, the next one waits at least this long."),
      ok ? h("label", { class: "gi check" }, h("div", {}, h("b", {}, "My staff have been told that this area is watched"), h("small", {}, "Monitoring staff needs a lawful reason and notice to them. The alert names the area, never a person.")), ok) : null,
      err, h("div", { class: "acts" }, save, h("button", { class: "btn gray", type: "button", onclick: () => { ed.cur = null; ed.mode = false; drawForm(); paint(); drawHelp(); } }, "Cancel"))));
  }

  mount(frame, holder, over);
  showPicture(false);
  drawList(); drawForm(); paint(); drawHelp();
  return h("div", { class: "card" }, h("h3", {}, "Marked areas"), h("p", { class: "sub" }, "Mark a part of this camera's picture. Set the limit for each area; you are told only when it is reached."),
    frame, help, list, form);
}
