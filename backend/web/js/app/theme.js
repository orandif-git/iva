// Light, dark or automatic appearance, remembered on this device. Shared by the customer app and the staff console.
// The customer app starts in light, whatever the phone's own setting is, and goes dark only when the person chooses it.
// The staff console follows the device by default.
export const THEME_KEY = "gi_theme";
const FALLBACK = location.pathname.startsWith("/platform") ? "auto" : "light";
export const theme = {
  fallback: FALLBACK,
  get() { try { const v = localStorage.getItem(THEME_KEY); return v === "light" || v === "dark" || v === "auto" ? v : FALLBACK; } catch { return FALLBACK; } },
  set(v) {
    try { localStorage.setItem(THEME_KEY, v); } catch { /* private mode */ }
    theme.apply();
  },
  apply() {
    const v = theme.get(), root = document.documentElement;
    v === "auto" ? root.removeAttribute("data-theme") : root.setAttribute("data-theme", v);
    const dark = v === "dark" || (v === "auto" && matchMedia("(prefers-color-scheme: dark)").matches);
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#070C12" : "#EEF2F7");
  },
};
theme.apply();
matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => theme.apply());
