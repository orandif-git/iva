// Recorders (connect, details, rename, delete), reports and the More tab.
import { ago, clear, copy, errText, fmtDateTime, h, istDay, mount } from "../lib.js";
import { dataTable } from "./table.js";
import { chips, confirmSheet, empty, field, ic, MIN_REFRESH, note, onSheetClose, openSheet, closeSheet, picInterval, pill, sec, seg, setPicInterval, toast, toggle } from "./ui.js";

const LINK = { online: ["Online", "ok"], offline: ["Offline", "crit"], never: ["Waiting to connect", ""] };
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const head = (t, sub, right) => h("div", { class: "pagehead" }, h("div", {}, h("h1", { class: "lt" }, t), sub ? h("p", { class: "sub" }, sub) : null), right || null);
const back = (href, label) => h("a", { class: "backlink" + (href === "#/more" ? " only-phone" : ""), href }, ic("chev"), label);
const recName = (r) => r.name || r.device_id;

/** A list of label / value lines with a copy button on each. */
function credList(rows) {
  return h("div", { class: "cred" }, rows.map(([label, value]) => [h("dt", {}, label), h("dd", {}, value),
    h("button", { class: "btn gray sm", type: "button", "aria-label": "Copy " + label, onclick: () => copy(value, label + " copied") }, ic("copy"))]));
}

async function recorders(X) {
  const recs = await X.api.get("/v1/recorders");
  const out = [back("#/more", "More"), head("Recorders", `${recs.length} connected`, X.isAdmin ? h("a", { class: "btn sm sec2", href: "#/recorders/new" }, ic("plus"), "Connect") : null)];
  out.push(recs.length ? dataTable({
    label: "Recorders", rows: recs, rowKey: (r) => r.id, memory: (X.mem.recs ||= {}), sort: { key: "name", dir: "asc" },
    href: (r) => "#/recorders/" + r.id, onOpen: (r) => X.go("#/recorders/" + r.id),
    columns: [
      { key: "name", label: "Recorder", primary: true, sort: true, value: recName, render: recName },
      { key: "brand", label: "Brand", sort: true, minor: true, render: (r) => cap(r.brand) },
      { key: "channel_count", label: "Cameras", sort: true, align: "end" },
      { key: "seen", label: "Last heard", sort: true, minor: true, value: (r) => (r.last_heartbeat_at ? new Date(r.last_heartbeat_at).getTime() : null), render: (r) => ago(r.last_heartbeat_at) },
      { key: "status", label: "Status", sort: true, render: (r) => { const [t, k] = LINK[r.status] || LINK.never; return pill(t, k); } },
    ],
  }) : empty("server", "No recorders yet", "Connect your Dahua recorder to start counting people and reading plates.", X.isAdmin ? h("a", { class: "btn", href: "#/recorders/new" }, "Connect a recorder") : null));
  return out;
}

