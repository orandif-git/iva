// Small pieces shared by the staff screens.
import { ago, fmtDateTime, h } from "../lib.js";
import { ic, pill } from "../app/ui.js";

export const money = (paise) => "₹" + Math.round((paise || 0) / 100).toLocaleString("en-IN");
export const initials = (n) => String(n || "?").split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
export const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");
export { ago, fmtDateTime };

export const CUS_STATUS = { active: ["Active", "ok"], trial: ["Trial", "acc"], past_due: ["Past due", "warn"], suspended: ["Suspended", "crit"], cancelled: ["Cancelled", ""] };
export const REC_STATUS = { online: ["Online", "ok"], offline: ["Offline", "crit"], never: ["Never connected", "warn"] };
export const INV_STATUS = { paid: ["Paid", "ok"], issued: ["Issued", "acc"], overdue: ["Overdue", "crit"], draft: ["Draft", ""], void: ["Void", ""] };
export const statusPill = (map, key) => { const m = map[key] || [cap(key), ""]; return pill(m[0], m[1]); };

export const title = (t, sub, right) => h("div", { class: "pagehead" }, h("div", {}, h("h1", { class: "lt" }, t), sub ? h("p", { class: "sub" }, sub) : null), right || null);
export const back = (label, href) => h("a", { class: "backlink" + (href === "#/more" ? " only-phone" : ""), href }, ic("chev"), label);
export const hero = (icon, name, sub, right, initialsText) => h("div", { class: "hero" }, h("span", { class: "ico" + (icon && icon.startsWith("!") ? " crit" : "") }, initialsText ? h("span", { class: "cust-initials" }, initialsText) : ic(icon)), h("div", { class: "grow" }, h("div", { class: "t" }, name), h("div", { class: "s" }, sub)), right || null);

/** A tappable row in a grouped list. */
export function row({ icon, title: t, sub, right, onclick, danger = false, initials: ini, href }) {
  const tag = href ? "a" : "button";
  const el = h(tag, { class: "gi" + (danger ? " dng" : "") + (onclick || href ? "" : " static"), type: href ? null : "button", href, onclick },
    ini ? h("span", { class: "ico" }, h("span", { class: "cust-initials" }, ini)) : icon ? h("span", { class: "ico" + (danger ? " crit" : "") }, ic(icon)) : null,
    h("div", {}, h("b", {}, t), sub ? h("small", {}, sub) : null),
    right === undefined ? (onclick || href ? ic("chev", "chev") : h("span")) : right);
  return el;
}
export const group = (...rows) => h("div", { class: "group" }, rows);
export const gh = (t) => h("div", { class: "gh" }, t);
/** A line of label and value, not tappable. */
export const kv = (label, value, sub) => h("div", { class: "gi static" }, h("div", {}, h("b", {}, label), sub ? h("small", {}, sub) : null), value instanceof Node ? value : h("span", { class: "muted" }, value ?? "—"));
