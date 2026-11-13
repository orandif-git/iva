/* Sign in to the Control Center, and set a password from an invitation or reset link. Staff only: there is no other kind of sign-in. */
const $ = (s) => document.querySelector(s);
const KEY = "gi_staff_rt";                                   // the console reads its sign-in from here
const setting = location.pathname.startsWith("/set-password");
const store = { get: () => { try { return sessionStorage.getItem(KEY); } catch { return null; } }, set: (v) => { try { v ? sessionStorage.setItem(KEY, v) : sessionStorage.removeItem(KEY); } catch { /* private window */ } } };

async function post(url, body) {
  let r;
  try { r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), cache: "no-store" }); }
  catch { throw Object.assign(new Error("Cannot reach the server. Check your internet and try again."), { status: 0 }); }
  let j = {}; try { j = r.status === 204 ? {} : await r.json(); } catch { /* no body */ }
  if (!r.ok) throw Object.assign(new Error(j?.error?.message || (r.status === 429 ? "Too many attempts. Wait a few minutes and try again." : "Something went wrong. Try again.")), { status: r.status });
  return j;
}
const fail = (id, msg) => { $("#err").textContent = msg; const e = id && $("#" + id); if (e) { e.classList.add("bad"); e.focus(); } };
const clear = () => { $("#err").textContent = ""; document.querySelectorAll(".in.bad").forEach((e) => e.classList.remove("bad")); };
document.addEventListener("input", (e) => { if (e.target.classList?.contains("in")) { e.target.classList.remove("bad"); $("#err").textContent = ""; } });

/* ---------------------------------------------------------------------------- the same plain password rules as the server */
function strength(p) {
  if (!p) return [0, "At least 10 characters. A long phrase is best."];
  if (p.length < 10) return [1, `Use at least 10 characters (${p.length} so far).`];
  if (["password123", "1234567890", "qwertyuiop"].includes(p.toLowerCase()) || new Set(p.toLowerCase()).size < 4) return [1, "Too easy to guess. Choose a longer one that is not a common word."];
  const s = 2 + (p.length >= 14 ? 1 : 0) + (/\d/.test(p) && /[a-z]/i.test(p) ? 1 : 0);
  return [Math.min(4, s), s >= 4 ? "Strong." : s === 3 ? "Good." : "Okay. A longer one is stronger."];
}

/* ---------------------------------------------------------------------------- set a password (invitation or reset link) */
function startSetPassword() {
  document.title = "Set your password · Jas iVision Control Center";
  $("#ttl").textContent = "Choose your password"; $("#sub").textContent = "This link works once.";
  $("#in-fields").hidden = true; $("#set-fields").hidden = false; $("#go").textContent = "Save password"; $("#foot").textContent = "";
  const token = new URLSearchParams(location.search).get("token") || "";
  if (!token) { $("#go").disabled = true; fail(null, "This link is not complete. Ask an Owner or Admin for a new one."); return; }
  $("#p1").focus();
  $("#p1").addEventListener("input", () => { const [s, t] = strength($("#p1").value); $("#meter").dataset.s = s; $("#mtext").textContent = t; });
  $("#form").addEventListener("submit", async (ev) => {
    ev.preventDefault(); clear();
    const a = $("#p1").value, b = $("#p2").value, [s, t] = strength(a);
    if (s <= 1) return fail("p1", t);
    if (a !== b) return fail("p2", "The two passwords are not the same.");
    $("#go").disabled = true;
    try { await post("/admin/v1/auth/set-password", { token, password: a }); }
    catch (e) { $("#go").disabled = false; return fail(e.status === 422 ? "p1" : null, e.message); }
    store.set(null);
    $("#set-fields").hidden = true; $("#ttl").textContent = "Password saved"; $("#sub").textContent = "You can sign in now.";
    $("#go").disabled = false; $("#go").textContent = "Go to sign in"; $("#go").type = "button"; $("#go").addEventListener("click", () => location.replace("/"));
  });
}

/* ---------------------------------------------------------------------------- sign in */
async function startSignIn() {
  const rt = store.get();
  if (rt) {                                                    // already signed in on this browser: go straight on
    try { const j = await post("/admin/v1/auth/refresh", { refresh_token: rt }); store.set(j.refresh_token); return location.replace("/console/"); } catch { store.set(null); }
  }
  $("#form").addEventListener("submit", async (ev) => {
    ev.preventDefault(); clear();
    const email = $("#email").value.trim(), pw = $("#pw").value;
    if (!email) return fail("email", "Enter your email.");
    if (!pw) return fail("pw", "Enter your password.");
    $("#go").disabled = true; $("#go").textContent = "Signing in…";
    try {
      const j = await post("/admin/v1/auth/login", { email, password: pw });
      store.set(j.refresh_token); location.replace("/console/");
    } catch (e) { $("#go").disabled = false; $("#go").textContent = "Sign in"; fail(null, e.message); $("#pw").value = ""; $("#pw").focus(); }
  });
}
setting ? startSetPassword() : startSignIn();
