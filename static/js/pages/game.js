// KAIZEN quests and rewards in the chat. Loaded only when the user switched on Quests & rewards on the Profile.
// Draws a level bar above the composer, "+N XP" toasts for new events, a quest checklist, reward cards with
// DEMO coupon codes, and the "Guess the bug" quiz after the user acts on a fix. The server owns every number.

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

    // The snippet as a small editor: file tab, line numbers, and the line the agent flagged marked.
    function codeView(q) {
      const first = q.first_line || 1;
      const rows = String(q.snippet || "").split("\n").map((text, i) => {
        const n = first + i;
        return el("div", "quiz-line" + (n === q.line ? " is-flagged" : ""), [el("span", "quiz-ln", String(n)), el("span", "quiz-src", text || " ")]);
      });
      const name = String(q.file || "").split("/").pop();
      return el("div", "quiz-code", [
        el("div", "quiz-tab", [el("span", "quiz-dots", [el("i"), el("i"), el("i")]), el("span", "quiz-file", name), el("span", "quiz-path", q.file)]),
        el("pre", "quiz-pre", el("code", null, rows)),
      ]);
    }

    function result(a) {
      if (a.correct) {
        return el("span", "quiz-result is-right", [el("strong", null, "Nice catch!"), " It is " + a.answer_label + ".",
          a.points ? el("span", "quiz-gain", "+" + a.points + " XP") : null]);
      }
      return el("span", "quiz-result is-wrong", [el("strong", null, "Not quite."), " It is " + a.answer_label + ".",
        a.points ? el("span", "quiz-gain", "+" + a.points + " XP for trying") : null]);
    }

    // "Guess the bug", asked once the user has acted on a fix (committed, opened a pull request or set it aside).
    // target: { run: id } for an agent run, or { vuln: id } for a dashboard problem. Never blocks the chat.
    function quiz(target) {
      if (!target || (state && state.prefs && state.prefs.quiz === false)) return;
      const url = target.vuln ? "/api/game/quiz/vuln/" + target.vuln : "/api/game/quiz/" + target.run;
      K.api(url).then((q) => {
        if (q.answered) return;
        const card = el("div", "quiz");
        const status = el("div", "quiz-status", [el("span", "quiz-pulse"), "Bonus round: one guess"]);
        const skip = btn("Skip this one", "quiz-skip", () => {
          card.classList.add("is-skipped");
          status.replaceChildren("Skipped.");
          skip.remove();
        });
        const choices = el("div", "quiz-choices", q.choices.map((c, i) => {
          const b = btn([el("span", "quiz-letter", "ABCD"[i]), el("span", "quiz-label", c.label), el("span", "quiz-mark")], "quiz-choice", async () => {
            choices.querySelectorAll("button").forEach((x) => { x.disabled = true; });
            b.classList.add("is-picked");
            skip.remove();
            try {
              const a = await K.api(url, { method: "POST", body: { choice: c.key } });
              choices.querySelectorAll("button").forEach((x) => {
                if (x.dataset.key === a.answer) x.classList.add("is-right");
                else if (x === b) x.classList.add("is-wrong");
                else x.classList.add("is-out");
              });
              card.classList.add(a.correct ? "is-won" : "is-lost");
              status.replaceChildren(result(a));
            } catch (err) {
              status.replaceChildren(err.message || "Could not check that answer.");
            }
            refresh();
          });
          b.dataset.key = c.key;
          return b;
        }));
        card.append(
          el("div", "quiz-head", [
            el("span", "quiz-icon", "?"),
            el("div", "quiz-titles", [el("p", "quiz-title", "Guess the bug"),
              el("p", "quiz-sub", "This is the code before the fix. What kind of bug was it?")]),
            el("span", "quiz-xp", "+30 XP"),
          ]),
          codeView(q),
          choices,
          el("div", "quiz-foot", [status, skip]),
        );
        post(card);
      }).catch(() => { /* no quiz for this one */ });
    }

    return { refresh, quiz };
  }

  window.KaizenGame = { init };
})();
