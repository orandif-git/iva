// Billing (invoices) and Plans. Owner only.
import { clear, errText, fmtNum, h, mount, qs } from "../lib.js";
import { chips, empty, field, formSheet, ic, kpi, note, openSheet, closeSheet, pill, reasonSheet, toast, toggle } from "../app/ui.js";
import { back, gh, group, INV_STATUS, kv, money, row, statusPill, title, fmtDateTime } from "./common.js";

const noaccess = () => empty("lock", "Not available for your role", "Ask an owner if you need this.");
const monthKey = (d) => d.toISOString().slice(0, 7);
const monthName = (m) => new Date(m + "-01T00:00:00Z").toLocaleDateString("en-IN", { month: "long", year: "numeric", timeZone: "UTC" });
function lastMonths(n) { const out = [], d = new Date(); d.setUTCDate(1); for (let i = 0; i < n; i++) { out.push(monthKey(d)); d.setUTCMonth(d.getUTCMonth() - 1); } return out; }

async function downloadPdf(X, inv) {
  try {
    const blob = await X.api.get(`/admin/v1/billing/invoices/${inv.id}/pdf`, { blob: true });
    const a = h("a", { href: URL.createObjectURL(blob), download: `${inv.number || "invoice"}.pdf` });
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  } catch (e) { toast(errText(e), true); }
}

export async function invoiceSheet(X, inv, after) {
  let full = inv;
  try { full = await X.api.get(`/admin/v1/billing/invoices/${inv.id}`); } catch { /* show what we have */ }
  const st = full.overdue ? "overdue" : full.status;
  const rows = [];
  if (full.status === "draft") rows.push(row({ icon: "check", title: "Issue and email", sub: "Gives it a number and sends it to the customer", onclick: async () => { try { await X.api.post(`/admin/v1/billing/invoices/${full.id}/issue`); closeSheet(); toast("Invoice issued"); after(); } catch (e) { toast(errText(e), true); } } }));
  if (full.status === "issued") rows.push(row({ icon: "card", title: "Record a payment", onclick: () => payment(X, full, after) }));
  rows.push(row({ icon: "doc", title: "Download PDF", onclick: () => downloadPdf(X, full) }));
  if (full.status !== "paid" && full.status !== "void") rows.push(row({ icon: "x", title: "Void invoice", sub: "Cancels it. The customer is no longer asked to pay.", danger: true, onclick: () => reasonSheet({ title: `Void ${full.number || "this draft"}?`, text: "The invoice is cancelled and cannot be brought back.", label: "Void invoice", danger: true, run: async (reason) => { await X.api.post(`/admin/v1/billing/invoices/${full.id}/void`, { reason }); closeSheet(); toast("Invoice voided"); after(); } }) }));
  openSheet(full.number || "Draft invoice",
    group(kv("Customer", full.tenant || "—"), kv("Period", monthName(full.period_month.slice(0, 7))), kv("Status", statusPill(INV_STATUS, st)), kv("Subtotal", money(full.subtotal_paise)), kv("Tax", money(full.tax_paise)), kv("Total", money(full.total_paise)),
      ...(full.due_at ? [kv("Due", fmtDateTime(full.due_at))] : []), ...(full.paid_at ? [kv("Paid", `${fmtDateTime(full.paid_at)} · ${full.payment_method || ""} ${full.payment_reference || ""}`)] : []), ...(full.void_reason ? [kv("Voided because", full.void_reason)] : [])),
    full.lines?.length ? [gh("What it is for"), group(...full.lines.map((l) => kv(l.description, money(l.amount_paise))))] : null, group(...rows));
}

function payment(X, inv, after) {
  const method = h("select", {}, [["bank_transfer", "Bank transfer"], ["upi", "UPI"], ["cheque", "Cheque"], ["cash", "Cash"], ["gateway", "Payment gateway"], ["other", "Other"]].map(([v, l]) => h("option", { value: v }, l)));
  const ref = h("input", { type: "text", placeholder: "For example the bank reference", maxlength: 100 }), amt = h("input", { type: "number", min: 1, step: "0.01", value: (inv.total_paise / 100).toFixed(2), inputmode: "decimal" });
  formSheet("Record a payment", [field("How they paid", method), field("Reference", ref), field("Amount (₹)", amt, `The invoice total is ${money(inv.total_paise)}.`)], "Save payment", async () => {
    await X.api.post(`/admin/v1/billing/invoices/${inv.id}/payments`, { method: method.value, reference: ref.value.trim(), amount_paise: Math.round(Number(amt.value) * 100) });
    closeSheet(); toast("Payment recorded"); after();
  });
}

