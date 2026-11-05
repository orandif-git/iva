// Customer app: sign in, connect a recorder, set up cameras and counting lines, see footfall and number plates.
import { Api, ApiError, BRAND, LOGO, ago, barChart, busy, chip, clear, copyRow, errText, field, fmtDateTime, fmtNum, h, icon, istDay, modal, mount, note, qs, toast } from "./lib.js";

const app = document.getElementById("app");
const api = new Api({ refreshUrl: "/v1/auth/refresh", key: "gi_rt", onSignedOut: () => { state.me = null; signIn(); } });
const state = { me: null, view: 0 };
const isAdmin = () => state.me?.user.role === "admin";
const STATE = { streaming: ["Live", "ok"], reconnecting: ["Reconnecting", "warn"], offline: ["Offline", "crit"], unknown: ["Waiting", ""] };
const LINK = { online: ["Online", "ok"], offline: ["Offline", "crit"], never: ["Waiting to connect", ""] };

// ------------------------------------------------------------------------------------------------ sign-in
function authShell(title, ...body) {
  mount(app, h("div", { class: "auth" }, h("div", { class: "box" },
    h("div", { class: "brand" }, h("i", {}, icon(LOGO)), BRAND.name), h("p", { class: "tagline" }, BRAND.tagline),
    h("div", { class: "card" }, h("h1", {}, title), h("div", { class: "stack" }, body)))));
}

function signIn(message) {
  const email = h("input", { type: "email", autocomplete: "username", required: true, autofocus: true });
  const pw = h("input", { type: "password", autocomplete: "current-password", required: true });
  const org = h("input", { type: "text", autocomplete: "organization", placeholder: "for example sharma-auto" });
  const orgField = field("Organisation short name", org, "Your email is used in more than one organisation. Ask your administrator for the short name.");
  orgField.classList.add("hide");
  const err = h("div");
  const btn = h("button", { class: "btn primary", type: "submit" }, "Sign in");
  const form = h("form", { onsubmit: busy(btn, async (e) => {
    e.preventDefault();
    clear(err);
    try {
      const t = await api.call("POST", "/v1/auth/login", { email: email.value, password: pw.value, ...(org.value ? { tenant: org.value } : {}) });
      api.setTokens(t);
      pw.value = "";
      boot();
    } catch (x) {
      if (x.code === "choose_account") orgField.classList.remove("hide");
      err.append(note(x.message, "err"));
    }
  }) }, field("Email", email), field("Password", pw), orgField, err, btn);
  authShell("Sign in", message ? note(message, "ok") : null, form,
    h("p", { class: "hint" }, "Forgotten your password? Ask your administrator or Jas\u00a0iVision support for a new link."));
}

function setPasswordPage() {
  const token = new URLSearchParams(location.search).get("token") || "";
  history.replaceState(null, "", "/set-password");                  // the link works once, but there is no reason to leave it in the address bar
  const a = h("input", { type: "password", autocomplete: "new-password", required: true, minlength: 10 });
  const b = h("input", { type: "password", autocomplete: "new-password", required: true });
  const err = h("div");
  const btn = h("button", { class: "btn primary", type: "submit" }, "Save password");
  const form = h("form", { onsubmit: busy(btn, async (e) => {
    e.preventDefault();
    clear(err);
    if (a.value !== b.value) return err.append(note("The two passwords are not the same.", "err"));
    try {
      await api.call("POST", "/v1/auth/set-password", { token, password: a.value });
      history.replaceState(null, "", "/");
      signIn("Your password is saved. Sign in with it now.");
    } catch (x) { err.append(note(x.message, "err")); }
  }) }, field("New password", a, "At least 10 characters. A few words together is easy to remember and hard to guess."), field("Type it again", b), err, btn);
  authShell(token ? "Choose your password" : "This link is not complete", token ? form : note("Open the full link you were sent, or ask for a new one.", "warn"));
}

