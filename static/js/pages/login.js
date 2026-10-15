// Login screen: plays the proof rail demo once, and handles the GitHub sign-in button.

(function () {
  const toast = (msg) => window.SecureFix && window.SecureFix.toast(msg);

  /* ---------- Sign-in button ---------- */

  const signin = document.getElementById("signin");
  if (signin) {
    const label = signin.querySelector(".login-cta-label");
    const original = label ? label.textContent : "";
    signin.addEventListener("click", () => {
      signin.setAttribute("aria-busy", "true");
      if (label) label.textContent = "Opening GitHub…";
    });
    // Coming back with the browser back button should show the normal button again.
    window.addEventListener("pageshow", () => {
      signin.removeAttribute("aria-busy");
      if (label) label.textContent = original;
    });
  }

  // The OAuth callback can send people back here with ?error=...
  const params = new URLSearchParams(window.location.search);
  if (params.has("error")) {
    const reason = params.get("error");
    const message =
      reason === "access_denied" || reason === "denied"
        ? "GitHub sign-in was cancelled. You can try again any time."
        : reason === "state"
          ? "Your sign-in link expired. Please sign in again."
          : "GitHub sign-in didn't finish. Please try again.";
    // Let app.js load the toast helper first.
    window.addEventListener("load", () => toast(message));
  }

  /* ---------- Proof rail demo ---------- */

  const card = document.querySelector("[data-demo]");
  if (!card) return;

  const rail = card.querySelector("[data-demo-rail]");
  const badge = card.querySelector("[data-demo-badge]");
  const status = card.querySelector("[data-demo-status]");
  const replay = card.querySelector("[data-demo-replay]");
  const steps = {};
  card.querySelectorAll("[data-step]").forEach((el) => {
    steps[el.dataset.step] = { el, note: el.querySelector("[data-note]") };
  });
  // Dark task list that mirrors the rail: one row per step, with a check circle.
  const tasks = {};
  card.querySelectorAll("[data-task]").forEach((el) => {
    tasks[el.dataset.task] = el;
  });
  const doneCount = card.querySelector("[data-demo-done]");
  function countDone() {
    if (!doneCount) return;
    doneCount.textContent = String(
      Object.values(tasks).filter((el) => el.dataset.status !== "idle" && el.dataset.status !== "running").length
    );
  }
  const reveals = {};
  card.querySelectorAll("[data-reveal]").forEach((el) => {
    reveals[el.dataset.reveal] = el;
  });

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Each step: what it shows while running, then the result.
  const script = [
    { step: "find", running: "Scanning code", status: "pass", note: "Bug found in admin.py", progress: 0,
      say: "Find: bug found in the admin search." },
    { step: "hack", running: "Trying an attack", status: "attack", note: "Attack worked", progress: 0.34,
      say: "Hack: the attack worked and returned all 50 students." },
    { step: "fix", running: "Writing a patch", status: "pass", note: "Patch ready", progress: 0.67,
      say: "Fix: patch written." },
    { step: "prove", running: "Re-running checks", status: "pass", note: "Blocked, tests pass", progress: 1,
      say: "Prove: attack blocked and all 12 tests pass. Commit marked fixed." },
  ];

  const RUN_MS = reduceMotion ? 0 : 1100;
  const GAP_MS = reduceMotion ? 0 : 450;
  let timers = [];
  let playing = false;

  function wait(ms) {
    return new Promise((resolve) => timers.push(setTimeout(resolve, ms)));
  }

  function setBadge(kind, text) {
    if (!badge) return;
    badge.className = "badge badge-" + kind;
    badge.textContent = text;
  }

  function say(text) {
    if (status) status.textContent = text;
  }

  function reset() {
    timers.forEach(clearTimeout);
    timers = [];
    Object.values(steps).forEach(({ el, note }) => {
      el.dataset.status = "idle";
      if (note) note.textContent = "Waiting";
    });
    Object.values(tasks).forEach((el) => { el.dataset.status = "idle"; });
    countDone();
    Object.values(reveals).forEach((el) => el.classList.remove("is-shown"));
    if (rail) rail.style.setProperty("--progress", "0");
    setBadge("new", "New");
  }

  async function play() {
    if (playing) return;
    playing = true;
    if (replay) replay.disabled = true;
    reset();
    say("Demo started.");

    try {
      await wait(reduceMotion ? 0 : 500);
      setBadge("scanning", "Scanning");

      for (const item of script) {
        const s = steps[item.step];
        if (!s) continue;
        if (rail) rail.style.setProperty("--progress", String(item.progress));
        s.el.dataset.status = "running";
        if (tasks[item.step]) tasks[item.step].dataset.status = "running";
        if (s.note) s.note.textContent = item.running;
        await wait(RUN_MS);

        s.el.dataset.status = item.status;
        if (tasks[item.step]) tasks[item.step].dataset.status = item.status;
        countDone();
        if (s.note) s.note.textContent = item.note;
        if (reveals[item.step]) reveals[item.step].classList.add("is-shown");
        say(item.say);
        await wait(GAP_MS);
      }

      setBadge("fixed", "Fixed");
    } finally {
      playing = false;
      if (replay) replay.disabled = false;
    }
  }

  if (replay) replay.addEventListener("click", play);

  // Play once, when the card first comes into view.
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          io.disconnect();
          play();
        }
      },
      { threshold: 0.35 }
    );
    io.observe(card);
  } else {
    play();
  }
})();
