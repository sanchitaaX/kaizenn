// Onboarding: pick a role and a repository, optionally go to the project brief next.

(function () {
  const form = document.getElementById("ob-form");
  if (!form) return;

  const submitBtn = document.getElementById("ob-submit");
  const submitLabel = submitBtn.querySelector("[data-label]");
  const modeLabel = form.querySelector("[data-mode-label]");
  const destHint = document.getElementById("ob-dest");
  const briefNext = document.getElementById("brief-next");
  const progress = document.querySelectorAll(".ob-progress li");

  const MODE_BY_ROLE = { student: "Learn mode", professional: "Report mode" };

  const toast = (msg) => {
    if (window.SecureFix && typeof window.SecureFix.toast === "function") window.SecureFix.toast(msg);
  };

  const checkedValue = (name) => {
    const el = form.querySelector(`input[name="${name}"]:checked`)
      || form.querySelector(`input[type="text"][name="${name}"]`);
    return el ? el.value.trim() : "";
  };

  // Keep card highlight in sync for browsers without :has()
  function syncChecked() {
    form.querySelectorAll(".ob-option").forEach((opt) => {
      const input = opt.querySelector("input");
      opt.classList.toggle("is-checked", !!(input && input.checked));
    });
  }

  function syncProgress(current) {
    const done = {
      role: !!checkedValue("role"),
      repo: !!checkedValue("repo"),
      brief: false,
    };
    progress.forEach((li) => {
      const step = li.dataset.step;
      const isCurrent = step === current;
      li.classList.toggle("is-current", isCurrent);
      li.classList.toggle("is-done", !isCurrent && done[step]);
      if (isCurrent) li.setAttribute("aria-current", "step");
      else li.removeAttribute("aria-current");
    });
  }

  function syncMode() {
    const role = checkedValue("role");
    if (modeLabel && MODE_BY_ROLE[role]) modeLabel.textContent = MODE_BY_ROLE[role];
  }

  function syncDestination() {
    if (!destHint) return;
    destHint.textContent = briefNext && briefNext.checked
      ? "Next you'll go to the project brief."
      : "Next you'll go to your dashboard.";
  }

  // Arrow keys inside a radiogroup are handled natively by the browser.
  form.addEventListener("change", (e) => {
    syncChecked();
    if (e.target.name === "role") syncMode();
    if (e.target === briefNext) syncDestination();
    const step = e.target.closest(".ob-step");
    syncProgress(step ? step.dataset.step : "role");
  });

  form.querySelectorAll(".ob-step").forEach((fs) => {
    fs.addEventListener("focusin", () => syncProgress(fs.dataset.step));
  });

  function setBusy(busy) {
    submitBtn.disabled = busy;
    submitBtn.setAttribute("aria-busy", busy ? "true" : "false");
    submitLabel.textContent = busy ? "Saving" : "Finish setup";
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    const role = checkedValue("role");
    const repo = checkedValue("repo");
    if (!role) {
      toast("Choose Student or Professional to continue.");
      const first = form.querySelector('input[name="role"]');
      if (first) first.focus();
      return;
    }
    if (!repo) {
      toast("Choose a repository to continue.");
      const first = form.querySelector('input[name="repo"]');
      if (first) first.focus();
      return;
    }

    const wantsBrief = !!(briefNext && briefNext.checked);
    setBusy(true);

    try {
      const data = await window.SecureFix.api("/api/onboarding", {
        method: "POST",
        body: JSON.stringify({ role, repo, brief_next: wantsBrief }),
      });
      const fallback = wantsBrief ? "/brief" : "/dashboard";
      const target = data && typeof data.redirect === "string" && data.redirect.startsWith("/")
        && !data.redirect.startsWith("//")
        ? data.redirect
        : fallback;
      window.location.assign(target);
    } catch (err) {
      setBusy(false);
      let detail = "";
      try { detail = JSON.parse(err.message).detail; } catch (e) { /* not JSON */ }
      toast(typeof detail === "string" && detail ? detail : "We couldn't save your setup. Please try again.");
    }
  });

  syncChecked();
  syncMode();
  syncDestination();
  syncProgress("role");
})();