// ------------------------------------------------------------------------------------------------ shell
const NAV = [["#/", "Overview"], ["#/cameras", "Cameras"], ["#/recorders", "Recorders"], ["#/footfall", "People count"], ["#/plates", "Number plates"], ["#/account", "Account"]];

function shell() {
  const main = h("main", { class: "main", id: "main" });
  mount(app, h("div", { class: "shell" },
    h("aside", { class: "side" }, h("div", { class: "brand" }, h("i", {}, icon(LOGO)), BRAND.name), h("div", { class: "org" }, state.me.tenant.name),
      h("nav", { class: "nav", "aria-label": "Main" }, NAV.map(([href, label]) => h("a", { href }, label)))), main));
  return main;
}

function markNav() {
  const cur = location.hash || "#/";
  const base = "#/" + (cur.split("/")[1] || "");
  document.querySelectorAll(".nav a").forEach((a) => (a.getAttribute("href") === base ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
}

const ROUTES = [
  [/^#\/?$/, overview], [/^#\/cameras$/, cameras], [/^#\/cameras\/([\w-]+)$/, cameraPage], [/^#\/recorders$/, recorders],
  [/^#\/recorders\/new$/, newRecorder], [/^#\/recorders\/([\w-]+)$/, recorderPage], [/^#\/footfall$/, footfall], [/^#\/plates$/, plates], [/^#\/account$/, account],
];

async function render() {
  const main = document.getElementById("main");
  if (!main) return;
  const mine = ++state.view;                                      // a page that finishes loading after the user moved on must not draw itself
  markNav();
  const hash = location.hash || "#/";
  for (const [re, fn] of ROUTES) {
    const m = hash.match(re);
    if (!m) continue;
    mount(main, h("p", { class: "muted" }, "Loading…"));
    try {
      const out = await fn(...m.slice(1), () => mine === state.view);
      if (mine === state.view && out) mount(main, out);
    } catch (e) {
      if (mine === state.view) mount(main, note(errText(e), "err"), h("button", { class: "btn", onclick: render }, "Try again"));
    }
    return;
  }
  location.hash = "#/";
}

async function boot() {
  if (!api.signedIn()) return signIn();
  try { state.me = await api.get("/v1/me"); }
  catch (e) { if (e.status === 401) return signIn(); return mount(app, h("div", { class: "auth" }, h("div", { class: "box" }, note(errText(e), "err"), h("button", { class: "btn", onclick: boot }, "Try again")))); }
  shell();
  render();
}
addEventListener("hashchange", render);

// ------------------------------------------------------------------------------------------------ overview
const top = (title, ...actions) => h("div", { class: "top" }, h("h1", {}, title), h("div", { class: "actions" }, actions));
const stat = (value, label) => h("div", { class: "stat" }, h("b", {}, value), h("span", {}, label));

async function overview() {
  const [sum, cams, recs] = await Promise.all([api.get("/v1/footfall/summary" + qs({ from: istDay(), to: istDay(1), tz: "Asia/Kolkata" })), api.get("/v1/cameras"), api.get("/v1/recorders")]);
  const live = cams.filter((c) => c.enabled && c.stream_state === "streaming").length;
  const out = [top("Overview", isAdmin() && !recs.length ? h("a", { class: "btn primary", href: "#/recorders/new" }, "Connect a recorder") : null)];
  if (!recs.length) out.push(h("div", { class: "card" }, h("h2", {}, "Let's connect your first recorder"),
    h("p", { class: "muted" }, "Connecting takes about ten minutes. You type a few details into your recorder, and we do the rest. Nobody needs to visit your site."),
    isAdmin() ? h("a", { class: "btn primary", href: "#/recorders/new" }, "Connect a recorder") : h("p", { class: "muted" }, "Ask your administrator to connect a recorder.")));
  out.push(h("div", { class: "grid" }, stat(fmtNum(sum.people_in), "People in today"), stat(fmtNum(sum.people_out), "People out today"), stat(fmtNum(sum.net), "Inside now (net)"),
    stat(`${live} of ${cams.filter((c) => c.enabled).length}`, "Cameras live"), stat(`${recs.filter((r) => r.status === "online").length} of ${recs.length}`, "Recorders online")));
  if (sum.peak_hour) out.push(note(`Busiest hour today so far: ${fmtHour(sum.peak_hour.bucket)} with ${fmtNum(sum.peak_hour.people_in)} people in.`));
  if (cams.length) out.push(h("div", { class: "card" }, h("h2", {}, "Cameras"), camTable(cams)));
  return out;
}

const fmtHour = (iso) => new Date(iso).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "numeric", hour12: true });

function camTable(cams) {
  return h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Camera", "Counts", "Picture", "Status", "Last activity"].map((x) => h("th", {}, x)))),
    h("tbody", {}, cams.map((c) => {
      const [t, k] = c.enabled ? STATE[c.stream_state] || STATE.unknown : ["Paused", ""];
      return h("tr", {}, h("td", {}, h("a", { href: "#/cameras/" + c.id }, c.name)), h("td", {}, [c.use_footfall ? chip("People", "acc") : null, " ", c.use_anpr ? chip("Plates", "acc") : null]),
        h("td", {}, c.stream === "main" ? "Full quality" : "Light"), h("td", {}, chip(t, k)), h("td", { class: "muted" }, ago(c.last_frame_at)));
    }))));
}

