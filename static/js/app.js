// Shared helpers for every page.

window.SecureFix = {
  toast(message) {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = message;
    el.classList.add("is-visible");
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.remove("is-visible"), 2600);
  },

  // JSON by default. FormData / Blob / string bodies are sent as given, and no
  // Content-Type is forced so the browser can set the multipart boundary.
  async api(path, options = {}) {
    const { headers: extra, body, ...rest } = options;
    const headers = { ...(extra || {}) };
    let payload = body;
    const isRaw = typeof FormData !== "undefined" && body instanceof FormData
      || (typeof Blob !== "undefined" && body instanceof Blob);
    if (body !== undefined && body !== null && !isRaw) {
      if (typeof body !== "string") payload = JSON.stringify(body);
      if (!Object.keys(headers).some((k) => k.toLowerCase() === "content-type")) {
        headers["Content-Type"] = "application/json";
      }
    }
    const res = await fetch(path, { credentials: "same-origin", ...rest, headers, body: payload });
    if (!res.ok) {
      const text = await res.text();
      let message = text;
      try {
        const data = JSON.parse(text);
        if (data && typeof data.detail === "string") message = data.detail;
      } catch (_) { /* plain text error */ }
      throw new Error(message || `Request failed: ${res.status}`);
    }
    return res.status === 204 ? null : res.json();
  },

  // Keep a chat thread pinned to its newest message.
  scrollToEnd(el, smooth = true) {
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  },
};

// Auto-growing textareas: <textarea data-autogrow>
document.addEventListener("input", (e) => {
  const t = e.target;
  if (t && t.matches && t.matches("textarea[data-autogrow]")) {
    t.style.height = "auto";
    t.style.height = Math.min(t.scrollHeight, 160) + "px";
  }
});

// Mobile chat history drawer: <button data-toggle-sidebar> opens/closes .chat-sidebar
document.addEventListener("click", (e) => {
  const btn = e.target.closest && e.target.closest("[data-toggle-sidebar]");
  const side = document.querySelector(".chat-sidebar");
  if (!side) return;
  if (btn) {
    side.classList.toggle("is-open");
  } else if (side.classList.contains("is-open") && !side.contains(e.target)) {
    side.classList.remove("is-open");
  }
});

// Floating assistant: the round button in the bottom-left corner opens the chat in a panel.
(function () {
  const launcher = document.getElementById("chat-launcher");
  const panel = document.getElementById("chat-panel");
  const frame = document.getElementById("chat-frame");
  if (!launcher || !panel || !frame) return;
  function setOpen(open) {
    panel.hidden = !open;
    launcher.setAttribute("aria-expanded", String(open));
    launcher.classList.toggle("is-open", open);
    if (open && !frame.getAttribute("src")) frame.setAttribute("src", frame.dataset.src);
    if (open) setTimeout(() => frame.focus(), 50);
  }
  launcher.addEventListener("click", () => setOpen(panel.hidden));
  // Full screen opens the conversation that is in the panel now (its run, analysis or thread), not a new one.
  const full = panel.querySelector("[data-chat-full]");
  if (full) full.addEventListener("click", () => {
    try {
      const u = new URL(frame.contentWindow.location.href);
      u.searchParams.delete("embed");
      if (u.origin === location.origin && u.pathname.startsWith("/chat")) full.href = u.pathname + u.search;
    } catch (_) { /* not loaded yet: plain /chat continues the remembered thread anyway */ }
  });
  panel.addEventListener("click", (e) => { if (e.target.closest("[data-chat-close]")) { setOpen(false); launcher.focus(); } });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.hidden && !document.querySelector("dialog[open]")) { setOpen(false); launcher.focus(); }
  });
  // Other scripts can open it: SecureFix.openChat()
  window.SecureFix.openChat = (src) => {
    if (src) frame.setAttribute("src", src);
    setOpen(true);
  };
})();

// Inside the floating panel (/chat?embed=1): keep every chat URL embedded, and open other pages in the main window.
(function () {
  if (!document.body.classList.contains("is-embed")) return;
  const embedUrl = (url) => {
    const u = new URL(url, location.href);
    if (u.origin === location.origin && u.pathname.startsWith("/chat")) u.searchParams.set("embed", "1");
    return u.pathname + u.search + u.hash;
  };
  const push = history.pushState.bind(history);
  history.pushState = (state, title, url) => push(state, title, url == null ? url : embedUrl(url));
  const replace = history.replaceState.bind(history);
  history.replaceState = (state, title, url) => replace(state, title, url == null ? url : embedUrl(url));
  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a[href]");
    if (!a || a.target === "_blank" || a.hasAttribute("download")) return;
    const u = new URL(a.href, location.href);
    if (u.origin !== location.origin || u.pathname.startsWith("/api/")) return;
    if (u.pathname.startsWith("/chat")) { a.href = embedUrl(a.href); return; }
    e.preventDefault();
    window.top.location.href = a.href;
  }, true);
})();
