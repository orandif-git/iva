// One list of every page in the Platform Admin. The sidebar, the phone tabs, the browser title, the search box and the
// "More" page all read from here, so a page is added (or moved between groups, or made owner-only) in one place.
//
// key: the address, "#/<key>". label: what staff see. icon: the icon name in app/ui.js. title: the browser-title name.
// group: which sidebar group it belongs to ("" is the top block). tab: shown in the phone's bottom bar.
// role: "owner" hides it from support (the server enforces the same). more: the line under it on the More page.

export const ROUTES = [
  { key: "today", label: "Today", icon: "today", title: "Today", group: "", tab: true },
  { key: "customers", label: "Customers", icon: "users", title: "Customers", group: "Customers", tab: true },
  { key: "fleet", label: "Fleet", icon: "server", title: "Fleet", group: "Customers", tab: true },
  { key: "billing", label: "Billing", icon: "card", title: "Billing", group: "Money", tab: true, role: "owner" },
  { key: "plans", label: "Plans", icon: "tag", title: "Plans", group: "Money", role: "owner", more: "Prices and what each plan includes" },
  { key: "alerts", label: "Alerts", icon: "bell", title: "Alerts", group: "Support", more: "Which alerts exist and who has them" },
  { key: "webhooks", label: "Webhooks", icon: "link", title: "Webhooks", group: "Support", role: "owner", more: "Where alerts are sent" },
  { key: "summaries", label: "WhatsApp summaries", icon: "chat", title: "WhatsApp summaries", group: "Support", role: "owner" },
  { key: "audit", label: "Audit log", icon: "log", title: "Audit log", group: "Support", more: "Every staff action" },
  { key: "storage", label: "Storage", icon: "cloud", title: "Storage", group: "System", role: "owner", more: "Where evidence pictures and clips are kept" },
  { key: "maintenance", label: "Maintenance", icon: "wrench", title: "Maintenance", group: "System", role: "owner", more: "Data retention and job runs" },
  { key: "staff", label: "Staff and roles", icon: "shield", title: "Staff and roles", group: "System", role: "owner", more: "Who can do what" },
];

/// The order groups appear in the sidebar. A group with no pages for this role is left out.
export const GROUP_ORDER = ["", "Customers", "Money", "Support", "System"];

/// The phone's bottom bar, in order, plus "More" which is added by the shell.
export const TABS = ROUTES.filter((r) => r.tab).map((r) => r.key);

/// The pages in the More sheet, for a role that may open them.
export const moreItems = (isOwner) => ROUTES.filter((r) => r.more && (!r.role || isOwner));

/// What the search box offers as pages, for a role that may open them.
export const pagesFor = (isOwner) => ROUTES.filter((r) => !r.role || isOwner).map((r) => ({ label: r.label, icon: r.icon, href: "#/" + r.key }));

/// The browser title for every address, including "more" which is not a route of its own.
export const TITLES = Object.fromEntries([...ROUTES.map((r) => [r.key, r.title]), ["more", "More"]]);

/// The sidebar groups, keeping only pages this role may open. Empty groups are dropped.
export const groupsFor = (isOwner) =>
  GROUP_ORDER.map((label) => ({ label, items: ROUTES.filter((r) => r.group === label && (!r.role || isOwner)).map((r) => ({ key: r.key, label: r.label, icon: r.icon })) }))
    .filter((g) => g.items.length);

/// The menu entry for a key, if this role may open it.
export const navItem = (key, isOwner) => {
  const r = ROUTES.find((x) => x.key === key);
  return r && (!r.role || isOwner) ? { key: r.key, label: r.label, icon: r.icon } : null;
};