// ------------------------------------------------------------------------------------------------ recorders
async function recorders() {
  const recs = await api.get("/v1/recorders");
  return [top("Recorders", isAdmin() ? h("a", { class: "btn primary", href: "#/recorders/new" }, "Connect a recorder") : null),
    recs.length ? h("div", { class: "card" }, h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Recorder", "Brand", "Status", "Channels", "Last seen"].map((x) => h("th", {}, x)))),
      h("tbody", {}, recs.map((r) => { const [t, k] = LINK[r.status] || LINK.never; return h("tr", {}, h("td", {}, h("a", { href: "#/recorders/" + r.id, class: "mono" }, r.device_id)), h("td", {}, cap(r.brand)), h("td", {}, chip(t, k)), h("td", { class: "num" }, r.channel_count), h("td", { class: "muted" }, ago(r.last_heartbeat_at))); })))))
      : h("div", { class: "empty" }, "No recorders yet.")];
}
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

function newRecorder() {
  if (!isAdmin()) return note("Only an administrator can connect a recorder.", "warn");
  const box = h("div");
  const step1 = () => mount(box, top("Connect a recorder"), h("div", { class: "card" }, h("h2", {}, "Which brand is your recorder?"),
    h("div", { class: "row" }, h("button", { class: "btn primary", onclick: (e) => create(e.currentTarget, "dahua") }, "Dahua"),
      h("button", { class: "btn", disabled: true, title: "Coming soon" }, "Hikvision ", chip("Coming soon"))),
    h("p", { class: "hint" }, "Your recorder connects out to us over the internet. You do not need a static IP, a VPN or any port forwarding.")));
  const create = busy_((btn, brand) => api.post("/v1/recorders", { brand }).then(step2, (e) => toast(errText(e), true)));
  step1();
  return box;

  function step2(c) {
    let stop = false;
    const checks = h("div", { class: "checks" });
    const goNext = h("div");
    const steps = [["reached", "Our server heard from your recorder"], ["key_ok", "The password and ID matched"], ["channels_found", "We found your cameras"], ["preview_ok", "We received a picture"]];
    const draw = (s) => {
      mount(checks, steps.map(([k, label]) => h("div", { class: "check" }, h("span", { class: "dot" + (s.checks[k] ? " on" : "") }, s.checks[k] ? "✓" : ""), label)));
      if (s.checks.channels_found) mount(goNext, h("a", { class: "btn primary", href: "#/recorders/" + c.recorder_id }, "Choose your cameras"));
    };
    const poll = async () => {
      while (!stop && document.body.contains(box)) {
        try { draw(await api.get(`/v1/recorders/${c.recorder_id}/status`)); } catch { /* keep trying */ }
        await new Promise((r) => setTimeout(r, 3000));
      }
    };
    mount(box, top("Connect your Dahua recorder"),
      h("div", { class: "card" }, h("h2", {}, "1. Details to type into the recorder"),
        h("dl", { class: "cred" }, copyRow("Server address", c.server), copyRow("Port", String(c.port)), copyRow("Sub-Device ID", c.device_id), copyRow("Viewer user", c.viewer_user || "giview"), copyRow("Viewer password", c.key)),
        note("Save the password now. For your security it is shown only once. If you lose it, you can make a new one.", "warn")),
      h("div", { class: "card" }, h("h2", {}, "2. On the recorder (menu or web page)"),
        h("ol", { class: "steps" },
          h("li", {}, "Open ", h("b", {}, "Network → Register"), " (called Auto Register on some models)."),
          h("li", {}, "Switch it ", h("b", {}, "on"), ". Type the server address and port above. Set ", h("b", {}, "Sub-Device ID"), " to the ID above, exactly. Save."),
          h("li", {}, "Open ", h("b", {}, "Account"), " (users). Add a new user with the viewer user name and password above. Give it ", h("b", {}, "live view only"), "."),
          h("li", {}, "Do not use your administrator account or password here. This separate user can only look.")),
        h("p", { class: "muted small" }, "This page updates by itself when the recorder connects, usually within a minute.")),
      h("div", { class: "card" }, h("h2", {}, "3. Connection check"), checks, h("div", { class: "after" }, goNext)));
    poll();
    draw({ checks: {} });
    window.addEventListener("hashchange", () => { stop = true; }, { once: true });
  }
}
function busy_(fn) { return (btn, ...a) => { if (btn.disabled) return; btn.disabled = true; return fn(btn, ...a).finally(() => { btn.disabled = false; }); }; }

