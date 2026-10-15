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
