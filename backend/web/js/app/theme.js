// Light, dark or automatic appearance, remembered on this device. Shared by the customer app and the staff console.
export const THEME_KEY = "gi_theme";
export const theme = {
  get() { try { return localStorage.getItem(THEME_KEY) || "auto"; } catch { return "auto"; } },
  set(v) {
    try { v === "auto" ? localStorage.removeItem(THEME_KEY) : localStorage.setItem(THEME_KEY, v); } catch { /* private mode */ }
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
