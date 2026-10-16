// Quests & rewards page: the on/off switch, the options (quiz, XP pop-ups), copying a coupon code and starting over.
// The page itself is drawn by the server, so after a change that moves numbers it simply reloads.

(function () {
  "use strict";

  const K = window.Kaizen;
  if (!K) return;

  async function setEnabled(on, control) {
    if (control) control.disabled = true;
    try {
      await K.api("/api/profile/modes", { method: "POST", body: { game_mode: on } });
      location.reload();
    } catch (err) {
      K.toast(err.message || "That could not be saved.");
      if (control) {
        control.disabled = false;
        if (control.type === "checkbox") control.checked = !on;
      }
    }
  }

  const master = document.getElementById("qx-enabled");
  if (master) master.addEventListener("change", () => setEnabled(master.checked, master));
  const turnOn = document.getElementById("qx-turn-on");
  if (turnOn) turnOn.addEventListener("click", () => setEnabled(true, turnOn));

  document.querySelectorAll("input[data-pref]").forEach((input) => {
    input.addEventListener("change", async () => {
      input.disabled = true;
      try {
        await K.api("/api/game/prefs", { method: "POST", body: { [input.dataset.pref]: input.checked } });
        K.toast("Saved");
      } catch (err) {
        input.checked = !input.checked;
        K.toast(err.message || "That could not be saved.");
      } finally {
        input.disabled = false;
      }
    });
  });

  document.querySelectorAll("button[data-copy]").forEach((b) => {
    b.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(b.dataset.copy); K.toast("Code copied"); }
      catch (_) { K.toast("Select the code to copy it"); }
    });
  });

  const reset = document.getElementById("qx-reset");
  if (reset) reset.addEventListener("click", async () => {
    if (!window.confirm("Reset your XP, quests and coupons to zero? This cannot be undone.")) return;
    reset.disabled = true;
    try {
      const out = await K.api("/api/game/reset", { method: "POST" });
      K.toast(out.message);
      location.reload();
    } catch (err) {
      K.toast(err.message || "Progress could not be reset.");
      reset.disabled = false;
    }
  });
})();