// ------------------------------------------------------------------------------------------------ connect wizard
function newRecorder(X) {
  if (!X.isAdmin) return [back("#/recorders", "Recorders"), note("Only an administrator can connect a recorder.", "warn")];
  const box = h("div", { class: "stack" });
  let created = null, stop = false;
  const leave = (e) => { if (created && !created.done) { e.preventDefault(); e.returnValue = ""; } };
  addEventListener("beforeunload", leave);
  X.onLeave(() => { stop = true; removeEventListener("beforeunload", leave); });

  const step1 = () => {
    const name = h("input", { type: "text", maxlength: 60, placeholder: "for example Front office", autocomplete: "off" });
    const go = h("button", { class: "btn block", type: "button" }, "Continue with Dahua");
    go.onclick = async () => {
      go.disabled = true;
      try { created = await X.api.post("/v1/recorders", { brand: "dahua", ...(name.value.trim() ? { name: name.value.trim() } : {}) }); step2(); }
      catch (e) { toast(errText(e), true); go.disabled = false; }
    };
    mount(box, h("div", { class: "card stack" }, h("h3", {}, "Connect your Dahua recorder"), go,
      field("Give it a name (optional)", name, "Helps you tell recorders apart."),
      h("p", { class: "sub" }, "Your recorder connects out to us over the internet. You do not need a static IP, a VPN or any port forwarding.")));
  };

  const steps = [["reached", "Our server heard from your recorder"], ["key_ok", "The ID and password matched"], ["channels_found", "We found your cameras"], ["preview_ok", "We received a picture"]];
  const step2 = () => {
    const c = created;
    const checks = h("div"), next = h("div");
    const draw = (s) => {
      mount(checks, steps.map(([k, label]) => h("div", { class: "check" }, h("span", { class: "tick" + (s.checks[k] ? " on" : "") }, s.checks[k] ? ic("check") : null), label)));
      if (s.checks.channels_found) { c.done = true; mount(next, h("a", { class: "btn block", href: "#/recorders/" + c.recorder_id }, "Choose your cameras")); }
    };
    draw({ checks: {} });
    (async () => { while (!stop) { try { draw(await X.api.get(`/v1/recorders/${c.recorder_id}/status`)); } catch { /* keep trying */ } await new Promise((r) => setTimeout(r, 3000)); } })();
    mount(box,
      h("div", { class: "card" }, h("h3", {}, "1. Type these into your recorder"), credList([["Server address", c.server], ["Port", String(c.port)], ["Sub-Device ID", c.device_id], ["Viewer user", c.viewer_user || "giview"], ["Viewer password", c.key]]),
        note("Save the password now. For your security it is shown only once. If you lose it you can make a new one.", "warn")),
      h("div", { class: "card" }, h("h3", {}, "2. On the recorder"), h("div", { class: "stepn" }, h("em", {}, "1"), h("div", {}, "Open ", h("b", {}, "Network › Register"), " (called Auto Register on some models).")),
        h("div", { class: "stepn" }, h("em", {}, "2"), h("div", {}, "Switch it on. Enter the server address and port, and set ", h("b", {}, "Sub-Device ID"), " exactly as above. Save.")),
        h("div", { class: "stepn" }, h("em", {}, "3"), h("div", {}, "Open ", h("b", {}, "Account"), ". Add a user with the viewer name and password above, with ", h("b", {}, "live view only"), ".")),
        h("p", { class: "sub" }, "Do not use your administrator account here. This separate user can only look.")),
      h("div", { class: "card" }, h("h3", {}, "3. Connection check"), checks, h("p", { class: "sub" }, "This updates by itself, usually within a minute."), next));
  };
  step1();
  return [back("#/recorders", "Recorders"), head("Connect a recorder"), box];
}

