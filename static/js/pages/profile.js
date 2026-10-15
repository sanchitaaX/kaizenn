// Profile: save the answer modes and the role as soon as they change.

(function () {
  const sf = window.SecureFix;
  const toast = (msg) => sf && sf.toast(msg);
  const switches = Array.from(document.querySelectorAll("input[data-mode]"));
  const roleButtons = Array.from(document.querySelectorAll("#role-picker button[data-role]"));
  let busy = false;

  function setBusy(on) {
    busy = on;
    switches.forEach((s) => { s.disabled = on; });
    roleButtons.forEach((b) => { b.disabled = on; });
  }

  function applyModes(data) {
    const map = { learn_mode: "learn-mode", report_mode: "report-mode" };
    Object.keys(map).forEach((key) => {
      if (typeof data[key] === "boolean") document.getElementById(map[key]).checked = data[key];
    });
  }

  function applyRole(role) {
    roleButtons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.role === role)));
  }

  switches.forEach((input) => {
    input.addEventListener("change", async () => {
      if (busy) { input.checked = !input.checked; return; }
      const body = {};
      switches.forEach((s) => { body[s.dataset.mode] = s.checked; });
      if (!body.learn_mode && !body.report_mode) {
        input.checked = true;
        toast("Keep at least one mode on");
        return;
      }
      setBusy(true);
      try {
        const data = await sf.api("/api/profile/modes", { method: "POST", body });
        applyModes(data);
        toast("Saved");
      } catch (err) {
        input.checked = !input.checked;
        toast(err.message || "Could not save. Try again.");
      } finally {
        setBusy(false);
      }
    });
  });

  roleButtons.forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (busy || btn.getAttribute("aria-pressed") === "true") return;
      const previous = roleButtons.find((b) => b.getAttribute("aria-pressed") === "true");
      applyRole(btn.dataset.role);
      setBusy(true);
      try {
        const data = await sf.api("/api/profile/role", { method: "POST", body: { role: btn.dataset.role } });
        applyModes(data);
        toast("Saved");
      } catch (err) {
        applyRole(previous ? previous.dataset.role : "student");
        toast(err.message || "Could not save. Try again.");
      } finally {
        setBusy(false);
      }
    });
  });
})();
