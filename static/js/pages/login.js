// Sign-in: a busy state on the button and a toast when GitHub sends the user back with an error.

(function () {
  const sf = window.Kaizen;

  const signin = document.getElementById("signin");
  if (signin) {
    const label = signin.querySelector(".login-cta-label");
    const original = label ? label.textContent : "";
    signin.addEventListener("click", () => {
      signin.setAttribute("aria-busy", "true");
      if (label) label.textContent = "Opening GitHub";
    });
    // Returning with the browser's back button should show the normal button again.
    window.addEventListener("pageshow", () => {
      signin.removeAttribute("aria-busy");
      if (label) label.textContent = original;
    });
  }

  const root = document.querySelector(".login");
  const code = root ? root.dataset.error : "";
  if (code && sf) {
    const messages = {
      denied: "GitHub sign-in was cancelled. You can try again.",
      state: "That sign-in link expired. Please try again.",
      github: "Could not reach GitHub. Please try again.",
    };
    setTimeout(() => sf.toast(messages[code] || "Sign-in did not work. Please try again."), 200);
    try {
      const url = new URL(window.location.href);
      url.searchParams.delete("error");
      window.history.replaceState({}, "", url.pathname + url.search);
    } catch (_) { /* keep the URL as is */ }
  }
})();