async function recorderPage(id) {
  const [r, chans, cams] = await Promise.all([api.get("/v1/recorders/" + id), api.get(`/v1/recorders/${id}/channels`), api.get("/v1/cameras")]);
  const [t, k] = LINK[r.status] || LINK.never;
  const feats = state.me.tenant.features || {};
  const setup = (c) => {
    const name = h("input", { type: "text", value: c.name || "Camera " + c.channel_no, maxlength: 120 });
    const foot = h("input", { type: "checkbox", checked: true, id: "uf" }), anpr = h("input", { type: "checkbox", id: "ua", disabled: !feats.anpr });
    const err = h("div");
    modal("Set up camera " + c.channel_no, h("div", {}, field("Name", name, "For example Main gate or Shop entrance."),
      h("div", { class: "chk" }, foot, h("label", { for: "uf" }, "Count people going in and out")),
      h("div", { class: "chk" }, anpr, h("label", { for: "ua" }, "Read number plates", feats.anpr ? "" : " (not in your plan)")), err),
      { actions: [{ label: "Cancel", run: (x) => x() }, { label: "Add camera", kind: "primary", run: async (close) => {
        clear(err);
        try {
          const cam = await api.post("/v1/cameras", { recorder_id: id, channel_no: c.channel_no, name: name.value, use_footfall: foot.checked, use_anpr: anpr.checked });
          close();
          toast("Camera added. Now draw the counting line.");
          location.hash = "#/cameras/" + cam.id;
        } catch (x) { err.append(note(errText(x), "err")); }
      } }] });
  };
  const rotate = () => modal("Make a new password?", h("p", {}, "This replaces the viewer password. You must type the new one into the recorder, and the recorder stays disconnected until you do."),
    { actions: [{ label: "Cancel", run: (x) => x() }, { label: "Make new password", kind: "danger", run: async (close) => {
      try { const c = await api.post(`/v1/recorders/${id}/rotate-key`); close(); modal("New viewer password", h("div", {}, h("dl", { class: "cred" }, copyRow("Viewer user", c.viewer_user || "giview"), copyRow("Viewer password", c.key)), note("Shown only once. Type it into the recorder's Account page for this user.", "warn")), { actions: [{ label: "Done", kind: "primary", run: (x) => x() }] }); }
      catch (x) { toast(errText(x), true); }
    } }] });
  return [top(r.device_id, h("span", {}, chip(t, k)), isAdmin() ? h("button", { class: "btn sm", onclick: rotate }, "Make a new password") : null),
    h("div", { class: "card" }, h("h2", {}, "Channels"), chans.length ? h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Channel", "Name", "", ""].map((x) => h("th", {}, x)))),
      h("tbody", {}, chans.map((c) => { const cam = cams.find((x) => x.recorder_id === id && x.channel_no === c.channel_no);
        return h("tr", {}, h("td", { class: "num" }, c.channel_no), h("td", {}, c.name || "—"), h("td", {}, cam ? chip("Camera set up", "ok") : null),
          h("td", {}, cam ? h("a", { class: "btn sm", href: "#/cameras/" + cam.id }, "Open") : isAdmin() && c.active ? h("button", { class: "btn sm primary", onclick: () => setup(c) }, "Set up camera") : null)); }))))
      : h("div", { class: "empty" }, "No channels yet. They appear when the recorder connects."))];
}

