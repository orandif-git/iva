// The frame both apps share: grouped sidebar that folds into a slim rail, bottom tabs, and one search box (Ctrl or Cmd + K).
// Each app passes in its own menu; everything else is the same code. Built with h() and text nodes only.
import { h, mount } from "../lib.js";
import { closeSheet, ic, openSheet } from "./ui.js";

const get = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
const put = (k, v) => { try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* private mode */ } };

/** A menu button, used in the sidebar and in the bottom tabs. */
export const navButton = (go, { key, label, icon, href }, withTip = false) =>
  h("button", { type: "button", "data-tab": key, "aria-label": label, title: withTip ? label : null, onclick: () => go(href || "#/" + key) }, ic(icon), h("span", { class: "l" }, label));

/**
 * groups: [{ label?, items: [{key, label, icon, href?}] }]. A group without a label is simply a gap-free block at the top.
 * Returns { el, mark(key), dot(key, n) }. The fold button flips the "rail" class on the app root and remembers the choice.
 */
export function sidebar({ go, groups, head, foot, storeKey }) {
  const nav = h("nav", { "aria-label": "Sidebar" }, groups.map((g, i) => h("div", { class: "ng", role: "group", "aria-label": g.label || "Main" },
    g.label ? h("div", { class: "ngl", "aria-hidden": "true" }, g.label) : null, g.items.map((it) => navButton(go, it, true)))));
  const fold = h("button", { class: "fold", type: "button", "aria-pressed": "false", "aria-label": "Fold the menu", onclick: () => set(!rail()) }, ic("panel"), h("span", { class: "l" }, "Fold menu"));
  const el = h("aside", { class: "sb" }, head, nav, h("div", { class: "foot" }, foot, fold));
  const root = () => el.closest(".ax");
  const rail = () => !!root()?.classList.contains("rail");
  const set = (on) => {
    root()?.classList.toggle("rail", on);
    fold.setAttribute("aria-pressed", String(on)); fold.setAttribute("aria-label", on ? "Open the menu" : "Fold the menu");
    put(storeKey, on ? "1" : null);
  };
  // apply the remembered choice as soon as the sidebar is in the page
  queueMicrotask(() => { if (get(storeKey) === "1") set(true); });
  return {
    el,
    mark: (key) => el.querySelectorAll("[data-tab]").forEach((b) => (b.getAttribute("data-tab") === key ? b.setAttribute("aria-current", "page") : b.removeAttribute("aria-current"))),
  };
}

export const tabbar = (go, tabs, label = "Main") => h("nav", { class: "tabbar", "aria-label": label }, tabs.map((t) => navButton(go, t)));

// ---------------------------------------------------------------------------------------------------------- search
const RECENT_MAX = 5;
function highlight(text, q) {
  const i = q ? text.toLowerCase().indexOf(q) : -1;
  return i < 0 ? text : [text.slice(0, i), h("mark", {}, text.slice(i, i + q.length)), text.slice(i + q.length)];
}

/**
 * cfg.pages: [{label, icon, href}] always available.
 * cfg.load(): Promise<[{section, icon, items: [{label, sub?, href}]}]> things looked up from the server (already limited to what this person may see).
 * cfg.recentKey: where the last few picks are remembered on this device.
 */
export function openPalette(cfg) {
  const input = h("input", { type: "text", role: "combobox", autocomplete: "off", spellcheck: "false", placeholder: cfg.placeholder, "aria-label": "Search", "aria-expanded": "true", "aria-controls": "pal-list", "aria-autocomplete": "list" });
  const list = h("div", { id: "pal-list", role: "listbox", "aria-label": "Results", class: "pal2" });
  const status = h("div", { class: "sr", role: "status", "aria-live": "polite" });
  const found = [];                                        // sections loaded from the server
  let flat = [], sel = 0;
  const recents = () => { try { return JSON.parse(get(cfg.recentKey) || "[]"); } catch { return []; } };
  const remember = (it) => put(cfg.recentKey, JSON.stringify([{ label: it.label, sub: it.sub, icon: it.icon, href: it.href }, ...recents().filter((r) => r.href !== it.href)].slice(0, RECENT_MAX)));
  const pick = (it) => { remember(it); closeSheet(); cfg.go(it.href); };

  const sections = () => {
    const q = input.value.trim().toLowerCase();
    const has = (it) => (it.label + " " + (it.sub || "")).toLowerCase().includes(q);
    if (!q) {
      const r = recents();
      return [r.length ? { title: "Recent", items: r } : null, { title: "Pages", items: cfg.pages }].filter(Boolean);
    }
    return [{ title: "Pages", items: cfg.pages.filter(has) }, ...found.map((s) => ({ title: s.section, items: s.items.map((i) => ({ icon: s.icon, ...i })).filter(has).slice(0, 6) }))].filter((s) => s.items.length);
  };
  const draw = () => {
    const q = input.value.trim().toLowerCase(), secs = sections();
    flat = secs.flatMap((s) => s.items);
    sel = Math.min(sel, Math.max(0, flat.length - 1));
    let n = 0;
    mount(list, flat.length ? secs.map((s) => h("div", { role: "group", "aria-label": s.title }, h("div", { class: "palh" }, s.title),
      s.items.map((it) => { const idx = n++; return h("div", { class: "palrow", role: "option", id: "pal-" + idx, "aria-selected": String(idx === sel), onclick: () => pick(it), onmousemove: () => { if (sel !== idx) { sel = idx; mark(); } } },
        ic(it.icon || "search"), h("div", { class: "grow" }, h("b", {}, highlight(it.label, q)), it.sub ? h("small", {}, it.sub) : null)); }))) : h("p", { class: "sub palnone" }, "Nothing matches. Try part of a name."));
    input.setAttribute("aria-activedescendant", flat.length ? "pal-" + sel : "");
    status.textContent = q ? `${flat.length} ${flat.length === 1 ? "result" : "results"}` : "";
  };
  const mark = () => {
    list.querySelectorAll('[role="option"]').forEach((o, i) => o.setAttribute("aria-selected", String(i === sel)));
    input.setAttribute("aria-activedescendant", "pal-" + sel);
    list.querySelector("#pal-" + sel)?.scrollIntoView?.({ block: "nearest" });
  };
  input.oninput = () => { sel = 0; draw(); };
  input.onkeydown = (e) => {
    if (e.key === "ArrowDown") { sel = Math.min(sel + 1, flat.length - 1); mark(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { sel = Math.max(0, sel - 1); mark(); e.preventDefault(); }
    else if (e.key === "Enter" && flat[sel]) { pick(flat[sel]); e.preventDefault(); }
  };
  openSheet("Search", h("div", { class: "field" }, input), list, status);
  draw();
  input.focus();
  Promise.resolve(cfg.load()).then((r) => { found.push(...r); draw(); }).catch(() => { /* pages still work offline */ });
}

/** Ctrl or Cmd + K and "/" open the search from anywhere except a text box. */
export function searchShortcuts(isReady, open) {
  addEventListener("keydown", (e) => {
    if (!isReady()) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); open(); }
    else if (e.key === "/" && !e.ctrlKey && !e.metaKey && !/^(input|textarea|select)$/i.test(e.target?.tagName || "") && !e.target?.isContentEditable) { e.preventDefault(); open(); }
  });
}
