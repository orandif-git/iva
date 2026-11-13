// The console's live counts. The server sends a small event whenever the numbers change; this reads it with fetch (the sign-in
// travels in a header, never in a URL), reconnects by itself, and stops cleanly when the tab signs out.
//
// If the browser has no streams, or the server is behind something that holds the first bytes, nothing breaks: the pages still
// load and refresh when opened. This only saves a person from pressing refresh to notice a recorder went offline.
export function watchEvents(api, { onCounts, onState } = {}) {
  let stopped = false, controller = null, timer = null;

  const connect = async () => {
    if (stopped) return;
    controller = new AbortController();
    try {
      const r = await fetch("/admin/v1/events", {
        method: "GET",
        headers: { Accept: "text/event-stream", ...(api.access ? { Authorization: "Bearer " + api.access } : {}) },
        cache: "no-store",
        signal: controller.signal,
      });
      if (r.status === 401 && await api.refresh()) return connect();      // the short-lived access token had run out
      if (!r.ok || !r.body || typeof r.body.getReader !== "function") throw new Error("no stream");
      onState?.("on");
      const reader = r.body.getReader(), decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done || stopped) break;
        buffer += decoder.decode(value, { stream: true });
        let cut;
        while ((cut = buffer.indexOf("\n\n")) >= 0) {
          const block = buffer.slice(0, cut);
          buffer = buffer.slice(cut + 2);
          const data = block.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("\n");
          if (!data) continue;
          try { onCounts?.(JSON.parse(data)); } catch { /* one bad line must not end the stream */ }
        }
      }
    } catch { /* offline or closed: reconnected below */ }
    if (stopped) return;
    onState?.("off");
    timer = setTimeout(connect, 30000);
  };

  connect();
  return { stop() { stopped = true; clearTimeout(timer); controller?.abort(); } };
}