async function billing(X) {
  if (!X.isOwner) return [title("Billing", ""), noaccess()];
  const st = X.mem.bill ||= { month: lastMonths(2)[1], f: "all" };
  const [sum, inv] = await Promise.all([X.api.get(`/admin/v1/billing/summary${qs({ month: st.month })}`), X.api.get(`/admin/v1/billing/invoices${qs({ month: st.month, limit: 200 })}`)]);
  const i = sum.invoices || {};
  const list = h("div", { class: "group" });
  const draw = () => { const l = inv.filter((x) => st.f === "all" || (st.f === "overdue" ? x.overdue : x.status === st.f)); mount(list, l.length ? l.map((x) => row({ icon: "doc", title: x.tenant, sub: `${x.number || "Draft"} · ${monthName(x.period_month.slice(0, 7))}`, right: h("span", { class: "rowend" }, h("b", {}, money(x.total_paise)), statusPill(INV_STATUS, x.overdue ? "overdue" : x.status), ic("chev", "chev")), onclick: () => invoiceSheet(X, x, () => X.refresh()) })) : h("div", { class: "gi static" }, h("div", { class: "muted" }, "No invoices match."))); };
  draw();
  const month = h("select", { "aria-label": "Month", onchange: (e) => { st.month = e.target.value; X.refresh(); } }, lastMonths(8).map((m) => h("option", { value: m, selected: m === st.month }, monthName(m))));
  const act = async (label, path, body, done) => { try { const r = await X.api.post(path, body); toast(done(r)); X.refresh(); } catch (e) { toast(errText(e), true); } };
  const menu = () => openSheet("Billing actions", group(
    row({ icon: "doc", title: "Generate invoices", sub: `Drafts for ${monthName(st.month)}`, onclick: () => { closeSheet(); act("g", "/admin/v1/billing/invoices/generate", { month: st.month }, (r) => `${r.created?.length ?? 0} drafts made`); } }),
    row({ icon: "check", title: "Issue all drafts", sub: "Numbers them and emails each customer", onclick: () => { closeSheet(); act("i", "/admin/v1/billing/invoices/issue-all", { month: st.month }, (r) => `${r.issued?.length ?? 0} issued`); } }),
    row({ icon: "bell", title: "Send reminders", sub: "Runs the overdue rules now", onclick: () => { closeSheet(); act("d", "/admin/v1/billing/dunning/run", {}, () => "Reminders sent"); } })));
  return [title("Billing", monthName(st.month), h("button", { class: "btn sm sec2", type: "button", onclick: menu }, "Actions")), field("Month", month),
    h("div", { class: "kpis" }, kpi("Billed", money(i.billed), "issued and paid"), kpi("Collected", money(i.collected)), kpi("Outstanding", money(i.outstanding), `${money(i.overdue)} overdue`), kpi("Drafts", fmtNum(i.drafts || 0), "not yet issued")),
    h("div", { class: "gap" }, chips([["all", "All"], ["draft", "Draft"], ["issued", "Issued"], ["overdue", "Overdue"], ["paid", "Paid"]], st.f, (v) => { st.f = v; draw(); })), list];
}

