// One table for both apps. On a wide window it is a real table (sortable headers, optional row select, density switch).
// On a phone the very same rows are shown as cards. Cells are built from text nodes only, never from HTML.
import { h, mount } from "../lib.js";
import { ic } from "./ui.js";

const DENSITY_KEY = "gi_density";
const density = {
  get() { try { return localStorage.getItem(DENSITY_KEY) === "compact" ? "compact" : "comfy"; } catch { return "comfy"; } },
  set(v) { try { localStorage.setItem(DENSITY_KEY, v); } catch { /* private mode */ } },
};
const num = (v) => (typeof v === "number" ? v : null);
const cmp = (a, b) => {
  if (a == null && b == null) return 0;
  if (a == null) return 1;                                  // empty values always sort last
  if (b == null) return -1;
  return num(a) !== null && num(b) !== null ? a - b : String(a).localeCompare(String(b), "en", { numeric: true, sensitivity: "base" });
};

/**
 * columns: [{ key, label, render?(row) -> node|text, value?(row) -> sortable value, sort?: true, primary?: true, minor?: true, align?: "end" }]
 *   primary: the cell that carries the link (and the card title on a phone). minor: hidden on a tablet to save room.
 * options: { label, rows, rowKey(row), href?(row), onOpen?(row), empty?: node, sort?: {key, dir}, bulk?: [{label, run(rows)}], memory?: object }
 * Returns an element with .setRows(rows). Sort, selection and density are kept in options.memory so they survive a refresh.
 */
