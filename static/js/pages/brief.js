// Project brief screen: save the brief, generate rules, toggle rules.
// API contract assumed (backend is being built in parallel):
//   POST  /api/brief                 { summary, users, permissions } -> { brief: { updated }, rules }
//   POST  /api/brief/rules           {}                              -> { rules: [rule], source }
//   PATCH /api/rules/{id}            { enabled }                     -> { rule }
// rule = { id, number, text, enabled, hits }

(function () {
  const form = document.getElementById("brief-form");
  if (!form) return;

  const toast = (msg) => window.SecureFix && window.SecureFix.toast(msg);
  const api = (path, options) => window.SecureFix.api(path, options);

  const fields = {
    summary: form.elements.summary,
    users: form.elements.users,
    permissions: form.elements.permissions,
  };
  const generateButtons = Array.from(document.querySelectorAll("[data-generate]"));
  const rulesList = document.querySelector("[data-rules]");
  const rulesCount = document.querySelector("[data-rules-count]");
  const rulesCountShort = document.querySelector("[data-rules-count-short]");
  const dirtyFlag = document.querySelector("[data-dirty]");
  const errorBox = document.querySelector("[data-form-error]");
  const rolesList = document.querySelector("[data-roles]");
  const updatedText = document.getElementById("brief-updated");

  /* ---------- Live helpers: counters, roles, filled state, dirty flag ---------- */

  function updateCount(textarea) {
    const out = document.querySelector(`[data-count-for="${textarea.id}"]`);
    if (!out) return;
    const max = Number(textarea.getAttribute("maxlength")) || 0;
    out.textContent = max ? `${textarea.value.length} of ${max} characters` : "";
  }

  function renderRoles() {
    if (!rolesList) return;
    rolesList.replaceChildren();
    fields.users.value
      .split(",")
      .map((r) => r.trim())
      .filter(Boolean)
      .forEach((role) => {
        const li = document.createElement("li");
        li.className = "brief-role";
        li.textContent = role;
        rolesList.appendChild(li);
      });
  }

  function updateFilledState() {
    Object.entries(fields).forEach(([name, el]) => {
      const state = document.querySelector(`[data-state-for="${name}"]`);
      if (!state) return;
      const done = el.value.trim().length > 0;
      state.textContent = done ? "Filled in" : "Not filled in yet";
      state.classList.toggle("is-done", done);

      const seg = document.querySelector(`[data-progress="${name}"]`);
      if (seg) {
        seg.classList.toggle("seg-butter", done);
        seg.classList.toggle("seg-hatch", !done);
        const note = seg.querySelector(".sr-only");
        if (note) note.textContent = done ? ", filled in" : ", not filled in yet";
      }
    });
  }

  function isDirty() {
    return Object.values(fields).some((el) => el.value !== el.defaultValue);
  }

  function updateDirty() {
    if (dirtyFlag) dirtyFlag.hidden = !isDirty();
  }

  function refreshAll() {
    form.querySelectorAll("textarea").forEach(updateCount);
    renderRoles();
    updateFilledState();
    updateDirty();
  }

  form.addEventListener("input", (event) => {
    const target = event.target;
    if (target.tagName === "TEXTAREA") updateCount(target);
    if (target === fields.users) renderRoles();
    const field = target.closest(".brief-field");
    if (field && target.value.trim()) field.removeAttribute("aria-invalid");
    updateFilledState();
    updateDirty();
  });

  form.addEventListener("reset", () => {
    // Reset fires before values change.
    setTimeout(() => {
      Object.values(fields).forEach((el) => el.closest(".brief-field")?.removeAttribute("aria-invalid"));
      showError("");
      refreshAll();
    }, 0);
  });

  window.addEventListener("beforeunload", (event) => {
    if (isDirty()) {
      event.preventDefault();
      event.returnValue = "";
    }
  });

  /* ---------- Section list: highlight the section in view ---------- */

  const steps = Array.from(document.querySelectorAll(".brief-step[data-section]"));

  function setCurrentStep(name) {
    steps.forEach((s) => {
      if (s.dataset.section === name) s.setAttribute("aria-current", "true");
      else s.removeAttribute("aria-current");
    });
  }

  steps.forEach((step) => {
    step.addEventListener("click", () => {
      setCurrentStep(step.dataset.section);
      const target = document.querySelector(`[data-section-target="${step.dataset.section}"]`);
      const focusable = target && target.querySelector("textarea, input");
      if (focusable) setTimeout(() => focusable.focus({ preventScroll: true }), 250);
    });
  });

  if ("IntersectionObserver" in window) {
    const visible = new Map();
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => visible.set(e.target.dataset.sectionTarget, e.isIntersecting ? e.intersectionRatio : 0));
        let best = null;
        let bestRatio = 0;
        steps.forEach((s) => {
          const r = visible.get(s.dataset.section) || 0;
          if (r > bestRatio) { best = s.dataset.section; bestRatio = r; }
        });
        if (best) setCurrentStep(best);
      },
      { rootMargin: "-90px 0px -35% 0px", threshold: [0, 0.25, 0.5, 0.75, 1] }
    );
    document.querySelectorAll("[data-section-target]").forEach((el) => observer.observe(el));
  }

  /* ---------- Rules list ---------- */

  function normaliseRules(data) {
    const list = Array.isArray(data) ? data : (data && Array.isArray(data.rules) ? data.rules : []);
    return list.map((r, i) =>
      typeof r === "string"
        ? { id: i + 1, text: r, enabled: true, hits: 0 }
        : { id: r.id ?? i + 1, text: r.text ?? r.rule ?? "", enabled: r.enabled !== false, hits: Number(r.hits) || 0 }
    ).filter((r) => r.text);
  }

  function ruleItem(rule, animate) {
    const li = document.createElement("li");
    li.className = "rule" + (rule.enabled ? "" : " is-off") + (animate ? " is-new" : "");
    li.dataset.ruleId = rule.id;
    li.dataset.hits = rule.hits || 0;

    const check = document.createElement("span");
    check.className = "check is-done";
    check.setAttribute("aria-hidden", "true");
    check.innerHTML =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>';

    const body = document.createElement("div");
    body.className = "rule-body";

    const text = document.createElement("p");
    text.className = "rule-text";
    text.id = `rule-text-${rule.id}`;
    text.textContent = rule.text;
    body.appendChild(text);

    const meta = document.createElement("span");
    if (rule.hits) {
      meta.className = "badge badge-attack rule-hits";
      meta.textContent = `Caught ${rule.hits} bug${rule.hits === 1 ? "" : "s"}`;
    } else {
      meta.className = "rule-quiet hint";
      meta.textContent = "No bugs caught yet";
    }
    body.appendChild(meta);

    const label = document.createElement("label");
    label.className = "switch";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = rule.enabled;
    input.dataset.ruleToggle = "";
    input.setAttribute("aria-labelledby", text.id);
    label.append(input, document.createElement("span"));

    li.append(check, body, label);
    if (animate) li.style.animationDelay = `${Math.min(animate - 1, 8) * 60}ms`;
    return li;
  }

  function renderRules(rules) {
    rulesList.replaceChildren();
    if (!rules.length) {
      const li = document.createElement("li");
      li.className = "rules-empty";
      li.innerHTML = '<p class="rule-text">No rules yet</p><p class="hint">Add more detail to who can do what, then try again.</p>';
      rulesList.appendChild(li);
    } else {
      rules.forEach((rule, i) => rulesList.appendChild(ruleItem(rule, i + 1)));
    }
    updateRuleCount();
  }

  function showSkeleton() {
    rulesList.replaceChildren();
    for (let i = 0; i < 4; i++) {
      const li = document.createElement("li");
      li.className = "rule-skeleton";
      li.setAttribute("aria-hidden", "true");
      rulesList.appendChild(li);
    }
  }

  function updateRuleCount() {
    const toggles = rulesList.querySelectorAll("[data-rule-toggle]");
    const on = Array.from(toggles).filter((t) => t.checked).length;
    const total = toggles.length;
    if (rulesCount) rulesCount.textContent = total ? `${on} of ${total} rules are on` : "No rules yet";
    if (rulesCountShort) rulesCountShort.textContent = total ? `${on} of ${total} on` : "None yet";

    // Big numerals, marker meter and bugs caught follow the same data.
    const setText = (selector, value) => {
      const el = document.querySelector(selector);
      if (el) el.textContent = String(value);
    };
    setText("[data-stat-on]", on);
    setText("[data-stat-total]", total);
    const caught = Array.from(rulesList.querySelectorAll(".rule")).reduce(
      (sum, item) => sum + (Number(item.dataset.hits) || 0),
      0
    );
    setText("[data-stat-caught]", caught);
    const caughtNote = document.querySelector("[data-stat-caught] + .sr-only");
    if (caughtNote) caughtNote.textContent = `${caught} bugs caught so far`;
    const meter = document.querySelector("[data-rules-meter]");
    if (meter) meter.style.setProperty("--v", `${total ? Math.round((on / total) * 1000) / 10 : 0}%`);
  }

  rulesList.addEventListener("change", async (event) => {
    const input = event.target;
    if (!input.matches("[data-rule-toggle]")) return;
    const item = input.closest(".rule");
    const id = item.dataset.ruleId;
    const enabled = input.checked;
    const ruleText = item.querySelector(".rule-text")?.textContent || "Rule";

    item.classList.toggle("is-off", !enabled);
    updateRuleCount();
    input.disabled = true;

    try {
      await api(`/api/rules/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      });
      toast(enabled ? "Rule turned on" : "Rule turned off");
    } catch (err) {
      input.checked = !enabled;
      item.classList.toggle("is-off", enabled);
      updateRuleCount();
      toast(`Could not change "${ruleText}". Try again.`);
    } finally {
      input.disabled = false;
    }
  });

  /* ---------- Save and generate ---------- */

  function setBusy(busy, label) {
    generateButtons.forEach((btn) => {
      btn.disabled = busy;
      btn.setAttribute("aria-busy", String(busy));
      const span = btn.querySelector("[data-generate-label]");
      if (span) span.textContent = label;
    });
    rulesList.setAttribute("aria-busy", String(busy));
  }

  function showError(message) {
    if (!errorBox) return;
    errorBox.textContent = message;
    errorBox.hidden = !message;
  }

  function validate() {
    let firstEmpty = null;
    Object.values(fields).forEach((el) => {
      const field = el.closest(".brief-field");
      const empty = !el.value.trim();
      if (field) {
        if (empty) field.setAttribute("aria-invalid", "true");
        else field.removeAttribute("aria-invalid");
      }
      if (empty && !firstEmpty) firstEmpty = el;
    });
    if (firstEmpty) {
      showError("Please fill in all three questions first.");
      firstEmpty.focus();
      return false;
    }
    showError("");
    return true;
  }

  let running = false;

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (running || !validate()) return;
    running = true;

    const payload = {
      summary: fields.summary.value.trim(),
      users: fields.users.value.trim(),
      permissions: fields.permissions.value.trim(),
    };
    const previousRules = Array.from(rulesList.children).map((n) => n.cloneNode(true));

    setBusy(true, "Saving brief");
    let saved;
    try {
      saved = await api("/api/brief", { method: "POST", body: JSON.stringify(payload) });
    } catch (err) {
      setBusy(false, "Save and generate rules");
      running = false;
      toast("Could not save the brief. Try again in a moment.");
      return;
    }

    // Brief is saved: the form's current values become the new baseline.
    Object.values(fields).forEach((el) => { el.defaultValue = el.value; });
    updateDirty();
    if (updatedText) {
      const when = (saved && ((saved.brief && saved.brief.updated) || saved.updated)) || "just now";
      updatedText.textContent = `Last saved ${/^(Just now|Yesterday)$/.test(when) ? when.toLowerCase() : when}`;
    }

    setBusy(true, "Writing rules");
    showSkeleton();
    try {
      const data = await api("/api/brief/rules", { method: "POST", body: "{}" });
      const rules = normaliseRules(data);
      renderRules(rules);
      toast(rules.length ? `Brief saved. ${rules.length} rules ready.` : "Brief saved, but no rules came back.");
    } catch (err) {
      rulesList.replaceChildren(...previousRules);
      updateRuleCount();
      toast("Brief saved, but rules could not be generated. Try again.");
    } finally {
      setBusy(false, "Save and generate rules");
      running = false;
    }
  });

  refreshAll();
  updateRuleCount();
})();