// ------------------------------------------------------------------------------------------------ recorder page
async function recorderPage(X, id) {
  const { api } = X;
  const [r, chans, cams] = await Promise.all([api.get("/v1/recorders/" + id), api.get(`/v1/recorders/${id}/channels`), api.get("/v1/cameras")]);
  const [t, k] = LINK[r.status] || LINK.never;
  const feats = X.state.me.tenant.features || {};
  const out = [back("#/recorders", "Recorders"), head(recName(r), `${cap(r.brand)} · last seen ${ago(r.last_heartbeat_at)}`, pill(t, k))];

  const setup = (c) => {
    const name = h("input", { type: "text", value: c.name || "Camera " + c.channel_no, maxlength: 120 });
    const foot = h("input", { type: "checkbox", checked: true, id: "uf" }), anpr = h("input", { type: "checkbox", id: "ua", disabled: !feats.anpr });
    const err = h("div"), btn = h("button", { class: "btn", type: "button" }, "Add camera");
    btn.onclick = async () => {
      btn.disabled = true; clear(err);
      try {
        const cam = await api.post("/v1/cameras", { recorder_id: id, channel_no: c.channel_no, name: name.value, use_footfall: foot.checked, use_anpr: anpr.checked });
        closeSheet(); toast("Camera added. Now place the counting line."); X.go("#/cameras/" + cam.id);
      } catch (e) { err.append(note(errText(e), "err")); btn.disabled = false; }
    };
    openSheet("Set up camera " + c.channel_no, field("Name", name, "For example Main gate or Shop entrance."),
      h("label", { class: "check" }, foot, "Count people going in and out"), h("label", { class: "check" }, anpr, "Read number plates" + (feats.anpr ? "" : " (not in your plan)")), err, h("div", { class: "acts" }, btn));
  };
  out.push(h("div", { class: "card" }, h("h3", {}, "Cameras on this recorder"), chans.length ? chans.map((c) => {
    const cam = cams.find((x) => x.recorder_id === id && x.channel_no === c.channel_no);
    return h("div", { class: "rowlink" }, h("span", { class: "ico" }, ic("cam")), h("div", {}, h("b", {}, c.name || "Channel " + c.channel_no), h("small", {}, "Channel " + c.channel_no)),
      cam ? h("a", { class: "btn gray sm", href: "#/cameras/" + cam.id }, "Open") : X.isAdmin && c.active ? h("button", { class: "btn sm", type: "button", onclick: () => setup(c) }, "Set up") : h("span"));
  }) : h("p", { class: "sub" }, "No cameras yet. They appear when the recorder connects.")));
  if (!X.isAdmin) return out;

  const rename = () => {
    const name = h("input", { type: "text", value: r.name || "", maxlength: 60, placeholder: r.device_id });
    const err = h("div"), btn = h("button", { class: "btn", type: "button" }, "Save");
    btn.onclick = async () => { btn.disabled = true; try { await api.patch("/v1/recorders/" + id, { name: name.value.trim() || null }); closeSheet(); toast("Renamed."); X.refresh(); } catch (e) { clear(err); err.append(note(errText(e), "err")); btn.disabled = false; } };
    openSheet("Rename recorder", field("Name", name, "Leave empty to show the recorder ID instead."), err, h("div", { class: "acts" }, btn));
  };
  const details = async () => {
    try {
      const c = await api.get(`/v1/recorders/${id}/connection`);
      openSheet("Connection details", h("p", { class: "sub" }, "Type these into the recorder's Register page if you ever need to set it up again. The password is never shown here; make a new one if you lost it."),
        credList([["Server address", c.server], ["Port", String(c.port)], ["Sub-Device ID", c.device_id], ["Viewer user", c.viewer_user || "giview"]]));
    } catch (e) { toast(errText(e), true); }
  };
  const rotate = () => confirmSheet("Make a new password?", "This replaces the viewer password. You must type the new one into the recorder, and the recorder stays disconnected until you do.", "Make new password", async () => {
    const c = await api.post(`/v1/recorders/${id}/rotate-key`);
    setTimeout(() => openSheet("New viewer password", credList([["Viewer user", c.viewer_user || "giview"], ["Viewer password", c.key]]), note("Shown only once. Type it into the recorder's Account page for this user.", "warn")), 450);
  });
  const del = () => confirmSheet(`Delete ${recName(r)}?`, `This removes the recorder and its ${chans.length} camera channels. The counts and number plate reads from its cameras are permanently deleted and cannot be brought back. Alerts you already received stay. The recorder will be refused if it tries to connect again.`, "Delete recorder", async () => { await api.del("/v1/recorders/" + id); toast("Recorder deleted."); X.go("#/recorders"); });
  out.push(h("div", { class: "group" },
    h("button", { class: "gi", type: "button", onclick: rename }, ic("edit"), h("div", {}, "Rename"), ic("chev", "chev")),
    h("button", { class: "gi", type: "button", onclick: details }, ic("doc"), h("div", {}, "Connection details", h("small", {}, "Server, port and IDs to type into the recorder")), ic("chev", "chev")),
    h("button", { class: "gi", type: "button", onclick: rotate }, ic("key"), h("div", {}, "Make a new password"), ic("chev", "chev"))),
    h("div", { class: "acts" }, h("button", { class: "btn danger", type: "button", onclick: del }, ic("trash"), "Delete recorder")));
  return out;
}

// ------------------------------------------------------------------------------------------------ reports
const REPORTS = { gate_register: "Gate register", footfall_summary: "Footfall summary", vehicle_audit: "Vehicle audit" };
async function reports(X) {
  const list = await X.api.get("/v1/reports?limit=30");
  const out = [back("#/more", "More"), head("Reports", "Spreadsheets you can share")];
  if (X.isAdmin) {
    const type = h("select", {}, Object.entries(REPORTS).map(([k, l]) => h("option", { value: k }, l)));
    const from = h("input", { type: "date", value: istDay(-6).slice(0, 10) }), to = h("input", { type: "date", value: istDay().slice(0, 10) });
    const btn = h("button", { class: "btn", type: "button" }, "Create report");
    btn.onclick = async () => { btn.disabled = true; try { await X.api.post("/v1/reports", { report_type: type.value, from: from.value, to: to.value }); toast("Report requested. It appears below in a moment."); X.refresh(); } catch (e) { toast(errText(e), true); btn.disabled = false; } };
    out.push(h("div", { class: "card" }, h("h3", {}, "New report"), field("Type", type), field("From", from), field("To", to), btn));
  }
  out.push(h("div", { class: "card" }, h("h3", {}, "Your reports"), list.length ? list.map((r) => {
    const ready = r.status === "ready", [t, kind] = ready ? ["Ready", "ok"] : r.status === "failed" ? ["Failed", "crit"] : ["Preparing", "warn"];
    return h("div", { class: "rowlink" }, h("span", { class: "ico" }, ic("doc")), h("div", {}, h("b", {}, r.title), h("small", {}, `${fmtDateTime(r.period_start)} – ${fmtDateTime(r.period_end)}`)),
      ready ? h("button", { class: "btn sm", type: "button", onclick: () => download(X, r) }, ic("down"), "Download") : pill(t, kind));
  }) : h("p", { class: "sub" }, "No reports yet.")));
  return out;
}
async function download(X, r) {
  try {
    const blob = await X.api.get(`/v1/reports/${r.id}/download`, { blob: true });
    const a = h("a", { href: URL.createObjectURL(blob), download: `${r.title}.xlsx` });
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  } catch (e) { toast(errText(e), true); }
}

