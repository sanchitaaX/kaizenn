// Dashboard: commit filtering, sync, scan, pull request, side tabs and copy helpers.
//
// API calls (backend is built in parallel, every call fails softly with a toast):
//   POST /api/commits/sync                -> { added, ... }
//   POST /api/commits/{sha}/scan          -> 202 scan view { status, steps, error, report }
//   GET  /api/commits/{sha}/scan          -> scan view (polled while status is "running")
//   POST /api/commits/{sha}/pull-request  -> { number, url, status }

(function () {
  "use strict";

  const root = document.getElementById("dashboard");
  if (!root) return;

  const sha = root.dataset.sha;
  const toast = (msg) => window.SecureFix && window.SecureFix.toast(msg);
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const wait = (ms) => new Promise((r) => setTimeout(r, reducedMotion ? 0 : ms));

  async function api(path, options) {
    if (window.SecureFix && window.SecureFix.api) return window.SecureFix.api(path, options);
    const res = await fetch(path, { credentials: "same-origin", ...options });
    if (!res.ok) throw new Error(`Request failed: ${res.status}`);
    return res.status === 204 ? null : res.json();
  }

  // The backend answers errors with {"detail": "<plain sentence>"}; show it when there is one.
  function detailOf(err, fallback) {
    try {
      const data = JSON.parse(err && err.message);
      if (data && typeof data.detail === "string") return data.detail;
    } catch (e) { /* not JSON */ }
    return fallback;
  }

  function setBusy(btn, busy, label) {
    if (!btn) return;
    const text = btn.querySelector(".btn-label");
    if (busy) {
      btn.dataset.label = text ? text.textContent : "";
      btn.setAttribute("aria-busy", "true");
      btn.disabled = true;
      if (text && label) text.textContent = label;
    } else {
      btn.removeAttribute("aria-busy");
      btn.disabled = false;
      if (text && btn.dataset.label) text.textContent = btn.dataset.label;
    }
  }

  const store = {
    get(key) {
      try { return JSON.parse(sessionStorage.getItem(key)); } catch (e) { return null; }
    },
    set(key, value) {
      try { sessionStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage blocked */ }
    },
  };

  /* ---------- Commit list: search and state filter ---------- */

  const list = document.getElementById("commit-list");
  const search = document.getElementById("commit-search");
  const emptyMsg = document.getElementById("list-empty");
  const chips = Array.from(document.querySelectorAll(".state-filter .chip"));
  const FILTER_KEY = "securefix.dashboard.filter";
  let activeState = "all";

  function applyFilter() {
    if (!list) return;
    const q = (search ? search.value : "").trim().toLowerCase();
    let shown = 0;
    list.querySelectorAll("li").forEach((li) => {
      const okState = activeState === "all" || li.dataset.state === activeState;
      const okText = !q || (li.dataset.search || "").includes(q);
      const show = okState && okText;
      li.hidden = !show;
      if (show) shown += 1;
    });
    if (emptyMsg) emptyMsg.hidden = shown > 0;
    store.set(FILTER_KEY, { q: search ? search.value : "", state: activeState });
  }

  function setState(next) {
    activeState = next;
    chips.forEach((c) => c.setAttribute("aria-pressed", String(c.dataset.filter === next)));
    applyFilter();
  }

  chips.forEach((chip) => chip.addEventListener("click", () => setState(chip.dataset.filter)));
  if (search) search.addEventListener("input", applyFilter);

  const saved = store.get(FILTER_KEY);
  if (saved) {
    if (search && typeof saved.q === "string") search.value = saved.q;
    const known = chips.some((c) => c.dataset.filter === saved.state);
    setState(known ? saved.state : "all");
  }

  // Keep the selected commit in view inside the scrolling list.
  const current = list && list.querySelector('[aria-current="true"]');
  if (current && current.closest("li") && !current.closest("li").hidden) {
    current.scrollIntoView({ block: "nearest" });
  }

  // "/" jumps to search.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    if (!search) return;
    e.preventDefault();
    search.focus();
  });

  /* ---------- Sync commits ---------- */

  const syncBtn = document.getElementById("sync-btn");
  const syncHint = document.getElementById("sync-hint");
  if (syncBtn) {
    syncBtn.addEventListener("click", async () => {
      setBusy(syncBtn, true, "Syncing commits");
      try {
        const res = await api("/api/commits/sync", { method: "POST" });
        const added = res && typeof res.added === "number" ? res.added : null;
        if (syncHint) syncHint.textContent = "Last synced just now";
        if (added === 0) {
          toast("You are up to date. No new commits.");
          setBusy(syncBtn, false);
          return;
        }
        toast(added ? `Synced ${added} new ${added === 1 ? "commit" : "commits"}` : "Commits synced");
        await wait(700);
        window.location.reload();
      } catch (err) {
        toast("Could not sync commits. Try again in a moment.");
        setBusy(syncBtn, false);
      }
    });
  }

  /* ---------- Proof rail ---------- */

  const rail = document.getElementById("proof-rail");
  const ORDER = ["find", "hack", "fix", "prove"];
  const STATUS_WORDS = { idle: "Not run", running: "Running", attack: "Attack worked", pass: "Passed", fail: "Failed" };

  function railSnapshot() {
    if (!rail) return null;
    return {
      progress: rail.style.getPropertyValue("--progress"),
      steps: ORDER.map((key) => {
        const el = rail.querySelector(`[data-step="${key}"]`);
        return { key, status: el.dataset.status, note: el.querySelector(".proof-note").textContent };
      }),
    };
  }

  function setStep(key, status, note) {
    const el = rail && rail.querySelector(`[data-step="${key}"]`);
    if (!el) return;
    el.dataset.status = status;
    if (typeof note === "string") el.querySelector(".proof-note").textContent = note;
    const sr = el.querySelector(".proof-status");
    if (sr) sr.textContent = STATUS_WORDS[status] || status;
  }

  function setProgress(index) {
    if (rail) rail.style.setProperty("--progress", String(Math.max(0, index) / 3));
  }

  function restoreRail(snap) {
    if (!snap) return;
    snap.steps.forEach((s) => setStep(s.key, s.status, s.note));
    rail.style.setProperty("--progress", snap.progress || "0");
  }

  /* ---------- Scan (start, then poll until the four lights settle) ---------- */

  const scanButtons = Array.from(document.querySelectorAll('[data-action="scan"]'));
  const POLL_MS = 700;
  const POLL_LIMIT = 15 * 60 * 1000;
  let scanning = false;

  function showSteps(steps) {
    let last = 0;
    ORDER.forEach((key, i) => {
      const s = steps && steps[key];
      if (!s) return;
      const status = s.status || "idle";
      setStep(key, status, s.note || (status === "running" ? "Working" : status === "idle" ? "Waiting" : ""));
      if (status !== "idle") last = i;
    });
    setProgress(last);
  }

  async function pollScan() {
    const started = Date.now();
    for (;;) {
      await new Promise((r) => setTimeout(r, POLL_MS));
      const view = await api(`/api/commits/${encodeURIComponent(sha)}/scan`);
      if (view && view.steps) showSteps(view.steps);
      if (!view || view.status !== "running") return view;
      if (Date.now() - started > POLL_LIMIT) throw new Error("Scan is taking too long");
    }
  }

  async function runScan(resume) {
    if (scanning || !sha) return;
    scanning = true;
    const snap = railSnapshot();
    scanButtons.forEach((b) => setBusy(b, true, "Scanning"));
    if (!resume) {
      ORDER.forEach((k) => setStep(k, "idle", "Waiting"));
      setStep("find", "running", "Starting the scan");
      setProgress(0);
    }

    try {
      let view = resume ? null : await api(`/api/commits/${encodeURIComponent(sha)}/scan`, { method: "POST" });
      if (view && view.steps) showSteps(view.steps);
      if (!view || view.status === "running") view = await pollScan();
      if (view && view.status === "error") {
        throw new Error(JSON.stringify({ detail: view.error || "The scan stopped. Try again in a moment." }));
      }
      toast("Scan finished. Loading the new report.");
      await wait(900);
      window.location.reload();
    } catch (err) {
      restoreRail(snap);
      scanButtons.forEach((b) => setBusy(b, false));
      scanning = false;
      toast(detailOf(err, "Could not finish the scan. Try again in a moment."));
    }
  }

  scanButtons.forEach((btn) => btn.addEventListener("click", () => runScan(false)));

  // A scan started earlier (or in another tab) is still running: keep showing its lights.
  if (root.dataset.scanStatus === "running") runScan(true);

  /* ---------- Open pull request ---------- */

  document.querySelectorAll('[data-action="pull-request"]').forEach((btn) => {
    btn.addEventListener("click", async () => {
      setBusy(btn, true, "Opening pull request");
      try {
        const res = await api(`/api/commits/${encodeURIComponent(sha)}/pull-request`, { method: "POST" });
        toast(res && res.number ? `Pull request #${res.number} opened` : "Pull request opened");
        if (res && res.url && res.url !== "#") window.open(res.url, "_blank", "noopener");
        await wait(700);
        window.location.reload();
      } catch (err) {
        toast(detailOf(err, "Could not open the pull request. Try again in a moment."));
        setBusy(btn, false);
      }
    });
  });

  /* ---------- Side pill tabs (report and learn) ---------- */

  const tabs = Array.from(document.querySelectorAll('.segmented [role="tab"]'));
  const TAB_KEY = "securefix.dashboard.tab";

  function selectTab(tab, focus) {
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      const panel = document.getElementById(t.getAttribute("aria-controls"));
      if (panel) panel.hidden = !on;
    });
    if (focus) tab.focus();
    try { localStorage.setItem(TAB_KEY, tab.id); } catch (e) { /* storage blocked */ }
  }

  if (tabs.length) {
    tabs.forEach((tab, i) => {
      tab.addEventListener("click", () => selectTab(tab, false));
      tab.addEventListener("keydown", (e) => {
        let next = null;
        if (e.key === "ArrowRight") next = tabs[(i + 1) % tabs.length];
        else if (e.key === "ArrowLeft") next = tabs[(i - 1 + tabs.length) % tabs.length];
        else if (e.key === "Home") next = tabs[0];
        else if (e.key === "End") next = tabs[tabs.length - 1];
        if (next) {
          e.preventDefault();
          selectTab(next, true);
        }
      });
    });
    let remembered = null;
    try { remembered = localStorage.getItem(TAB_KEY); } catch (e) { /* storage blocked */ }
    const start = tabs.find((t) => t.id === remembered);
    if (start) selectTab(start, false);
  }

  /* ---------- Copy helpers ---------- */

  async function copyText(text, done) {
    try {
      await navigator.clipboard.writeText(text);
      toast(done);
    } catch (err) {
      toast("Could not copy. Select the text and copy it by hand.");
    }
  }

  document.querySelectorAll('[data-action="copy-diff"]').forEach((btn) => {
    btn.addEventListener("click", () => {
      const diff = document.getElementById("fix-diff");
      if (!diff) return;
      const lines = Array.from(diff.querySelectorAll(".diff-line")).map((line) => {
        const clone = line.cloneNode(true);
        clone.querySelectorAll(".sr-only").forEach((n) => n.remove());
        return clone.textContent;
      });
      copyText(lines.join("\n") + "\n", "Fix copied");
    });
  });

  document.querySelectorAll('[data-action="copy-summary"]').forEach((btn) => {
    btn.addEventListener("click", () => {
      const title = document.getElementById("commit-title");
      const rows = Array.from(document.querySelectorAll(".summary > div")).map((row) => {
        const dt = row.querySelector("dt");
        const dd = row.querySelector("dd");
        return `${dt ? dt.textContent.trim() : ""}: ${dd ? dd.textContent.replace(/\s+/g, " ").trim() : ""}`;
      });
      const header = `SecureFix report for commit ${sha}${title ? `: ${title.textContent.trim()}` : ""}`;
      copyText([header, ...rows].join("\n"), "Summary copied");
    });
  });
})();