// ------------------------------------------------------------------------------------------------ cameras
async function cameras() {
  const cams = await api.get("/v1/cameras");
  return [top("Cameras", isAdmin() ? h("a", { class: "btn", href: "#/recorders" }, "Add from a recorder") : null),
    cams.length ? h("div", { class: "card" }, camTable(cams)) : h("div", { class: "empty" }, "No cameras yet. Connect a recorder, then choose its cameras.")];
}

async function cameraPage(id, alive) {
  const [cams, zones] = await Promise.all([api.get("/v1/cameras"), api.get(`/v1/cameras/${id}/zones`)]);
  const cam = cams.find((c) => c.id === id);
  if (!cam) return note("Camera not found.", "err");
  const ed = { line: null, flip: 1, poly: [], mode: null };
  const zl = zones.find((z) => z.kind === "count_line"), zp = zones.find((z) => z.kind === "anpr_zone");
  if (zl) { ed.line = zl.points; ed.flip = zl.in_direction && dot(zl.in_direction, normal(zl.points)) < 0 ? -1 : 1; }
  if (zp) ed.poly = zp.points;
  const img = h("img", { alt: "Camera view", class: "hide", draggable: "false" });
  const ph = h("div", { class: "ph" }, "Getting a picture from the camera…");
  const svg = h("svg:svg", { viewBox: "0 0 1 1", preserveAspectRatio: "none" });
  const stage = h("div", { class: "stage" }, ph, img, svg);
  const help = h("p", { class: "hint" });
  let url = null;
  const redraw = () => {
    clear(svg);
    if (ed.line) {
      ed.line.forEach((p) => svg.append(cross(p)));
      if (ed.line.length === 2) {
        const [a, b] = ed.line, n = normal(ed.line).map((v) => v * ed.flip * 0.12), m = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
        svg.append(h("svg:line", { class: "zline", x1: a[0], y1: a[1], x2: b[0], y2: b[1] }), h("svg:path", { class: "zarrow", d: `M${m[0]} ${m[1]} L${m[0] + n[0]} ${m[1] + n[1]}` }),
          h("svg:circle", { cx: m[0] + n[0], cy: m[1] + n[1], r: 0.012, fill: "#fff" }));
      }
    }
    if (ed.poly.length) { svg.append(h("svg:polygon", { class: "zpoly", points: ed.poly.map((p) => p.join(",")).join(" ") }), ...ed.poly.map(cross)); }
    help.textContent = ed.mode === "line" ? (ed.line ? "Click the second end of the line." : "Click the first end of the line.") : ed.mode === "poly" ? "Click each corner of the area where plates appear, then press Finish shape." : "";
  };
  const cross = (p) => h("svg:path", { class: "zline", d: `M${p[0] - 0.012} ${p[1]} H${p[0] + 0.012} M${p[0]} ${p[1] - 0.02} V${p[1] + 0.02}` });
  svg.addEventListener("click", (e) => {
    if (!ed.mode) return;
    const r = svg.getBoundingClientRect(), p = [round((e.clientX - r.left) / r.width), round((e.clientY - r.top) / r.height)];
    if (ed.mode === "line") { if (!ed.line || ed.line.length === 2) ed.line = [p]; else { ed.line.push(p); ed.mode = null; } }
    else ed.poly.push(p);
    redraw();
  });
  const load = async () => {
    mount(ph, "Getting a picture from the camera…"); ph.classList.remove("hide"); img.classList.add("hide");
    try {
      const blob = await api.get(`/v1/cameras/${id}/snapshot`, { blob: true });
      if (!alive()) return;
      if (url) URL.revokeObjectURL(url);
      img.src = url = URL.createObjectURL(blob);
      img.classList.remove("hide"); ph.classList.add("hide");
    } catch (e) { mount(ph, errText(e)); }
  };
  const tools = h("div", { class: "row" },
    cam.use_footfall ? h("button", { class: "btn", onclick: () => { ed.mode = "line"; ed.line = null; redraw(); } }, "Draw counting line") : null,
    cam.use_footfall ? h("button", { class: "btn", onclick: () => { ed.flip *= -1; redraw(); } }, "Swap in / out") : null,
    cam.use_anpr ? h("button", { class: "btn", onclick: () => { ed.mode = "poly"; ed.poly = []; redraw(); } }, "Draw plate area") : null,
    cam.use_anpr ? h("button", { class: "btn", onclick: () => { ed.mode = null; redraw(); } }, "Finish shape") : null,
    h("button", { class: "btn", onclick: load }, "New picture"));
  const save = h("button", { class: "btn primary" }, "Save");
  save.onclick = busy(save, async () => {
    const zs = [];
    if (cam.use_footfall && ed.line?.length === 2) { const n = normal(ed.line).map((v) => round(v * ed.flip)); zs.push({ kind: "count_line", points: ed.line, in_direction: n }); }
    if (cam.use_anpr && ed.poly.length >= 3) zs.push({ kind: "anpr_zone", points: ed.poly });
    if (!zs.length) return toast("Draw the counting line or plate area first.", true);
    try { await api.put(`/v1/cameras/${id}/zones`, { zones: zs }); toast("Saved. The camera uses it within a minute."); } catch (e) { toast(errText(e), true); }
  });
  const streamSel = h("select", {}, h("option", { value: "sub", selected: cam.stream === "sub" }, "Light (counting people)"), h("option", { value: "main", selected: cam.stream === "main" }, "Full quality (number plates)"));
  streamSel.onchange = async () => { try { await api.patch("/v1/cameras/" + id, { stream: streamSel.value }); toast("Picture quality changed. It applies at the next reconnect."); } catch (e) { toast(errText(e), true); } };
  const pause = h("button", { class: "btn" }, cam.enabled ? "Pause this camera" : "Resume this camera");
  pause.onclick = busy(pause, async () => { try { await api.patch("/v1/cameras/" + id, { enabled: !cam.enabled }); toast(cam.enabled ? "Paused." : "Resumed."); render(); } catch (e) { toast(errText(e), true); } });
  const remove = h("button", { class: "btn danger" }, "Remove camera");
  remove.onclick = () => modal("Remove this camera?", h("p", {}, "Its history stays in your reports. The channel goes back to your recorder's list."), { actions: [{ label: "Cancel", run: (x) => x() },
    { label: "Remove", kind: "danger", run: async (close) => { try { await api.del("/v1/cameras/" + id); close(); location.hash = "#/cameras"; } catch (e) { toast(errText(e), true); } } }] });
  const [t, k] = cam.enabled ? STATE[cam.stream_state] || STATE.unknown : ["Paused", ""];
  redraw();
  load();
  return [top(cam.name, chip(t, k)), h("div", { class: "card" }, h("h2", {}, cam.use_footfall && cam.use_anpr ? "Counting line and plate area" : cam.use_footfall ? "Counting line" : "Plate area"),
    h("p", { class: "muted" }, cam.use_footfall ? "Press Draw counting line, then click both ends of a line across the doorway or path. A person crossing it in the direction of the arrow counts as IN, and the other way as OUT. If the arrow points the wrong way, press Swap in / out." : "Draw the area where number plates appear."),
    stage, help, tools, h("div", { class: "after" }, save)),
    isAdmin() ? h("div", { class: "card" }, h("h2", {}, "Settings"), field("Picture quality", streamSel, "Full quality needs more of our server, and is needed to read number plates."), h("div", { class: "row" }, pause, remove)) : null];
}
const round = (v) => Math.round(Math.min(1, Math.max(0, v)) * 1000) / 1000;
const normal = (l) => { const dx = l[1][0] - l[0][0], dy = l[1][1] - l[0][1], n = Math.hypot(dx, dy) || 1; return [-dy / n, dx / n]; };
const dot = (a, b) => a[0] * b[0] + a[1] * b[1];

