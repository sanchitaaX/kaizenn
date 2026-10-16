// KAIZEN quests and rewards in the chat. Loaded only when the user switched on Quests & rewards on the Profile.
// Draws a level bar above the composer, "+N XP" toasts for new events, a quest checklist, reward cards with
// DEMO coupon codes, and the "Guess the bug" quiz while the agent works. The server owns every number.

(function () {
  "use strict";

  const K = window.Kaizen;
  if (!K) return;

  function el(tag, cls, kids) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    [].concat(kids == null ? [] : kids).forEach((k) => { if (k != null && k !== false) n.append(k); });
    return n;
  }

  function btn(label, cls, onClick) {
    const b = el("button", cls, label);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  function init(opts) {
    const bar = opts.bar;
    const post = opts.post;
    let state = null;
    let seen = null; // id of the newest event already shown

    function questCard() {
      return el("div", "game-card", [
        el("p", "game-card-title", "Quests"),
        el("ul", "game-quests", state.quests.map((q) => el("li", q.done ? "is-done" : null, [
          el("span", "game-quest-name", q.name),
          el("span", "game-quest-goal", q.goal),
          el("span", "game-quest-progress", q.done ? "Done · +" + q.bonus + " XP" : q.progress + " / " + q.target),
        ]))),
      ]);
    }

    function rewardCard(r) {
      return el("div", "game-card game-reward", [
        el("p", "game-card-title", "Level " + r.level + " reward unlocked"),
        el("p", null, r.discount + "% off on " + r.platform),
        el("code", "game-code", r.code),
        el("span", "game-demo", r.label),
        btn("Copy code", "btn btn-secondary btn-sm", async () => {
          try { await navigator.clipboard.writeText(r.code); K.toast("Code copied"); }
          catch (_) { K.toast("Select the code to copy it"); }
        }),
      ]);
    }

    function renderBar() {
      if (!bar) return;
      if (!state || !state.enabled) { bar.hidden = true; return; }
      const lv = state.level;
      const part = lv.next == null ? 1 : (state.xp - lv.min) / (lv.next - lv.min);
      const pct = Math.round(Math.min(1, Math.max(0, part)) * 100);
      const fill = el("span", "game-fill");
      fill.style.width = pct + "%";
      const track = el("span", "game-track", fill);
      track.setAttribute("role", "progressbar");
      track.setAttribute("aria-label", "Progress to the next level");
      track.setAttribute("aria-valuemin", "0");
      track.setAttribute("aria-valuemax", "100");
      track.setAttribute("aria-valuenow", String(pct));
      const done = state.quests.filter((q) => q.done).length;
      bar.replaceChildren(
        el("span", "game-level", "Lv " + lv.n + " · " + lv.name),
        track,
        el("span", "game-xp", lv.next == null ? state.xp + " XP · top level" : state.xp + " / " + lv.next + " XP"),
        btn("Quests " + done + "/" + state.quests.length, "chip game-quests-btn", () => post(questCard())));
      bar.hidden = false;
    }

    async function refresh() {
      let next;
      try { next = await K.api("/api/game"); } catch (_) { return state; }
      const prev = state;
      state = next;
      renderBar();
      if (!next.enabled) return state;
      const newest = next.recent.length ? next.recent[0].id : 0;
      if (prev && prev.enabled && seen != null) {
        // One toast per kind of event, so committing 20 fixes at once says "+200 XP" once instead of 20 times.
        const groups = new Map();
        next.recent.filter((e) => e.id > seen).reverse().forEach((e) => {
          const g = groups.get(e.reason) || { reason: e.reason, points: 0, n: 0 };
          g.points += e.points;
          g.n += 1;
          groups.set(e.reason, g);
        });
        if (next.prefs && next.prefs.toasts === false) groups.clear();  // "XP pop-ups" off on the Quests page
        [...groups.values()].forEach((g, i) => setTimeout(() =>
          K.toast("+" + g.points + " XP · " + g.reason + (g.n > 1 ? " ×" + g.n : "")), i * 2800));
        const had = new Set(prev.rewards.map((r) => r.level));
        next.rewards.filter((r) => !had.has(r.level)).forEach((r) => post(rewardCard(r)));
      }
      seen = Math.max(seen || 0, newest);
      return state;
    }

    // Resolves when the user answered or skipped, so the chat can then show the agent's result.
    // null when "Guess the bug" is switched off on the Quests page.
    function quiz(runId, stepsEl) {
      if (state && state.prefs && state.prefs.quiz === false) return null;
      return new Promise((resolve) => {
        let finished = false;
        const finish = () => {
          if (finished) return;
          finished = true;
          stepsEl.classList.remove("is-spoiler");
          resolve();
          refresh();
        };
        K.api("/api/game/quiz/" + runId).then((q) => {
          if (q.answered) { finish(); return; }
          stepsEl.classList.add("is-spoiler");
          const status = el("p", "game-quiz-status", "Pick one before the agent shows its answer.");
          const skip = btn("Skip", "btn btn-ghost btn-sm", finish);
          const choices = el("div", "game-choices", q.choices.map((c) => btn(c.label, "chip", async (ev) => {
            const picked = ev.currentTarget;
            choices.querySelectorAll("button").forEach((x) => { x.disabled = true; });
            skip.remove();
            try {
              const a = await K.api("/api/game/quiz/" + runId, { method: "POST", body: { choice: c.key } });
              picked.classList.add(a.correct ? "is-right" : "is-wrong");
              status.textContent = a.correct ? "Right: " + a.answer_label + "." : "Not quite. It is " + a.answer_label + ".";
            } catch (err) {
              status.textContent = err.message || "Could not check that answer.";
            }
            finish();
          })));
          post(el("div", "game-card game-quiz", [
            el("p", "game-card-title", "Guess the bug · +30 XP"),
            el("p", "game-quiz-where", q.file + ":" + q.line),
            el("pre", "game-snippet", el("code", null, q.snippet)),
            choices, status, skip,
          ]));
        }).catch(finish);
      });
    }

    return { refresh, quiz };
  }

  window.KaizenGame = { init };
})();