// ------------------------------------------------------------------------------------------------ More
async function more(X) {
  const { state } = X, me = state.me;
  const out = [head("More", me.tenant.name)];
  const row = (icon, label, sub, href) => h("a", { class: "gi", href }, ic(icon), h("div", {}, label, sub ? h("small", {}, sub) : null), ic("chev", "chev"));
  out.push(h("div", { class: "group" }, row("server", "Recorders", "Connect, rename or remove", "#/recorders"), row("doc", "Reports", "Spreadsheets to download or share", "#/reports")));
  out.push(h("div", { class: "gh" }, "Appearance"), h("div", { class: "card" }, seg([["light", "Light"], ["auto", "Auto"], ["dark", "Dark"]], X.theme.get(), (v) => X.theme.set(v))));
  const cur = picInterval(), presets = [["0", "Off"], ["3", "3 s"], ["5", "5 s"], ["10", "10 s"], ["30", "30 s"], ["60", "1 min"]];
  const custom = h("input", { type: "number", min: MIN_REFRESH, max: 3600, inputmode: "numeric", placeholder: `Other, ${MIN_REFRESH} or more`, "aria-label": "Seconds between pictures", value: presets.some((x) => x[0] === String(cur)) ? "" : cur });
  const preset = chips(presets, String(cur), (v) => { custom.value = ""; setPicInterval(Number(v)); toast(Number(v) ? `Pictures refresh every ${v} seconds` : "Pictures no longer refresh by themselves", false); });
  custom.onchange = () => {
    const n = Number(custom.value);
    if (!n) { custom.value = ""; return; }
    const v = setPicInterval(n); custom.value = v; preset.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", "false"));
    toast(`Pictures refresh every ${v} seconds`, false);
  };
  out.push(h("div", { class: "gh" }, "Camera pictures"), h("div", { class: "card stack" }, h("div", {}, h("b", {}, "Refresh every"), h("p", { class: "sub" }, `While a page is open, pictures update on their own. The shortest is ${MIN_REFRESH} seconds. Faster uses more of the recorder's internet. Saved on this device.`)), preset, field("Or type your own (seconds)", custom)));
  if (X.install.available()) out.push(h("div", { class: "gh" }, "App"), h("div", { class: "group" }, h("button", { class: "gi", type: "button", onclick: () => X.install.prompt() }, ic("phone"), h("div", {}, "Install Jas iVision", h("small", {}, "Open it from your home screen like an app")), ic("chev", "chev"))));
  out.push(h("div", { class: "gh" }, "Your account"), h("div", { class: "card" },
    h("p", {}, h("b", {}, me.user.name), h("br"), h("span", { class: "sub" }, `${me.user.role === "admin" ? "Administrator" : "Viewer"} · plan ${me.tenant.plan_name}`)),
    h("p", { class: "sub" }, `Cameras in use: ${me.tenant.cameras_in_use} of ${me.tenant.camera_limit}`),
    h("div", { class: "acts" }, h("button", { class: "btn gray", type: "button", onclick: () => passwordSheet(X) }, ic("lock"), "Change password"), h("button", { class: "btn gray", type: "button", onclick: () => X.signOut() }, "Sign out"))));
  out.push(h("p", { class: "sub center" }, "Jas iVision · Intelligent Vision. Automated Insights."));
  return out;
}
function passwordSheet(X) {
  const cur = h("input", { type: "password", autocomplete: "current-password" }), a = h("input", { type: "password", autocomplete: "new-password", minlength: 10 });
  const err = h("div"), btn = h("button", { class: "btn", type: "button" }, "Change password");
  btn.onclick = async () => { btn.disabled = true; clear(err); try { await X.api.post("/v1/auth/change-password", { current_password: cur.value, new_password: a.value }); closeSheet(); toast("Password changed. Other devices will need to sign in again."); } catch (e) { err.append(note(errText(e), "err")); btn.disabled = false; } };
  openSheet("Change password", field("Current password", cur), field("New password", a, "At least 10 characters. A few words together is easy to remember and hard to guess."), err, h("div", { class: "acts" }, btn));
}

export const recorderScreens = { recorders, newRecorder, recorderPage, reports, more };