// ------------------------------------------------------------------------------------------------ people count
async function footfall() {
  const cams = await api.get("/v1/cameras");
  const range = h("select", {}, [["today", "Today"], ["7", "Last 7 days"], ["30", "Last 30 days"]].map(([v, l]) => h("option", { value: v }, l)));
  const camSel = h("select", {}, h("option", { value: "" }, "All cameras"), cams.filter((c) => c.use_footfall).map((c) => h("option", { value: c.id }, c.name)));
  const out = h("div", {});
  const load = async () => {
    const today = range.value === "today", days = today ? 1 : Number(range.value);
    const from = istDay(1 - days), to = istDay(1), interval = today ? "hour" : "day";
    mount(out, h("p", { class: "muted" }, "Loading…"));
    try {
      const [f, s] = await Promise.all([api.get("/v1/footfall" + qs({ from, to, interval, camera_id: camSel.value, tz: "Asia/Kolkata" })), api.get("/v1/footfall/summary" + qs({ from, to, camera_id: camSel.value, tz: "Asia/Kolkata" }))]);
      const labels = f.items.map((p) => (interval === "hour" ? fmtHourShort(p.bucket) : new Date(p.bucket).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short" })));
      mount(out, h("div", { class: "grid" }, stat(fmtNum(s.people_in), "People in"), stat(fmtNum(s.people_out), "People out"), stat(fmtNum(s.net), "Net"), stat(s.peak_hour ? fmtHour(s.peak_hour.bucket) : "—", "Busiest hour")),
        h("div", { class: "card" }, h("div", { class: "legend" }, h("span", {}, h("i", { class: "k1" }), "In"), h("span", {}, h("i", { class: "k2" }), "Out")),
          barChart(labels, [{ name: "In", color: "var(--in)", values: f.items.map((p) => p.people_in) }, { name: "Out", color: "var(--out)", values: f.items.map((p) => p.people_out) }])));
      out.querySelectorAll(".legend i").forEach((i, n) => { i.style.background = n ? "var(--out)" : "var(--in)"; });
    } catch (e) { mount(out, note(errText(e), "err")); }
  };
  range.onchange = camSel.onchange = load;
  load();
  return [top("People count"), h("div", { class: "row" }, h("div", {}, range), h("div", {}, camSel)), h("p"), out];
}
const fmtHourShort = (iso) => new Date(iso).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "numeric", hour12: true }).replace(" ", "");

