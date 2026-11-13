// How the Control Center talks to the server: one place that adds the sign-in, renews it when it runs out, and turns errors into plain messages.
export class ApiError extends Error {
  constructor(status, code, message, details) { super(message); this.status = status; this.code = code; this.details = details; }
}

export class Api {
  /** refreshUrl: where a refresh token is exchanged. key: where it is kept. Staff use sessionStorage (gone when the tab closes). */
  constructor({ refreshUrl, key, storage = localStorage, onSignedOut }) {
    Object.assign(this, { refreshUrl, key, storage, onSignedOut });
    this.access = null;
    this._refreshing = null;
  }
  get refreshToken() { try { return this.storage.getItem(this.key); } catch { return null; } }
  set refreshToken(v) { try { v ? this.storage.setItem(this.key, v) : this.storage.removeItem(this.key); } catch { /* private mode */ } }
  signedIn() { return !!(this.access || this.refreshToken); }
  setTokens(t) { this.access = t.access_token; this.refreshToken = t.refresh_token; }
  clear() { this.access = null; this.refreshToken = null; }

  async refresh() {
    if (this._refreshing) return this._refreshing;           // one refresh at a time: a refresh token works once
    const run = async () => {
      const rt = this.refreshToken;
      if (!rt) return false;
      let r;
      try { r = await fetch(this.refreshUrl, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: rt }) }); }
      catch { return false; }                                  // no network: keep the sign-in
      if (!r.ok) { this.clear(); return false; }
      this.setTokens(await r.json());
      return true;
    };
    // The lock also serialises tabs, so two open tabs cannot both spend the same refresh token.
    this._refreshing = (navigator.locks ? navigator.locks.request("gi-refresh-" + this.key, run) : run()).finally(() => { this._refreshing = null; });
    return this._refreshing;
  }

  async call(method, url, body, { blob = false, retry = true } = {}) {
    if (!this.access && this.refreshToken) await this.refresh();
    const headers = { ...(this.headers || {}) };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (this.access) headers.Authorization = "Bearer " + this.access;
    let r;
    try { r = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) }); }
    catch { throw new ApiError(0, "network", "Cannot reach the server. Check your internet connection."); }
    if (r.status === 401 && retry) {
      const j = await r.clone().json().catch(() => ({}));
      if (["invalid_token", "missing_token"].includes(j.error?.code) && await this.refresh()) return this.call(method, url, body, { blob, retry: false });
      if (["invalid_token", "missing_token"].includes(j.error?.code)) { this.clear(); this.onSignedOut?.(); }
    }
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      throw new ApiError(r.status, j.error?.code || "error", j.error?.message || "Something went wrong. Try again.", j.error?.details);
    }
    if (r.status === 204) return null;
    return blob ? r.blob() : r.json();
  }
  get(u, o) { return this.call("GET", u, undefined, o); }
  post(u, b) { return this.call("POST", u, b ?? {}); }
  put(u, b) { return this.call("PUT", u, b); }
  patch(u, b) { return this.call("PATCH", u, b); }
  del(u) { return this.call("DELETE", u); }
}