// ------------------------------------------------------------------------------------------------------ Plans
function planSheet(X, plan) {
  const isNew = !plan;
  const p = plan || { name: "", price_per_camera_paise: 99900, stream_mode: "snapshots", anpr: false, review_queue: false, active: true, price_change_policy: "next", customers: 0, customers_on_old_price: 0 };
  const name = h("input", { type: "text", value: p.name, maxlength: 60, placeholder: "For example Business" });
  const price = h("input", { type: "number", min: 0, step: "1", value: Math.round(p.price_per_camera_paise / 100), inputmode: "numeric" });
  const mode = h("select", {}, h("option", { value: "snapshots", selected: p.stream_mode === "snapshots" }, "Quick connect: a picture every few seconds"), h("option", { value: "realtime_5fps", selected: p.stream_mode === "realtime_5fps" }, "Full speed: live counting, 5 frames a second"));
  const policy = h("select", {}, h("option", { value: "next", selected: p.price_change_policy !== "keep" }, "Existing customers move to the new price from their next invoice"), h("option", { value: "keep", selected: p.price_change_policy === "keep" }, "Existing customers keep their old price; only new customers pay the new one"));
  const anpr = toggle(p.anpr, () => {}, "Number plate reading"), review = toggle(p.review_queue, () => {}, "Plate review queue"), offer = toggle(p.active, () => {}, "Offer to new customers");
  const sw = (t, label, sub) => h("div", { class: "gi static" }, h("div", {}, h("b", {}, label), h("small", {}, sub)), t);
  const why = h("input", { type: "text", placeholder: "For example: yearly price review", autocomplete: "off", "aria-label": "Reason" });
  const err = h("div"), save = h("button", { class: "btn", type: "button", disabled: true }, isNew ? "Create plan" : "Save changes");
  const del = isNew ? null : h("button", { class: "btn danger", type: "button", disabled: true }, p.customers ? "Retire only" : "Delete");
  const ok = () => why.value.trim().length >= 5;
  why.oninput = () => { save.disabled = !ok(); if (del) del.disabled = !ok() || p.customers > 0; };
  const body = () => ({ name: name.value.trim(), price_per_camera_paise: Math.round(Number(price.value) * 100), stream_mode: mode.value, anpr: anpr.querySelector("input").checked, review_queue: review.querySelector("input").checked, price_change_policy: policy.value, reason: why.value.trim() });
  save.onclick = async () => {
    save.disabled = true; clear(err);
    try {
      if (isNew) await X.api.post("/admin/v1/plans", body());
      else await X.api.put(`/admin/v1/plans/${p.id}`, { ...body(), active: offer.querySelector("input").checked });
      closeSheet(); toast(isNew ? "Plan created" : "Plan saved"); X.refresh();
    } catch (e) { err.append(note(errText(e), "err")); save.disabled = false; }
  };
  if (del) del.onclick = async () => { del.disabled = true; try { await X.api.del(`/admin/v1/plans/${p.id}${qs({ reason: why.value.trim() })}`); closeSheet(); toast("Plan deleted"); X.refresh(); } catch (e) { clear(err); err.append(note(errText(e), "err")); } };
  openSheet(isNew ? "New plan" : p.name, h("div", { class: "stack" },
    h("div", { class: "card" }, field("Plan name", name, "Customers see this name."), field("Price per camera per month (₹)", price, "Shown on invoices."), field("Picture speed", mode),
      field("When you change the price", policy, isNew ? "You can change this for each plan. It applies the next time you save a new price." : `A setting for this plan. It applies the next time you save a new price.${p.customers_on_old_price ? ` ${p.customers_on_old_price} customer${p.customers_on_old_price > 1 ? "s are" : " is"} on an older price now.` : ""}`)),
    gh("Included"), h("div", { class: "group" }, sw(anpr, "Number plate reading", "Reads plates at gates and keeps a vehicle log"), sw(review, "Plate review queue", "Shows uncertain plates for a person to check")),
    isNew ? null : h("div", { class: "group" }, sw(offer, "Offer to new customers", `${p.customers} ${p.customers === 1 ? "customer is" : "customers are"} on this plan${p.customers ? ". Turning this off does not affect them." : "."}`)),
    h("div", { class: "card" }, field("Reason (saved in the audit log)", why), err, h("div", { class: "two" }, del || h("button", { class: "btn gray", type: "button", onclick: () => closeSheet() }, "Cancel"), save),
      p.customers ? h("p", { class: "sub" }, "This plan has customers, so it cannot be deleted. Turn off “Offer to new customers” to retire it.") : null)));
}

async function plans(X) {
  if (!X.isOwner) return [title("Plans", ""), noaccess()];
  const items = (await X.api.get("/admin/v1/plans?include_retired=true")).items;
  return [back("More", "#/more"), title("Plans", "What customers can choose and what it costs", h("button", { class: "btn sm sec2", type: "button", onclick: () => planSheet(X, null) }, ic("plus"), "New")),
    group(...items.map((p) => row({ icon: "tag", title: p.name, sub: `${money(p.price_per_camera_paise)} per camera per month · ${p.customers} ${p.customers === 1 ? "customer" : "customers"}${p.anpr ? " · number plates" : ""}${p.price_change_policy === "keep" ? " · keeps old price for existing" : ""}`,
      right: h("span", { class: "rowend" }, pill(p.active ? "Offered" : "Retired", p.active ? "ok" : ""), ic("chev", "chev")), onclick: () => planSheet(X, p) }))),
    h("p", { class: "sub" }, "A plan with customers on it cannot be deleted. Retire it instead: new customers cannot pick it, existing ones keep it.")];
}

export const moneyScreens = { billing, plans };