// ------------------------------------------------------------------------------------------------ number plates
async function plates() {
  const cams = await api.get("/v1/cameras");
  if (!cams.some((c) => c.use_anpr)) return [top("Number plates"), h("div", { class: "empty" }, "Number plate reading is not turned on for any camera. Set it up when you add a camera.")];
  const q = h("input", { type: "search", placeholder: "Search a plate, for example GJ14", maxlength: 20 });
  const body = h("tbody", {}), more = h("div", { class: "row" });
  let cursor = null, last = "";
  const load = async (reset) => {
    if (reset) { cursor = null; clear(body); }
    try {
      const r = await api.get("/v1/plates" + qs({ q: q.value.trim(), cursor, limit: 50, from: istDay(-6), to: istDay(1) }));
      cursor = r.next_cursor;
      r.items.forEach((p) => body.append(h("tr", {}, h("td", { class: "muted" }, fmtDateTime(p.seen_at)), h("td", { class: "mono" }, p.plate_text || "unreadable"), h("td", {}, cams.find((c) => c.id === p.camera_id)?.name || ""),
        h("td", {}, p.direction ? chip(p.direction === "in" ? "In" : "Out", "acc") : ""), h("td", {}, [p.review_status === "needs_review" ? chip("Needs a check", "warn") : null, p.blocked ? chip("Blocked list", "crit") : null]))));
      if (reset && !r.items.length) body.append(h("tr", {}, h("td", { colspan: 5, class: "empty" }, "No plates in the last 7 days.")));
      mount(more, cursor ? h("button", { class: "btn", onclick: () => load(false) }, "Show more") : null);
    } catch (e) { toast(errText(e), true); }
  };
  q.oninput = () => { if (q.value !== last) { last = q.value; clearTimeout(q._t); q._t = setTimeout(() => load(true), 350); } };
  load(true);
  return [top("Number plates"), h("div", { class: "field" }, q), h("div", { class: "card" }, h("div", { class: "tablewrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["Time", "Plate", "Camera", "Direction", ""].map((x) => h("th", {}, x)))), body)), more)];
}

// ------------------------------------------------------------------------------------------------ account
function account() {
  const cur = h("input", { type: "password", autocomplete: "current-password", required: true });
  const a = h("input", { type: "password", autocomplete: "new-password", required: true, minlength: 10 });
  const err = h("div");
  const btn = h("button", { class: "btn primary", type: "submit" }, "Change password");
  const out = h("button", { class: "btn" }, "Sign out");
  out.onclick = async () => { try { if (api.refreshToken) await api.call("POST", "/v1/auth/logout", { refresh_token: api.refreshToken }); } catch { /* signing out anyway */ } api.clear(); state.me = null; location.hash = ""; signIn(); };
  return [top("Account"), h("div", { class: "card" }, h("h2", {}, state.me.user.name), h("p", { class: "muted" }, `${state.me.tenant.name} · ${state.me.user.role === "admin" ? "Administrator" : "Viewer"} · plan ${state.me.tenant.plan_name}`),
    h("p", { class: "muted" }, `Cameras in use: ${state.me.tenant.cameras_in_use} of ${state.me.tenant.camera_limit}`), out),
  h("form", { class: "card", onsubmit: busy(btn, async (e) => {
    e.preventDefault(); clear(err);
    try { await api.post("/v1/auth/change-password", { current_password: cur.value, new_password: a.value }); cur.value = a.value = ""; err.append(note("Password changed. Other devices will need to sign in again.", "ok")); }
    catch (x) { err.append(note(errText(x), "err")); }
  }) }, h("h2", {}, "Change password"), field("Current password", cur), field("New password", a, "At least 10 characters."), err, btn)];
}

// ------------------------------------------------------------------------------------------------ start
if (location.pathname === "/set-password") setPasswordPage(); else boot();