export function dataTable(opts) {
  const { columns, rowKey, label } = opts;
  const mem = opts.memory || {};
  mem.sort ||= opts.sort || null;
  const selected = (mem.selected ||= new Set());
  let rows = opts.rows || [];
  const bulk = opts.bulk || [];
  const selectable = bulk.length > 0;
  const root = h("div", { class: "dt-wrap", "data-density": density.get() });
  const live = h("div", { class: "sr", role: "status", "aria-live": "polite" });
  const body = h("tbody");

  const sorted = () => {
    const s = mem.sort, col = s && columns.find((c) => c.key === s.key);
    if (!col) return rows;
    const val = col.value || ((r) => r[col.key]);
    return [...rows].sort((a, b) => cmp(val(a), val(b)) * (s.dir === "desc" ? -1 : 1));
  };
  const setSort = (key, dir) => {
    const was = mem.sort;
    mem.sort = { key, dir: dir || (was && was.key === key && was.dir === "asc" ? "desc" : "asc") };
    live.textContent = `Sorted by ${columns.find((c) => c.key === key).label}, ${mem.sort.dir === "asc" ? "ascending" : "descending"}.`;
    draw();
  };

  // ---- toolbar: count, phone sort menu, density
  const count = h("span", { class: "dt-count" });
  const sortable = columns.filter((c) => c.sort);
  const sortSel = h("select", { "aria-label": "Sort by", onchange: (e) => { const [k, d] = e.target.value.split(":"); setSort(k, d); } },
    sortable.flatMap((c) => [h("option", { value: c.key + ":asc" }, `${c.label} (A to Z)`), h("option", { value: c.key + ":desc" }, `${c.label} (Z to A)`)]));
  const dens = h("button", { class: "dt-dens", type: "button", "aria-pressed": String(density.get() === "compact"), title: "Compact rows", onclick: () => {
    const next = density.get() === "compact" ? "comfy" : "compact";
    density.set(next); root.setAttribute("data-density", next); dens.setAttribute("aria-pressed", String(next === "compact"));
  } }, ic("rows"), h("span", { class: "sr" }, "Compact rows"));
  const bar = h("div", { class: "dt-bar" }, count, h("span", { class: "grow" }), sortable.length ? h("label", { class: "dt-sort" }, h("span", { class: "sr" }, "Sort by"), sortSel) : null, dens);
  const bulkBar = h("div", { class: "dt-bulk", hidden: true, role: "region", "aria-label": "Selected rows" });

  // ---- head
  const allBox = h("input", { type: "checkbox", "aria-label": "Select all rows", onchange: (e) => { rows.forEach((r) => (e.target.checked ? selected.add(rowKey(r)) : selected.delete(rowKey(r)))); draw(); } });
  const headRow = h("tr", {}, selectable ? h("th", { class: "dt-sel", scope: "col" }, allBox) : null, columns.map((c) => {
    const th = h("th", { scope: "col", class: (c.minor ? "minor " : "") + (c.align === "end" ? "end" : "") });
    if (c.sort) th.append(h("button", { type: "button", class: "dt-h", onclick: () => setSort(c.key) }, c.label, h("span", { class: "dt-arrow", "aria-hidden": "true" })));
    else th.append(c.label);
    th.dataset.key = c.key;
    return th;
  }));
  const thead = h("thead", {}, headRow);
  const table = h("table", { class: "dt", "aria-label": label || "Table" }, thead, body);

  function drawHead() {
    const s = mem.sort;
    headRow.querySelectorAll("th[data-key]").forEach((th) => {
      if (s && th.dataset.key === s.key) th.setAttribute("aria-sort", s.dir === "asc" ? "ascending" : "descending"); else if (th.querySelector("button")) th.setAttribute("aria-sort", "none");
    });
    if (s) sortSel.value = `${s.key}:${s.dir}`;
  }
  function drawBulk() {
    const picked = rows.filter((r) => selected.has(rowKey(r)));
    bulkBar.hidden = !selectable || picked.length === 0;
    if (selectable) {
      allBox.checked = picked.length > 0 && picked.length === rows.length;
      allBox.indeterminate = picked.length > 0 && picked.length < rows.length;
      mount(bulkBar, h("b", {}, `${picked.length} selected`), bulk.map((b) => h("button", { class: "btn sm gray", type: "button", onclick: () => b.run(picked) }, b.label)),
        h("button", { class: "btn sm gray", type: "button", onclick: () => { selected.clear(); draw(); } }, "Clear"));
    }
  }
  function open(r, e) {
    if (e && e.target.closest("a,button,input,select,label")) return;
    if (opts.onOpen) opts.onOpen(r);
  }
  function draw() {
    drawHead();
    const list = sorted();
    count.textContent = `${list.length} ${list.length === 1 ? "row" : "rows"}`;
    if (!list.length) {
      root.classList.add("is-empty");
      mount(body);
      return drawBulk();
    }
    root.classList.remove("is-empty");
    mount(body, list.map((r) => {
      const key = rowKey(r);
      const tr = h("tr", { class: opts.onOpen ? "open" : "", "aria-selected": selectable ? String(selected.has(key)) : null, onclick: (e) => open(r, e) },
        selectable ? h("td", { class: "dt-sel" }, h("input", { type: "checkbox", checked: selected.has(key), "aria-label": "Select row", onchange: (e) => { e.target.checked ? selected.add(key) : selected.delete(key); drawBulk(); tr.setAttribute("aria-selected", String(e.target.checked)); } })) : null,
        columns.map((c) => {
          const v = c.render ? c.render(r) : r[c.key];
          const inner = c.primary && opts.href ? h("a", { class: "dt-link", href: opts.href(r) }, v) : v;
          return h("td", { "data-label": c.label, class: (c.primary ? "primary " : "") + (c.minor ? "minor " : "") + (c.align === "end" ? "end" : "") }, inner ?? "—");
        }));
      return tr;
    }));
    drawBulk();
  }
  root.setRows = (next) => { rows = next; [...selected].forEach((k) => { if (!rows.some((r) => rowKey(r) === k)) selected.delete(k); }); draw(); };
  root.append(bar, bulkBar, h("div", { class: "dt-scroll" }, table), h("div", { class: "dt-empty" }, opts.empty || h("p", { class: "sub" }, "Nothing to show.")), live);
  draw();
  return root;
}
