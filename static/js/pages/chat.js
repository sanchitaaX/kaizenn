// Chat: pick a repository, pick a commit, optionally add a brief, then watch the check and talk about the result.
// Or check many repositories at once: tick several (or Select all) in the repository menu, or type any public
// repository as owner/name or a GitHub link. That check reads GitHub code scanning (POST /api/fleet/check) and
// answers with one card per repository, riskiest first.
// Picking (or typing) one repository runs the security repair agent (POST /api/agent/runs): it finds a real bug,
// writes a minimal patch, proves it with an exploit test and the repository's own tests, and returns a verdict
// with downloadable evidence. /chat?run=<id> reopens a run.
// Everything is a message in one thread.

(function () {
  "use strict";

  const SF = window.Kaizen;
  const root = document.getElementById("chat");
  if (!root || !SF) return;

  const $ = (id) => document.getElementById(id);
  const thread = $("thread");
  const scroller = $("thread-scroll");
  const sidebar = $("sidebar");
  const historyEl = $("history");
  const form = $("composer");
  const box = $("composer-box");
  const input = $("input");
  const sendBtn = $("send");
  const attachBtn = $("attach");
  const fileInput = $("file");
  const attachments = $("attachments");
  const toggleBtn = document.querySelector("[data-toggle-sidebar]");
  const repoChip = $("repo-chip");
  const repoChipLabel = $("repo-chip-label");
  const modeChip = $("mode-chip");

  const FIRST_NAME = (root.dataset.name || "").trim().split(/\s+/)[0] || "";
  const MODES = { learn: root.dataset.learn === "1", report: root.dataset.report === "1" };
  const BRIEF_LIMIT = 20000;
  const FILE_LIMIT = 2 * 1024 * 1024;
  const FILE_TYPES = ["txt", "md", "pdf", "docx"];
  const COMMITS_SHOWN = 5;
  const REPOS_SCROLL_AFTER = 5;

  // ---------------------------------------------------------------- state

  const S = {
    run: 0,            // bumped whenever the thread is reset, so late answers from old work are ignored
    phase: "idle",     // repo | commit | brief | running | done | failed
    repo: null,
    commit: null,
    file: null,        // {name, text} for an attached brief document
    fileMsg: null,
    analysisId: null,
    data: null,        // the last analysis loaded or finished
    steps: {},         // step key -> element
    pollTimer: null,
    asked: new Set(),  // follow-up questions already asked, so quick replies do not repeat
    history: [],
    historyLoaded: false,
    historyFailed: false,
    repos: null,       // every repository the user can pick (loaded once)
    multi: new Set(),  // repositories ticked in the menu for a multi-repository check
  };
  let composerMode = "off"; // off | brief | ask
  let GAME = null; // quests and rewards (static/js/pages/game.js), only when switched on in the Profile

  // ---------------------------------------------------------------- tiny DOM helpers

  const ICONS = {
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    alert: '<path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4.5"/><path d="M12 17h.01"/>',
    spark: '<path d="M12 3.5 13.9 9l5.6 1.9-5.6 1.9L12 18.5l-1.9-5.7L4.5 10.9 10.1 9z"/><path d="M18.5 3.5v3"/><path d="M17 5h3"/>',
    repo: '<path d="M5 5.5A2.5 2.5 0 0 1 7.5 3H19v15H7.5A2.5 2.5 0 0 0 5 20.5z"/><path d="M5 20.5A2.5 2.5 0 0 0 7.5 21H19v-3"/>',
    paperclip: '<path d="m20 11-8.2 8.2a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4L15 6.7"/>',
    book: '<path d="M4 5.5C4 4.7 4.7 4 5.5 4H11v16H5.5A1.5 1.5 0 0 1 4 18.5z"/><path d="M20 5.5c0-.8-.7-1.5-1.5-1.5H13v16h5.5a1.5 1.5 0 0 0 1.5-1.5z"/>',
    play: '<circle cx="12" cy="12" r="9"/><path d="m10.5 8.8 4.5 3.2-4.5 3.2z"/>',
    refresh: '<path d="M20 11a8 8 0 0 0-14.3-4.5L4 8.5"/><path d="M4 4v4.5h4.5"/><path d="M4 13a8 8 0 0 0 14.3 4.5L20 15.5"/><path d="M20 20v-4.5h-4.5"/>',
    external: '<path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
    x: '<path d="M6 6l12 12"/><path d="M18 6 6 18"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    branch: '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="8" r="2"/><path d="M6 7v10"/><path d="M18 10c0 4-6 3-12 7"/>',
  };

  function svg(name, size) {
    const t = document.createElement("template");
    t.innerHTML = '<svg class="icon" width="' + (size || 16) + '" height="' + (size || 16) +
      '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" ' +
      'stroke-linejoin="round" aria-hidden="true" focusable="false">' + (ICONS[name] || "") + "</svg>";
    return t.content.firstElementChild;
  }

  // h("div", "class names", [children]) - strings become text, never HTML.
  function h(tag, cls, kids) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    [].concat(kids == null ? [] : kids).forEach((k) => { if (k != null && k !== false) n.append(k); });
    return n;
  }
  const p = (text, cls) => h("p", cls, text);

  function button(label, cls, onClick, iconName) {
    const b = h("button", cls);
    b.type = "button";
    if (iconName) b.append(svg(iconName, 14));
    b.append(label);
    if (onClick) b.addEventListener("click", onClick);
    return b;
  }

  function firstLine(text) {
    return String(text || "").split("\n")[0].trim();
  }

  function repoPath(fullName) {
    return String(fullName).split("/").map(encodeURIComponent).join("/");
  }

  function safeUrl(url) {
    return /^https?:\/\//i.test(url || "") ? url : null;
  }

  // A short, calm version of an error. Long or technical text is replaced by the fallback.
  function calm(err, fallback) {
    const m = err && err.message ? String(err.message).trim() : "";
    if (!m || m.length > 140 || /^<|^\{|Failed to fetch|NetworkError|Load failed/i.test(m)) return fallback;
    return m;
  }

  const STATE_LABELS = { new: "New", safe: "Safe", fixed: "Fixed", attention: "Needs attention" };
  function badge(state) {
    const key = STATE_LABELS[state] ? state : "new";
    const b = h("span", "badge badge-" + key);
    if (key === "safe" || key === "fixed") b.append(svg("check", 12));
    else if (key === "attention") b.append(svg("alert", 12));
    b.append(STATE_LABELS[key]);
    return b;
  }

  function avatar(person, size) {
    const who = (person && (person.name || person.login)) || "?";
    const initials = () => {
      const s = h("span", "avatar avatar-" + size, who.slice(0, 1).toUpperCase());
      s.setAttribute("aria-hidden", "true");
      return s;
    };
    if (!person || !person.avatar_url) return initials();
    const img = h("img", "avatar avatar-" + size);
    img.alt = "";
    img.loading = "lazy";
    img.src = person.avatar_url;
    img.addEventListener("error", () => img.replaceWith(initials()), { once: true });
    return img;
  }

  // ---------------------------------------------------------------- thread

  function scrollEnd(smooth) {
    SF.scrollToEnd(scroller, smooth !== false);
  }

  function scrollToNode(node) {
    const top = node.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop - 16;
    scroller.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
  }

  // New messages go above the quick replies, which always stay last.
  function place(node) {
    const q = thread.querySelector(".quick-wrap");
    if (q) thread.insertBefore(node, q);
    else thread.append(node);
  }

  function assistant(kids) {
    const m = h("div", "msg msg-assistant");
    const av = h("span", "msg-dot");
    av.setAttribute("aria-hidden", "true");
    m.append(av, h("div", "msg-body", kids));
    place(m);
    return m;
  }
  const bodyOf = (m) => m.querySelector(".msg-body");

  function userMsg(content) {
    const kids = typeof content === "string" ? h("div", "bubble-text", content) : content;
    const m = h("div", "msg msg-user", h("div", "msg-bubble", kids));
    place(m);
    scrollEnd();
    return m;
  }

  function commitBubble(c) {
    return [
      h("div", "bubble-text", firstLine(c.message) || "Commit"),
      h("div", "bubble-sub", [h("span", "mono", c.short_sha || String(c.sha || "").slice(0, 7))]),
    ];
  }

  function briefBubble(text, fileName) {
    const kids = [];
    if (fileName) kids.push(fileChip(fileName));
    if (text) {
      const t = text.trim();
      kids.push(h("div", "bubble-text", t.length > 400 ? t.slice(0, 400).trimEnd() + "..." : t));
    }
    return kids;
  }

  function typing() {
    const t = h("span", "typing", [h("span", "sr-only", "Thinking"), h("span"), h("span"), h("span")]);
    const m = assistant([t]);
    scrollEnd();
    return m;
  }

  function skeleton() {
    return h("div", "choice-skeleton", [h("span", "skeleton skeleton-line-lg"), h("span", "skeleton skeleton-line-sm")]);
  }

  function clearQuick() {
    thread.querySelectorAll(".quick-wrap").forEach((n) => n.remove());
  }

  // items: [{label, onClick, primary}]
  function setQuick(items) {
    clearQuick();
    const row = h("div", "quick-replies");
    items.forEach((it) => {
      row.append(button(it.label, "btn btn-sm " + (it.primary ? "btn-primary" : "btn-secondary"), it.onClick));
    });
    thread.append(h("div", "quick-wrap", row));
    scrollEnd();
  }

  function errorMsg(text, retry) {
    const m = assistant([p(text)]);
    if (retry) {
      bodyOf(m).append(button("Try again", "btn btn-secondary btn-sm", () => {
        m.remove();
        retry();
      }, "refresh"));
    }
    scrollEnd();
    return m;
  }

  // ---------------------------------------------------------------- composer

  function setComposer(mode, placeholder) {
    composerMode = mode;
    const on = mode === "brief" || mode === "ask" || mode === "repo";
    input.disabled = !on;
    input.maxLength = mode === "ask" ? 2000 : BRIEF_LIMIT;  // the server accepts 2,000 characters per question
    input.placeholder = placeholder || "";
    attachBtn.disabled = mode !== "brief";
    box.classList.toggle("is-off", !on);
    updateSend();
  }

  function updateSend() {
    const hasText = input.value.trim().length > 0;
    sendBtn.disabled = !(composerMode === "ask" || composerMode === "repo" ? hasText
      : composerMode === "brief" ? hasText || !!S.file : false);
  }

  function clearInput() {
    input.value = "";
    input.style.height = "auto";
    updateSend();
  }

  function submitComposer() {
    const text = input.value.trim();
    if (composerMode === "brief") {
      if (!text && !S.file) return;
      clearInput();
      startFromBrief(text);
    } else if (composerMode === "ask") {
      if (!text) return;
      clearInput();
      askFollowUp(text);
    } else if (composerMode === "repo") {
      const names = parseRepos(text);
      clearInput();
      if (names.length) checkRepos(names);
      else askAnything(text);  // not a repository: an ordinary message to the assistant
    }
  }

  // "octocat/hello-world, https://github.com/a/b" -> ["octocat/hello-world", "a/b"]
  function parseRepos(text) {
    const out = [];
    String(text || "").split(/[\s,;]+/).forEach((raw) => {
      const t = raw.replace(/^https?:\/\//i, "").replace(/^(www\.)?github\.com\//i, "").replace(/\.git$/i, "")
        .replace(/\/+$/, "");
      const m = t.match(/^([A-Za-z0-9_.-]{1,100})\/([A-Za-z0-9_.-]{1,100})(\/.*)?$/);
      if (m) out.push(m[1] + "/" + m[2]);
    });
    return [...new Set(out)];
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); submitComposer(); });
  input.addEventListener("input", updateSend);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      submitComposer();
    }
  });

  // ---------------------------------------------------------------- history sidebar

  function renderHistory() {
    if (!S.historyLoaded) return;
    historyEl.replaceChildren();
    if (S.historyFailed && !S.history.length) {
      historyEl.append(h("p", "history-empty", "I could not load your earlier checks."));
      return;
    }
    if (!S.history.length) {
      historyEl.append(h("p", "history-empty", "Your checks will show up here."));
      return;
    }
    S.history.forEach((a) => {
      const link = h("a", "history-item" + (a.id === S.analysisId ? " is-active" : ""));
      link.href = "/chat/" + a.id;
      link.title = a.created_label || "";
      if (a.id === S.analysisId) link.setAttribute("aria-current", "page");
      link.append(h("span", "history-item-text", [
        h("span", "history-title", a.title || a.short_sha),
        h("span", "history-sub", a.repo),
      ]));
      if (a.state && a.state !== "new") {
        const mark = h("span", "history-mark" + (a.state === "attention" ? " is-alert" : ""),
          [svg(a.state === "attention" ? "alert" : "check", 16), h("span", "sr-only", STATE_LABELS[a.state] || "")]);
        link.append(mark);
      }
      link.addEventListener("click", (e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
        e.preventDefault();
        sidebar.classList.remove("is-open");
        syncToggle();
        if (a.id !== S.analysisId) loadAnalysis(a.id, { push: true });
      });
      historyEl.append(link);
    });
  }

  async function refreshHistory() {
    try {
      const data = await SF.api("/api/analyses");
      S.history = data.analyses || [];
      S.historyFailed = false;
    } catch (_) {
      S.historyFailed = true;
    }
    S.historyLoaded = true;
    renderHistory();
  }

  function syncToggle() {
    if (toggleBtn) toggleBtn.setAttribute("aria-expanded", String(sidebar.classList.contains("is-open")));
  }
  if (toggleBtn) toggleBtn.addEventListener("click", () => setTimeout(syncToggle, 0));
  document.addEventListener("click", () => setTimeout(syncToggle, 0));

  // ---------------------------------------------------------------- reset

  function resetThread() {
    S.run += 1;
    clearTimeout(S.pollTimer);
    S.phase = "idle";
    S.repo = null;
    S.commit = null;
    S.analysisId = null;
    S.steps = {};
    S.asked = new Set();
    S.fileMsg = null;
    S.file = null;
    S.data = null;
    S.talk = [];           // the free conversation in this thread, sent back as context
    S.agentRun = null;     // the agent run on screen, so questions can be about it
    renderAttachment();
    clearInput();
    thread.replaceChildren();
    scroller.scrollTop = 0;
  }

  // ---------------------------------------------------------------- new analysis: repo, commit, brief

  // The conversation on screen is remembered for this browser tab (sessionStorage), so opening the floating chat
  // on another page or going full screen brings back the same thread instead of a new one. New analysis and
  // Start over forget it. What is kept: the agent run, analysis or multi-repository check on screen, and the
  // free messages typed in this thread.
  const THREAD_KEY = "sf-chat-thread";
  function savedThread() {
    try { return JSON.parse(sessionStorage.getItem(THREAD_KEY) || "null"); } catch (_) { return null; }
  }
  function remember(patch, fresh) {
    try { sessionStorage.setItem(THREAD_KEY, JSON.stringify(Object.assign({}, fresh ? {} : savedThread() || {}, patch))); }
    catch (_) { /* storage off: the chat still works, it just starts fresh next time */ }
  }
  function forget() {
    try { sessionStorage.removeItem(THREAD_KEY); } catch (_) { /* ignore */ }
  }
  function replayTalk(talk) {
    (talk || []).forEach((m) => {
      if (m.role === "user") userMsg(m.text);
      else assistant(String(m.text || "").split(/\n{2,}/).map((para) => p(para, "msg-reply")));
    });
    S.talk = (talk || []).slice();
    scrollEnd();
  }

  function startNew(push) {
    if (push) forget();
    resetThread();
    if (push && location.pathname !== "/chat") history.pushState({}, "", "/chat");
    renderHistory();
    S.phase = "repo";
    setRepoChip(null);
    S.multi = new Set();
    setComposer("repo", "Pick repositories below, type any public one (owner/name or a GitHub link), or ask me anything");
    return showWelcome(S.run);
  }

  function focusFirstChoice(list) {
    const first = list.querySelector(".choice");
    if (first) first.focus({ preventScroll: true });
  }

  async function getRepos() {
    if (S.repos) return S.repos;
    const data = await SF.api("/api/repos");
    S.repos = data.repos || [];
    return S.repos;
  }

  // Repositories checked most recently come first, then the rest in the order GitHub gives them.
  function topRepos(repos, n) {
    const order = [];
    S.history.forEach((a) => { if (a.repo && !order.includes(a.repo)) order.push(a.repo); });
    const byName = new Map(repos.map((r) => [r.full_name, r]));
    const top = order.map((name) => byName.get(name)).filter(Boolean);
    repos.forEach((r) => { if (!top.includes(r)) top.push(r); });
    return top.slice(0, n);
  }

  function lastChecked(name) {
    const a = S.history.find((x) => x.repo === name);
    return a ? "Checked " + (a.created_label || "recently") : "Not checked yet";
  }

  async function showWelcome(run) {
    const wrap = h("section", "welcome", [
      h("h1", "welcome-title", "What should I check today" + (FIRST_NAME ? ", " + FIRST_NAME : "") + "?"),
      p("Pick a repository to check one commit, tick several (or all) to check them together, or type any public repository below.", "welcome-sub"),
    ]);
    const grid = h("div", "repo-grid", [skeleton()]);
    wrap.append(grid);
    thread.append(wrap);
    try {
      if (!S.historyLoaded) await refreshHistory();
      const repos = await getRepos();
      if (run !== S.run) return;
      grid.replaceChildren();
      if (!repos.length) {
        grid.append(p("I do not see any repositories on this account yet. Once you have one, come back and I will check it.", "muted"));
        return;
      }
      topRepos(repos, 3).forEach((r) => {
        const card = h("button", "repo-card", [
          h("span", "repo-card-icon", svg("repo", 16)),
          h("span", "repo-card-name", r.full_name),
          h("span", "repo-card-meta", [svg("branch", 12), r.default_branch || "main"]),
          h("span", "repo-card-last", lastChecked(r.full_name)),
        ]);
        card.type = "button";
        card.addEventListener("click", () => chooseRepo(r));
        grid.append(card);
      });
      const all = h("button", "repo-all", [svg("search", 14), "All repositories", h("span", "repo-all-count", String(repos.length)), svg("down", 14)]);
      all.type = "button";
      all.setAttribute("aria-haspopup", "listbox");
      all.setAttribute("aria-expanded", "false");
      all.addEventListener("click", () => openRepoMenu(all));
      wrap.append(h("div", "repo-all-row", all));
    } catch (err) {
      if (run !== S.run) return;
      grid.replaceChildren(p(calm(err, "I could not load your repositories just now."), "muted"),
        button("Try again", "btn btn-secondary btn-sm", () => { wrap.remove(); showWelcome(run); }, "refresh"));
    }
  }

  function setRepoChip(repo) {
    repoChipLabel.textContent = repo ? repo.full_name : "Choose a repository";
    repoChip.classList.toggle("is-set", !!repo);
  }

  // Picking a repository from the welcome cards, the dropdown or the composer chip runs the agent.
  function chooseRepo(repo) {
    startAgent(repo.full_name, () => chooseCommit(repo));
  }

  // The older single-commit check (also the fallback for accounts without GitHub).
  function chooseCommit(repo) {
    closeRepoMenu(true);
    if (S.phase !== "repo") {
      resetThread();
      if (location.pathname !== "/chat") history.pushState({}, "", "/chat");
      renderHistory();
    }
    thread.querySelector(".welcome")?.remove();
    S.phase = "commit";
    S.repo = repo;
    setRepoChip(repo);
    setComposer("off", "Pick a commit above");
    userMsg("Check " + repo.full_name);
    askCommit();
  }

  // ---------------------------------------------------------------- repository dropdown

  let menu = null;
  let menuAnchor = null;

  function closeRepoMenu(silent) {
    if (!menu) return;
    menu.remove();
    menu = null;
    if (menuAnchor) {
      menuAnchor.setAttribute("aria-expanded", "false");
      if (!silent) menuAnchor.focus({ preventScroll: true });
    }
    menuAnchor = null;
  }

  async function openRepoMenu(anchor) {
    if (menu && menuAnchor === anchor) { closeRepoMenu(); return; }
    closeRepoMenu(true);
    menuAnchor = anchor;
    anchor.setAttribute("aria-expanded", "true");
    const search = h("input", "repo-menu-search");
    search.type = "search";
    search.placeholder = "Search repositories";
    search.setAttribute("aria-label", "Search repositories");
    const list = h("div", "repo-menu-list");
    list.setAttribute("role", "listbox");
    list.id = "repo-menu-list";
    search.setAttribute("aria-controls", list.id);
    const allBox = h("input");
    allBox.type = "checkbox";
    const allRow = h("label", "repo-select-all", [allBox, h("span", null, "Select all")]);
    const checkBtn = button("Check selected", "btn btn-primary btn-sm repo-check-btn", () => {
      const names = [...S.multi];
      if (names.length) checkRepos(names);
    });
    const foot = h("div", "repo-menu-foot", [h("span", "repo-menu-count"), checkBtn]);
    const syncFoot = () => {
      const n = S.multi.size;
      foot.hidden = n === 0;
      foot.firstChild.textContent = n === 1 ? "1 repository selected" : n + " repositories selected";
      checkBtn.lastChild.textContent = n === 1 ? "Check 1 repository" : "Check " + n + " repositories";
      allBox.checked = repos.length > 0 && repos.every((r) => S.multi.has(r.full_name));
      allBox.indeterminate = n > 0 && !allBox.checked;
    };
    allBox.addEventListener("change", () => {
      if (allBox.checked) repos.forEach((r) => S.multi.add(r.full_name));
      else S.multi.clear();
      render();
    });
    menu = h("div", "repo-menu", [h("div", "repo-menu-head", [svg("search", 14), search]), allRow, list, foot]);
    document.body.append(menu);
    position(menu, anchor);

    let repos = [];
    let active = 0;
    const matching = () => {
      const q = search.value.trim().toLowerCase();
      return repos.filter((r) => r.full_name.toLowerCase().includes(q));
    };
    const render = () => {
      const shown = matching();
      list.replaceChildren();
      const typed = parseRepos(search.value)[0];
      if (typed && !repos.some((r) => r.full_name.toLowerCase() === typed.toLowerCase())) {
        const pub = h("div", "repo-option repo-option-public", [svg("search", 14),
          h("span", "repo-option-name", "Check " + typed), h("span", "repo-option-branch", "public")]);
        pub.setAttribute("role", "option");
        pub.addEventListener("mousedown", (e) => e.preventDefault());
        pub.addEventListener("click", () => checkRepos([typed]));
        list.append(pub);
      } else if (!shown.length) {
        list.append(h("p", "repo-menu-empty", "No repository matches that. Type owner/name to check a public one."));
      }
      active = Math.min(active, Math.max(0, shown.length - 1));
      shown.forEach((r, i) => {
        const current = S.repo && S.repo.full_name === r.full_name;
        const tick = h("input", "repo-tick");
        tick.type = "checkbox";
        tick.checked = S.multi.has(r.full_name);
        tick.setAttribute("aria-label", "Select " + r.full_name);
        tick.addEventListener("click", (e) => e.stopPropagation());
        tick.addEventListener("change", () => {
          if (tick.checked) S.multi.add(r.full_name); else S.multi.delete(r.full_name);
          syncFoot();
        });
        const opt = h("div", "repo-option" + (i === active ? " is-active" : "") + (current ? " is-current" : ""), [
          tick,
          h("span", "repo-option-name", r.full_name),
          current ? svg("check", 14) : h("span", "repo-option-branch", r.default_branch || "main"),
        ]);
        opt.setAttribute("role", "option");
        opt.id = "repo-opt-" + i;
        opt.setAttribute("aria-selected", String(i === active));
        opt.addEventListener("mousedown", (e) => e.preventDefault());
        opt.addEventListener("click", () => chooseRepo(r));
        list.append(opt);
      });
      if (shown.length) search.setAttribute("aria-activedescendant", "repo-opt-" + active);
      else search.removeAttribute("aria-activedescendant");
      syncFoot();
    };
    search.addEventListener("input", () => { active = 0; render(); });
    search.addEventListener("keydown", (e) => {
      const shown = matching();
      if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, shown.length - 1); render(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); render(); }
      else if (e.key === "Enter") {
        e.preventDefault();
        const typed = parseRepos(search.value)[0];
        if (!shown.length && typed) checkRepos([typed]);
        else if (shown[active]) chooseRepo(shown[active]);
        return;
      }
      else if (e.key === "Escape") { e.preventDefault(); closeRepoMenu(); return; }
      list.querySelector(".is-active")?.scrollIntoView({ block: "nearest" });
    });
    list.append(skeleton());
    foot.hidden = true;
    search.focus({ preventScroll: true });
    try {
      repos = await getRepos();
      if (menu) render();
    } catch (err) {
      if (menu) list.replaceChildren(h("p", "repo-menu-empty", calm(err, "I could not load your repositories.")));
    }
  }

  // ---------------------------------------------------------------- checking many repositories at once

  async function checkRepos(names) {
    if (names.length === 1) { startAgent(names[0], () => checkManyRepos(names)); return; }
    checkManyRepos(names);
  }

  async function checkManyRepos(names, jobId, onDone) {
    closeRepoMenu(true);
    resetThread();
    if (location.pathname !== "/chat") history.pushState({}, "", "/chat");
    renderHistory();
    S.phase = "running";
    const run = S.run;
    setRepoChip(names.length === 1 ? { full_name: names[0] } : null);
    if (names.length > 1) repoChipLabel.textContent = names.length + " repositories";
    setComposer("off", "Checking...");
    userMsg(names.length === 1 ? "Check " + names[0] : "Check " + names.length + " repositories");
    const status = p("Reading GitHub code scanning...", "check-progress");
    const bar = h("span", "check-bar", h("span", "check-bar-fill"));
    assistant([status, bar]);
    scrollEnd();
    let job;
    try {
      job = jobId ? { id: jobId } : await SF.api("/api/fleet/check", { method: "POST", body: { repos: names } });
      if (!jobId) remember({ check: job.id, names: names }, true);
    } catch (err) {
      if (run !== S.run) return;
      status.textContent = calm(err, "I could not start that check.");
      bar.remove();
      afterCheck();
      return;
    }
    const tick = async () => {
      if (run !== S.run) return;
      try {
        job = await SF.api("/api/fleet/check/" + job.id);
      } catch (err) {
        status.textContent = calm(err, "I lost track of that check. Try again.");
        bar.remove();
        afterCheck();
        return;
      }
      if (run !== S.run) return;
      status.textContent = job.total > 1
        ? "Reading GitHub code scanning: " + job.done + " of " + job.total + " repositories"
        : "Reading GitHub code scanning...";
      bar.firstChild.style.width = Math.max(4, Math.round(100 * job.done / Math.max(1, job.total))) + "%";
      if (job.status !== "done") { S.pollTimer = setTimeout(tick, 900); return; }
      bar.closest(".msg").remove();
      showCheckResults(job.results);
      afterCheck();
      if (onDone) onDone();
    };
    tick();
  }

  function afterCheck() {
    S.phase = "repo";
    S.multi = new Set();
    setComposer("repo", "Type another repository (owner/name), pick from the list, or ask me anything");
    setQuick([{ label: "Check more repositories", primary: true, onClick: () => openRepoMenu(repoChip) },
      { label: "Start over", onClick: () => startNew(true) }]);
  }

  const BAND_NAMES = { critical: "Critical", high: "High", medium: "Medium", low: "Low", clean: "Clean" };

  function showCheckResults(results) {
    const scanned = results.filter((r) => r.status === "scanned");
    const risky = scanned.filter((r) => r.band === "critical" || r.band === "high");
    const lead = !scanned.length
      ? "I could not read code scanning results for " + (results.length === 1 ? "that repository." : "any of those repositories.")
      : risky.length
        ? risky.length + " of " + scanned.length + " need action now. Riskiest first:"
        : "None of them is Critical or High right now. Here is how they stand:";
    const cards = h("div", "check-list", results.map(checkCard));
    assistant([p(lead), cards]);
    scrollEnd();
  }

  function checkCard(r) {
    const name = h("span", "check-name", r.full_name);
    if (r.status !== "scanned") {
      const kids = [h("div", "check-head", [h("span", "check-score is-none", "?"), h("div", "check-title", [name,
        h("span", "check-meta", r.note || "No results.")])])];
      if (r.status === "hidden" || r.status === "error") {
        kids.push(h("div", "check-actions", button("Check a commit instead", "btn btn-secondary btn-sm",
          () => chooseRepo({ full_name: r.full_name }))));
      }
      return h("article", "check-card is-muted", kids);
    }
    const counts = [r.open_critical ? r.open_critical + " critical" : null, r.open_high ? r.open_high + " high" : null,
      r.open_total + " open"].filter(Boolean).join(" · ");
    const head = h("div", "check-head", [
      h("span", "check-score band-" + r.band, [h("strong", null, String(r.risk_score)), h("span", null, "risk")]),
      h("div", "check-title", [name, h("span", "check-meta", (BAND_NAMES[r.band] || "") + " · " + counts)]),
    ]);
    const kids = [head];
    if (r.top && r.top.length) {
      kids.push(h("ul", "check-top", r.top.map((v) => h("li", null, [
        h("span", "check-sev sev-" + v.severity, v.severity === "critical" ? "Critical" : "High"),
        h("span", "check-vuln", v.title),
        h("span", "check-where", v.file + ":" + v.line),
      ]))));
    }
    const open = h("a", "btn btn-primary btn-sm", "Review and fix");
    open.href = "/dashboard?repo=" + r.id;
    open.target = "_top";
    const report = h("a", "btn btn-secondary btn-sm", "Report");
    report.href = "/api/fleet/repos/" + r.id + "/report.pdf";
    report.setAttribute("download", "");
    kids.push(h("div", "check-actions", [open, report]));
    return h("article", "check-card", kids);
  }

  // Open below the anchor, or above it when there is no room (the composer chip sits at the bottom).
  function position(el, anchor) {
    const r = anchor.getBoundingClientRect();
    const width = Math.min(360, window.innerWidth - 32);
    el.style.width = width + "px";
    el.style.left = Math.max(16, Math.min(r.left, window.innerWidth - width - 16)) + "px";
    const below = window.innerHeight - r.bottom;
    if (below < 320 && r.top > below) {
      el.style.top = "auto";
      el.style.bottom = (window.innerHeight - r.top + 8) + "px";
    } else {
      el.style.bottom = "auto";
      el.style.top = (r.bottom + 8) + "px";
    }
  }

  document.addEventListener("mousedown", (e) => {
    if (menu && !menu.contains(e.target) && menuAnchor && !menuAnchor.contains(e.target)) closeRepoMenu(true);
  });
  window.addEventListener("resize", () => closeRepoMenu(true));
  repoChip.addEventListener("click", () => openRepoMenu(repoChip));

  function askCommit() {
    const run = S.run;
    const m = assistant([p("Which commit should I look at?")]);
    loadCommits(bodyOf(m), run);
  }

  async function loadCommits(body, run) {
    const sk = skeleton();
    body.append(sk);
    try {
      const data = await SF.api("/api/repos/" + repoPath(S.repo.full_name) + "/commits");
      if (run !== S.run) return;
      sk.remove();
      const commits = data.commits || [];
      if (!commits.length) {
        body.append(p("This repository has no commits yet. Make one and I will take a look."));
        return;
      }
      const list = h("div", "choice-list");
      const rows = commits.map((c) => {
        const row = commitChoice(c, list);
        list.append(row);
        return row;
      });
      rows.forEach((r, i) => { if (i >= COMMITS_SHOWN) r.hidden = true; });
      body.append(list);
      if (commits.length > COMMITS_SHOWN) {
        const more = button("Show more", "btn btn-ghost btn-sm choice-more", () => {
          rows.forEach((r) => { r.hidden = false; });
          list.classList.add("is-scroll");
          more.remove();
        });
        body.append(more);
      }
      focusFirstChoice(list);
      scrollEnd();
    } catch (err) {
      if (run !== S.run) return;
      sk.remove();
      body.append(p(calm(err, "I could not load the commits just now.")),
        button("Try again", "btn btn-secondary btn-sm", (e) => {
          e.currentTarget.previousElementSibling.remove();
          e.currentTarget.remove();
          loadCommits(body, run);
        }, "refresh"));
    }
  }

  function commitChoice(c, list) {
    const author = c.author || {};
    const meta = h("span", "choice-meta is-row", [
      avatar(author, "sm"),
      h("span", "meta-name", author.name || author.login || "Unknown"),
      c.time_label ? h("span", "meta-fixed", c.time_label) : null,
      h("span", "meta-fixed mono", c.short_sha || String(c.sha || "").slice(0, 7)),
    ]);
    meta.firstChild.classList.add("meta-fixed");
    const title = h("span", "choice-title", firstLine(c.message) || "Commit");
    const b = h("button", "choice choice-commit", [
      h("span", "choice-main", [title, meta]),
      h("span", "choice-end", badge(c.state)),
    ]);
    b.type = "button";
    b.addEventListener("click", () => pickCommit(c, list));
    return b;
  }

  function pickCommit(c, list) {
    if (S.phase !== "commit") return;
    if (c.analysis_id) {
      // Already checked by this person: open that conversation instead of starting a duplicate.
      S.phase = "busy";
      loadAnalysis(c.analysis_id, { push: true });
      return;
    }
    S.phase = "brief";
    S.commit = c;
    list.closest(".msg").querySelector(".choice-more")?.remove();
    list.remove();
    userMsg(commitBubble(c));
    askBrief();
  }

  function briefQuick() {
    return [
      { label: "Add a brief", onClick: () => {
        input.placeholder = "Say what your app should do, then press Enter to start";
        input.focus();
      } },
      { label: "Attach a document", onClick: () => fileInput.click() },
      { label: "Skip", onClick: () => {
        clearQuick();
        userMsg("Skip the brief");
        startAnalysis("");
      } },
    ];
  }

  function askBrief() {
    assistant([p("Want to tell me what your app is supposed to do? It helps me catch logic bugs.")]);
    setQuick(briefQuick());
    setComposer("brief", "Describe what your app should do");
  }

  // ---------------------------------------------------------------- brief file

  function fileChip(name, onRemove, busy) {
    const chip = h("span", "file-chip");
    chip.append(busy ? h("span", "spinner") : svg("paperclip", 14));
    chip.append(h("span", "file-chip-name", busy ? "Reading " + name : name));
    if (onRemove) {
      const x = h("button", "file-chip-remove", svg("x", 14));
      x.type = "button";
      x.setAttribute("aria-label", "Remove " + name);
      x.addEventListener("click", onRemove);
      chip.append(x);
    }
    return chip;
  }

  function renderAttachment(busyName) {
    attachments.replaceChildren();
    if (busyName) attachments.append(fileChip(busyName, null, true));
    else if (S.file) attachments.append(fileChip(S.file.name, removeFile));
    attachments.hidden = !(busyName || S.file);
  }

  function removeFile() {
    S.file = null;
    if (S.fileMsg) S.fileMsg.remove();
    S.fileMsg = null;
    renderAttachment();
    updateSend();
    if (S.phase === "brief") setQuick(briefQuick());
    input.focus();
  }

  attachBtn.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    const f = fileInput.files && fileInput.files[0];
    fileInput.value = "";
    if (f) attachFile(f);
  });

  async function attachFile(file) {
    if (S.phase !== "brief") return;
    const run = S.run;
    const ext = (file.name.split(".").pop() || "").toLowerCase();
    if (!FILE_TYPES.includes(ext)) {
      assistant([p("I can read .txt, .md, .pdf and .docx files. Try another file, or just type your brief.")]);
      scrollEnd();
      return;
    }
    if (file.size > FILE_LIMIT) {
      assistant([p("That file is larger than 2 MB. Try a smaller one, or paste the main points instead.")]);
      scrollEnd();
      return;
    }
    renderAttachment(file.name);
    attachBtn.disabled = true;
    try {
      const fd = new FormData();
      fd.append("file", file);
      const data = await SF.api("/api/brief/extract", { method: "POST", body: fd });
      if (run !== S.run) return;
      const text = (data && data.text || "").trim();
      if (!text) throw new Error("I could not find any text in that file.");
      S.file = { name: data.filename || file.name, text: text };
      renderAttachment();
      clearQuick();
      if (S.fileMsg) S.fileMsg.remove();
      S.fileMsg = assistant([p("I read " + S.file.name + ". Add a few words below if you like, or start the check now.")]);
      setQuick([{ label: "Start checking", primary: true, onClick: () => { const t = input.value.trim(); clearInput(); startFromBrief(t); } }]);
    } catch (err) {
      if (run !== S.run) return;
      renderAttachment();
      assistant([p(calm(err, "I could not read that file. Try another one, or type your brief."))]);
      scrollEnd();
    } finally {
      attachBtn.disabled = false;
      updateSend();
    }
  }

  // ---------------------------------------------------------------- run the analysis

  function startFromBrief(typed) {
    if (S.phase !== "brief") return;
    clearQuick();
    const fileName = S.file ? S.file.name : null;
    const brief = [S.file ? S.file.text : "", typed].filter(Boolean).join("\n\n").slice(0, BRIEF_LIMIT);
    userMsg(briefBubble(typed, fileName));
    S.file = null;
    S.fileMsg = null;
    renderAttachment();
    startAnalysis(brief);
  }

  async function startAnalysis(brief) {
    S.phase = "running";
    setComposer("off", "Checking your commit");
    const run = S.run;
    const body = { repo: S.repo.full_name, sha: S.commit.sha };
    if (brief) body.brief = brief;
    try {
      const res = await SF.api("/api/analyses", { method: "POST", body: body });
      if (run !== S.run) return;
      S.analysisId = res.id;
      history.pushState({}, "", "/chat/" + res.id);
      remember({ analysis: res.id }, true);
      showSteps(initialSteps(), "On it. This runs in the background, so you can find it again in your history.");
      refreshHistory();
      poll(res.id, run, 0, 300);
    } catch (err) {
      if (run !== S.run) return;
      S.phase = "failed";
      errorMsg(calm(err, "I could not start the check just now.") + " Nothing was changed.", () => startAnalysis(brief));
    }
  }

  function initialSteps() {
    return [
      { key: "find", label: "Looking for a bug", status: "waiting", note: "" },
      { key: "hack", label: "Trying to break it", status: "waiting", note: "" },
      { key: "fix", label: "Writing a fix", status: "waiting", note: "" },
      { key: "prove", label: "Checking the fix", status: "waiting", note: "" },
    ];
  }

  // Each engine step reads like a tool call: Name(argument), with its result on the line below.
  const TOOL_NAMES = { find: "Scan", hack: "Attack", fix: "Patch", prove: "Verify" };
  function toolArg(key) {
    const sha = S.commit ? (S.commit.short_sha || String(S.commit.sha || "").slice(0, 7)) : "";
    if (key === "find") return sha ? "commit " + sha : "commit";
    if (key === "hack") return "sandbox";
    if (key === "fix") return "smallest safe change";
    return "attack + tests";
  }

  function showSteps(steps, intro) {
    const list = h("div", "step-list");
    list.setAttribute("role", "list");
    S.steps = {};
    steps.forEach((s) => {
      const el = h("div", "step tool", [
        h("span", "tool-dot"),
        h("span", "step-text", [
          h("span", "tool-head", [
            h("span", "tool-name", TOOL_NAMES[s.key] || s.key),
            h("span", "tool-arg", "(" + toolArg(s.key) + ")"),
            h("span", "tool-label", s.label),
          ]),
          h("span", "tool-out", [h("span", "tool-elbow", "\u23bf"), h("span", "step-note")]),
        ]),
      ]);
      el.setAttribute("role", "listitem");
      S.steps[s.key] = el;
      list.append(el);
    });
    const m = assistant([p(intro), list]);
    updateSteps(steps);
    scrollEnd();
    return m;
  }

  function updateSteps(steps) {
    (steps || []).forEach((s) => {
      const el = S.steps[s.key];
      if (!el) return;
      if (el.dataset.state !== s.status) el.dataset.state = s.status;
      const note = el.querySelector(".step-note");
      if (note.textContent !== (s.note || "")) note.textContent = s.note || "";
    });
  }

  function poll(id, run, fails, delay) {
    S.pollTimer = setTimeout(async () => {
      try {
        const d = await SF.api("/api/analyses/" + id);
        if (run !== S.run) return;
        updateSteps(d.steps);
        if (d.status === "running") poll(id, run, 0, 1000);
        else finish(d, run);
      } catch (err) {
        if (run !== S.run) return;
        if (fails >= 4) {
          S.phase = "failed";
          errorMsg("I lost touch with the check. It may still be running, so you can try again.", () => {
            poll(id, run, 0, 300);
          });
        } else {
          poll(id, run, fails + 1, 1500);
        }
      }
    }, delay);
  }

  function finish(d, run) {
    if (run !== S.run) return;
    S.data = d;
    refreshHistory();
    if (d.status === "failed" || !d.result) {
      S.phase = "failed";
      setComposer("off", "Something went wrong with this check");
      errorMsg("Something went wrong while checking this commit. Nothing was changed.", () => {
        S.repo = S.repo || { full_name: d.repo };
        S.commit = S.commit || { sha: d.commit.sha };
        S.phase = "brief";
        startAnalysis(d.brief || "");
      });
      return;
    }
    S.phase = "done";
    const first = renderResult(d);
    setComposer("ask", "Ask me anything about this result");
    setQuick(followQuick());
    if (first) scrollToNode(first);
  }

  // ---------------------------------------------------------------- result

  function diffNode(diff) {
    const code = h("pre", "diff-code");
    String(diff).replace(/\s+$/, "").split("\n").forEach((line) => {
      let cls = "diff-line";
      if (/^(\+\+\+|---|diff |index |@@)/.test(line)) cls += " is-hunk";
      else if (line.startsWith("+")) cls += " is-add";
      else if (line.startsWith("-")) cls += " is-del";
      code.append(h("span", cls, line || " "));
    });
    return h("details", "diff", [h("summary", null, "See what changed"), code]);
  }

  // The changed lines, always visible: file name on top, old and new line numbers in the gutter.
  function changeView(diff) {
    const lines = String(diff).replace(/\s+$/, "").split("\n");
    const files = [];
    let oldNo = 0;
    let newNo = 0;
    const body = h("div", "change-lines");
    lines.forEach((line) => {
      if (line.startsWith("+++ ")) { files.push(line.slice(4).replace(/^b\//, "")); return; }
      if (line.startsWith("--- ") || line.startsWith("diff ") || line.startsWith("index ")) return;
      const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/);
      if (hunk) {
        oldNo = parseInt(hunk[1], 10);
        newNo = parseInt(hunk[2], 10);
        body.append(h("div", "change-line is-hunk", [h("span", "ln"), h("span", "ln"), h("span", "sign"), h("span", "code", (hunk[3] || "").trim() || "...")]));
        return;
      }
      let cls = "change-line";
      let a = "";
      let b = "";
      let sign = " ";
      if (line.startsWith("+")) { cls += " is-add"; b = newNo++; sign = "+"; }
      else if (line.startsWith("-")) { cls += " is-del"; a = oldNo++; sign = "-"; }
      else { a = oldNo++; b = newNo++; }
      body.append(h("div", cls, [h("span", "ln", String(a)), h("span", "ln", String(b)), h("span", "sign", sign), h("span", "code", line.slice(1) || " ")]));
    });
    const name = files[0] || "Changed file";
    return h("div", "change", [h("div", "change-file", [svg("repo", 14), h("span", "mono", name)]), body]);
  }

  function evidenceNode(evidence) {
    const code = h("pre", "diff-code", h("span", "diff-line evidence-line", evidence));
    return h("details", "diff", [h("summary", null, "What I sent"), code]);
  }

  function section(title, cls, kids) {
    return h("div", "result-section" + (cls ? " " + cls : ""), [h("h3", "result-title", title)].concat(kids));
  }

  function proofLine(ok, good, bad) {
    return h("div", "proof-line" + (ok ? "" : " is-bad"), [svg(ok ? "check" : "alert", 16), ok ? good : bad]);
  }

  function resourceNode(r) {
    const kind = String(r.kind || "").toLowerCase();
    const iconName = kind.includes("video") ? "play" : kind.includes("lab") || kind.includes("practice") ? "spark" : "book";
    const url = safeUrl(r.url);
    const kindLabel = kind ? kind.charAt(0).toUpperCase() + kind.slice(1) : "Guide";
    const node = h(url ? "a" : "div", "resource", [
      h("span", "icon-tile", svg(iconName, 16)),
      h("span", "resource-text", [
        h("span", "resource-title", r.title || "Learn more"),
        h("span", "resource-meta", r.source ? kindLabel + " from " + r.source : kindLabel),
      ]),
      url ? svg("external", 14) : null,
    ]);
    if (url) {
      node.href = url;
      node.target = "_blank";
      node.rel = "noopener noreferrer";
    }
    return node;
  }

  // Appends the result messages and returns the first one (so the view can start there).
  function renderResult(d) {
    const r = d.result;
    const fixed = r.verdict === "fixed";
    const attention = r.verdict === "attention";

    const lead = [p(r.summary)];
    if (r.repeat_note) lead.push(p(r.repeat_note, "muted"));
    if (attention) {
      lead.push(p("What to do next: look at the details below with a teammate who knows this code, or ask me how to fix it by hand."));
    }
    if (r.verdict === "safe") {
      lead.push(p("What I checked: the lines that changed, looking for anything an attacker could abuse, like database queries, file access and who can see whose data." +
        (d.brief ? " I also compared the change with what you told me the app should do." : "") +
        " Nothing could be turned into a real attack."));
    }
    const first = assistant(lead);

    if (r.verdict !== "safe") {
      const card = h("div", "result-card");

      if (r.what_wrong) card.append(section("What was wrong", "", [p(r.what_wrong)]));

      if (r.attack) {
        const kids = [p(r.attack.description)];
        if (r.attack.evidence) kids.push(evidenceNode(r.attack.evidence));
        card.append(section("The attack", "is-alert", kids));
      }

      if (r.proof) {
        const kids = [h("div", "proof-list", [
          proofLine(r.proof.attack_blocked, "Attack blocked", "The attack still works"),
          proofLine(r.proof.tests_pass, "Tests still pass", "Some of your tests fail"),
        ])];
        if (r.proof.note) kids.push(p(r.proof.note, "proof-note"));
        card.append(section("Proof", fixed ? "is-ok" : "is-alert", kids));
      }

      // The change comes last, so the Commit / Ignore bar sits right under it.
      if (r.fix) {
        const kids = [];
        if (r.fix.diff) kids.push(changeView(r.fix.diff));
        const about = h("ul", "change-about", [
          r.what_wrong ? h("li", null, [h("strong", null, "Problem: "), firstSentence(r.what_wrong)]) : null,
          r.fix.explanation ? h("li", null, [h("strong", null, "Change: "), r.fix.explanation]) : null,
          r.proof ? h("li", null, [h("strong", null, "Result: "),
            r.proof.attack_blocked && r.proof.tests_pass ? "the attack is blocked and your tests still pass."
              : !r.proof.attack_blocked ? "the attack still works, so this change is not enough." : "some of your tests fail with this change."]) : null,
        ]);
        kids.push(about);
        card.append(section(fixed ? "The change" : "The change I tried", fixed ? "" : "is-alert", kids));
      }

      const parts = [card];
      if (r.fix) parts.push(decisionNode(d));
      assistant(parts);

      const learning = (r.learning || []).slice(0, 3);
      if (MODES.learn && learning.length) {
        assistant([h("div", "result-card", section("Learn this", "", [h("div", "resource-list", learning.map(resourceNode))]))]);
      }
    }

    assistant([reportLink(d)]);

    (d.messages || []).forEach((msg) => {
      if (msg.role === "user") userMsg(msg.text);
      else assistant([p(msg.text, "msg-reply")]);
    });
    return first;
  }

  // Download the technical report (written from the recorded review) as a PDF.
  function reportLink(d) {
    const a = h("a", "report-link", [svg("book", 16), h("span", "report-link-text", [
      h("span", "report-link-title", "Technical report"),
      h("span", "report-link-sub", "Finding, attack evidence, the fix, verification and every action taken"),
    ]), h("span", "report-link-cta", "Download PDF")]);
    a.href = "/api/analyses/" + d.id + "/report.pdf";
    a.setAttribute("download", "");
    a.addEventListener("click", () => SF.toast("Preparing your report. This can take a few seconds."));
    return a;
  }

  function firstSentence(text) {
    const m = String(text || "").match(/^.*?[.!?](\s|$)/);
    return (m ? m[0] : String(text || "")).trim();
  }

  function decisionNode(d) {
    const r = d.result;
    const wrap = h("div", "decision");
    const showDone = (dec) => {
      wrap.replaceChildren();
      wrap.classList.add("is-done");
      if (dec.action === "commit") {
        const bits = [svg("check", 16), h("span", null, [
          "Committed to ", h("span", "mono", dec.branch || "a new branch"),
          dec.commit_sha ? " as " : null,
          dec.commit_sha ? h("span", "mono", dec.commit_sha) : null,
          ". Your original commit is untouched.",
        ])];
        wrap.append(h("div", "decision-status", bits));
        const pr = dec.pull_request && safeUrl(dec.pull_request.url);
        if (pr) {
          const a = h("a", "btn btn-secondary btn-sm", ["View pull request", svg("external", 14)]);
          a.href = pr; a.target = "_blank"; a.rel = "noopener noreferrer";
          wrap.append(a);
        }
      } else {
        wrap.classList.add("is-ignored");
        wrap.append(h("div", "decision-status", [svg("x", 16), "Ignored. Nothing was changed in your code."]));
      }
    };
    if (r.decision) { showDone(r.decision); return wrap; }

    const fixed = r.verdict === "fixed";
    const decide = async (action, btn) => {
      wrap.querySelectorAll("button").forEach((b) => { b.disabled = true; });
      const label = btn.textContent;
      btn.textContent = action === "commit" ? "Committing" : "Ignoring";
      try {
        const dec = await SF.api("/api/analyses/" + d.id + "/decision", { method: "POST", body: { action: action } });
        r.decision = dec;
        showDone(dec);
        SF.toast(action === "commit" ? "Fix committed" : "Fix ignored");
        refreshHistory();
      } catch (err) {
        wrap.querySelectorAll("button").forEach((b) => { b.disabled = false; });
        btn.textContent = label;
        SF.toast(calm(err, "That did not work. Try again."));
      }
    };
    wrap.append(h("p", "decision-ask", fixed ? "Do you want me to commit this fix?" : "This fix did not pass its checks, so I will not commit it."));
    const row = h("div", "decision-actions");
    if (fixed) {
      const commitBtn = button("Commit fix", "btn btn-primary btn-sm", (e) => decide("commit", e.currentTarget), "check");
      row.append(commitBtn);
    }
    row.append(button(fixed ? "Ignore" : "Ignore for now", "btn btn-secondary btn-sm", (e) => decide("ignore", e.currentTarget), "x"));
    wrap.append(row);
    return wrap;
  }

  function pullRequestNode(d) {
    const wrap = h("div", "result-actions");
    const showOpened = (pr) => {
      wrap.replaceChildren();
      const url = pr && safeUrl(pr.url);
      if (url) {
        const a = h("a", "btn btn-secondary btn-sm", ["View pull request", svg("external", 14)]);
        a.href = url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        wrap.append(a);
      } else {
        wrap.append(h("span", "result-done", [svg("check", 16), pr && pr.status === "merged" ? "Pull request merged" : "Pull request opened"]));
      }
    };
    const existing = d.result && d.result.pull_request;
    if (existing) {
      showOpened(existing);
      return wrap;
    }
    const b = button("Open pull request", "btn btn-primary btn-sm");
    b.addEventListener("click", async () => {
      b.disabled = true;
      b.textContent = "Opening";
      try {
        const pr = await SF.api("/api/analyses/" + d.id + "/pull-request", { method: "POST" });
        showOpened(pr);
        SF.toast("Pull request opened");
        if (GAME) GAME.refresh();
      } catch (err) {
        b.disabled = false;
        b.textContent = "Open pull request";
        SF.toast(calm(err, "I could not open the pull request. Try again."));
      }
    });
    wrap.append(b);
    return wrap;
  }

  // ---------------------------------------------------------------- follow-up questions

  const FOLLOW_UPS = ["Explain it more simply", "How do I avoid this?"];

  function followQuick() {
    const attention = S.data && S.data.result && S.data.result.verdict === "attention";
    const asks = attention ? ["Explain it more simply", "How do I fix it myself?"] : FOLLOW_UPS;
    const items = asks.filter((q) => !S.asked.has(q)).map((q) => ({ label: q, onClick: () => askFollowUp(q) }));
    items.push({ label: "Check another commit", onClick: () => startNew(true) });
    return items;
  }

  async function askFollowUp(text, retrying) {
    if (S.phase !== "done" || !S.analysisId) return;
    const run = S.run;
    const id = S.analysisId;
    clearQuick();
    if (!retrying) userMsg(text);
    S.asked.add(text);
    setComposer("off", "Thinking");
    const wait = typing();
    try {
      const res = await SF.api("/api/analyses/" + id + "/messages", { method: "POST", body: { text: text } });
      if (run !== S.run) return;
      wait.remove();
      assistant([p((res.reply && res.reply.text) || "I do not have an answer for that yet.", "msg-reply")]);
      setComposer("ask", "Ask me anything about this result");
      setQuick(followQuick());
      input.focus({ preventScroll: true });
    } catch (err) {
      if (run !== S.run) return;
      wait.remove();
      setComposer("ask", "Ask me anything about this result");
      const msg = calm(err, "I could not answer that just now.");
      const tooLong = /getting long|new check/i.test(msg);
      errorMsg(msg, tooLong ? null : () => askFollowUp(text, true));
      if (tooLong) setQuick([{ label: "Check another commit", primary: true, onClick: () => startNew(true) }]);
    }
  }

  // Any message that is not a repository: the assistant answers, with the agent run on screen (if any) and the
  // repository's project brief as context. The conversation stays in this thread.
  async function askAnything(text, retrying) {
    const run = S.run;
    if (!retrying) userMsg(text);
    const before = S.phase;
    setComposer("off", "Thinking");
    const wait = typing();
    try {
      const res = await SF.api("/api/chat/ask", { method: "POST", body: {
        text: text, history: (S.talk || []).slice(-8), run_id: S.agentRun || null,
        repo: S.repo ? S.repo.full_name : null } });
      if (run !== S.run) return;
      wait.remove();
      S.talk = (S.talk || []).concat([{ role: "user", text: text }, { role: "assistant", text: res.reply }]).slice(-16);
      remember({ talk: S.talk });
      assistant(String(res.reply || "").split(/\n{2,}/).map((para) => p(para, "msg-reply")));
      if (GAME) GAME.refresh();
    } catch (err) {
      if (run !== S.run) return;
      wait.remove();
      errorMsg(calm(err, "I could not answer that just now."), () => askAnything(text, true));
    } finally {
      if (run === S.run) {
        S.phase = before;
        setComposer("repo", S.agentRun ? "Ask about this result, type another repository, or pick from the list"
          : "Type a repository (owner/name or a GitHub link), pick from the list, or ask me anything");
        input.focus({ preventScroll: true });
      }
    }
  }

  // ---------------------------------------------------------------- Fix all (from the dashboard)

  // Every open problem of one repository: listed as it is handled, then all the fixes with Commit all.
  // A demo repository uses the prepared fixes; a GitHub repository gets one agent run on the riskiest problems,
  // whose pull request is the Commit all.
  const SEV = { critical: "Critical", high: "High", medium: "Medium", low: "Low" };
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  function notifyDashboard() {
    try { window.parent.postMessage({ type: "kaizen:fleet-changed" }, location.origin); } catch (_) { /* not embedded */ }
  }

  async function startFixAll(repoId, kept) {
    closeRepoMenu(true);
    resetThread();
    renderHistory();
    S.phase = "running";
    const run = S.run;
    setComposer("off", "Fixing...");
    if (kept && kept.done) {  // reopened after Commit all: show how it ended
      userMsg("Fix all problems in " + (kept.name || "the repository"));
      assistant([p(kept.done)]);
      replayTalk(kept.talk);
      afterAgent();
      return;
    }
    const thinking = typing();
    let res;
    try {
      res = await SF.api(`/api/fleet/repos/${repoId}/fix-all`, { method: "POST" });
    } catch (err) {
      thinking.remove();
      if (run !== S.run) return;
      assistant([p(calm(err, "I could not start fixing that repository."))]);
      afterAgent();
      return;
    }
    thinking.remove();
    if (run !== S.run) return;
    const name = res.repo.full_name;
    setRepoChip({ full_name: name });
    userMsg("Fix all problems in " + name);

    if (res.mode === "agent") {
      if (!res.run) { assistant([p(res.message)]); afterAgent(); return; }
      if ((res.handling || []).length) {
        assistant([p(`The KAIZEN agent is taking on the ${res.handling.length} riskiest problems in ${name}:`),
          h("ol", "fixall-list", res.handling.map((v) => h("li", "fixall-item", [
            h("span", "sev sev-" + v.severity, SEV[v.severity] || v.severity),
            h("span", "fixall-what", [v.title, h("code", null, `${v.file}:${v.line}`)])])))]);
      }
      history.replaceState({}, "", "/chat?run=" + res.run);
      remember({ run: res.run }, true);
      showRun(res.run, run, notifyDashboard);
      return;
    }

    remember({ fixall: repoId, name: name }, true);
    if (!res.fixes.length) { assistant([p(res.message)]); afterAgent(); return; }
    const n = res.fixes.length;
    const rows = res.fixes.map((f) => {
      const mark = h("span", "fixall-mark", h("span", "spinner"));
      const li = h("li", "fixall-item is-running", [mark,
        h("span", "sev sev-" + f.vuln.severity, SEV[f.vuln.severity] || f.vuln.severity),
        h("span", "fixall-what", [f.vuln.title, h("code", null, `${f.vuln.file}:${f.vuln.line}`)])]);
      return { li, mark };
    });
    assistant([p(`Fixing ${n} problem${n === 1 ? "" : "s"} in ${name}:`), h("ol", "fixall-list", rows.map((r) => r.li))]);
    scrollEnd();
    for (const r of rows) {  // the fixes are prepared already; show them landing one by one
      await wait(n > 12 ? 60 : 180);
      if (run !== S.run) return;
      r.li.classList.replace("is-running", "is-done");
      r.mark.replaceChildren(svg("check", 12));
    }

    const actions = h("div", "agent-actions");
    const commit = button(`Commit all ${n} fix${n === 1 ? "" : "es"}`, "btn btn-primary btn-sm", async () => {
      commit.disabled = true;
      try {
        const out = await SF.api(`/api/fleet/repos/${repoId}/commit-all`, { method: "POST",
          body: { fix_ids: res.fixes.map((f) => f.fix_id) } });
        actions.replaceChildren(h("div", "agent-verdict is-verified", [h("strong", null, "Committed"), h("span", null, out.message)]));
        remember({ done: out.message });
        notifyDashboard();
      } catch (err) { SF.toast(calm(err, "The fixes could not be committed.")); commit.disabled = false; }
    }, "check");
    const review = h("a", "btn btn-secondary btn-sm", "Review them on the dashboard");
    review.href = "/dashboard?repo=" + repoId;
    actions.append(commit, review);
    assistant([
      h("div", "agent-verdict is-verified", [h("strong", null, `${n} fix${n === 1 ? "" : "es"} ready`),
        h("span", null, "Each one is shown below. Commit them all at once, or review them one by one on the dashboard.")]),
      h("div", "fixall-fixes", res.fixes.map((f, i) => h("details", "agent-code fixall-fix" + (i === 0 ? "" : ""), [
        h("summary", null, [h("span", "sev sev-" + f.vuln.severity, SEV[f.vuln.severity] || f.vuln.severity), " ",
          f.vuln.title + " · " + f.vuln.file + ":" + f.vuln.line]),
        p(f.explanation || ""), changeView(f.diff)]))),
      actions]);
    S.phase = "repo";
    setComposer("repo", "Ask about these fixes, type a repository (owner/name), or pick from the list");
    scrollEnd();
  }

  // ---------------------------------------------------------------- open a stored conversation

  async function loadAnalysis(id, opts) {
    resetThread();
    const run = S.run;
    S.analysisId = id;
    if (opts && opts.push) history.pushState({}, "", "/chat/" + id);
    if ((savedThread() || {}).analysis !== id) remember({ analysis: id }, true);
    renderHistory();
    setComposer("off", "Loading");
    const wait = assistant([skeleton()]);
    try {
      const d = await SF.api("/api/analyses/" + id);
      if (run !== S.run) return;
      wait.remove();
      S.data = d;
      S.repo = { full_name: d.repo };
      S.commit = d.commit;
      S.phase = d.status === "running" ? "running" : d.status === "done" ? "done" : "failed";

      setRepoChip(S.repo);
      userMsg("Check " + d.repo);
      userMsg(commitBubble(d.commit));
      if (d.brief) userMsg(briefBubble(d.brief));

      const running = d.status === "running";
      showSteps(d.steps, running ? "On it. This runs in the background, so you can come back to it later."
        : "Here is how I checked this commit.");

      if (running) {
        poll(id, run, 0, 600);
      } else if (d.status === "done" && d.result) {
        const first = renderResult(d);
        setComposer("ask", "Ask me anything about this result");
        setQuick(followQuick());
        if ((d.messages || []).length) scrollEnd(false);
        else if (first) scrollToNode(first);
      } else {
        setComposer("off", "Something went wrong with this check");
        errorMsg("Something went wrong while checking this commit. Nothing was changed.", () => {
          S.phase = "brief";
          startAnalysis(d.brief || "");
        });
      }
      refreshHistory();
    } catch (err) {
      if (run !== S.run) return;
      wait.remove();
      S.phase = "failed";
      S.analysisId = null;
      renderHistory();
      errorMsg(calm(err, "I could not open that check just now."), () => loadAnalysis(id, { push: false }));
      setQuick([{ label: "Start a new analysis", onClick: () => startNew(true) }]);
    }
  }

  // ---------------------------------------------------------------- wiring

  $("new-chat").addEventListener("click", () => {
    sidebar.classList.remove("is-open");
    syncToggle();
    startNew(true);
  });

  // ---------------------------------------------------------------- the security repair agent

  const STEP_ICON = { waiting: "", running: "", done: "check", failed: "x", skipped: "" };
  const VERDICTS = {
    verified: ["Verified", "The exploit is fixed and the existing tests still pass.", "is-verified"],
    failed: ["Failed", "The patch did not pass verification, so it was not accepted.", "is-failed"],
    inconclusive: ["Inconclusive", "Some evidence could not be collected; read why below.", "is-inconclusive"],
    none_found: ["No vulnerability found", "Nothing exploitable was confirmed in this repository.", "is-none"],
  };

  async function startAgent(repoName, fallback) {
    closeRepoMenu(true);
    resetThread();
    renderHistory();
    S.phase = "running";
    const run = S.run;
    setRepoChip({ full_name: repoName });
    setComposer("off", "The agent is working...");
    userMsg("Find and fix vulnerabilities in " + repoName);
    let res;
    try {
      res = await SF.api("/api/agent/runs", { method: "POST", body: { repo: repoName } });
    } catch (err) {
      if (run !== S.run) return;
      if (fallback && /Sign in with GitHub/i.test(err.message || "")) {
        assistant([p("The security agent needs a GitHub sign-in to download repositories. Running the quick check instead.")]);
        S.phase = "repo";
        fallback();
        return;
      }
      assistant([p(calm(err, "I could not start the agent."))]);
      afterAgent();
      return;
    }
    history.pushState({}, "", "/chat?run=" + res.id);
    remember({ run: res.id }, true);
    if (GAME) GAME.refresh();
    showRun(res.id, run);
  }

  async function showRun(id, run, onDone) {
    run = run || S.run;
    S.phase = "running";
    const head = h("div", "agent-head", [h("span", "agent-badge", "KAIZEN agent"), h("span", "agent-repo", "")]);
    const steps = h("ol", "agent-steps");
    const msg = assistant([head, steps]);
    scrollEnd();
    let fails = 0; let quiz = null;
    const tick = async () => {
      if (run !== S.run) return;
      let d;
      try { d = await SF.api("/api/agent/runs/" + id); fails = 0; }
      catch (err) {
        if (++fails > 4) { bodyOf(msg).append(p(calm(err, "I lost track of the agent run."))); afterAgent(); return; }
        S.pollTimer = setTimeout(tick, 2000); return;
      }
      if (run !== S.run) return;
      head.lastChild.textContent = d.repo + (d.result.workspace ? " @ " + d.result.workspace.sha.slice(0, 7) : "");
      setRepoChip({ full_name: d.repo });
      renderSteps(steps, d.steps);
      if (GAME && d.result.quiz_ready && !quiz) quiz = GAME.quiz(id, steps);
      if (d.status === "running") { S.pollTimer = setTimeout(tick, 1500); return; }
      S.agentRun = d.id;
      const reveal = () => {
        if (run !== S.run) return;
        renderAgentResult(d);
        afterAgent();
        if (GAME) GAME.refresh();
        if (onDone) onDone();
      };
      if (quiz) quiz.then(reveal); else reveal();
    };
    tick();
  }

  function renderSteps(list, steps) {
    list.replaceChildren(...steps.map((st) => {
      const icon = st.status === "running" ? h("span", "spinner") : STEP_ICON[st.status] ? svg(STEP_ICON[st.status], 12) : null;
      return h("li", "agent-step is-" + st.status, [
        h("span", "agent-step-icon", icon),
        h("div", "agent-step-main", [
          h("span", "agent-step-label", [st.label, st.seconds != null ? h("span", "agent-step-time", st.seconds + " s") : null]),
          st.note ? h("span", "agent-step-note", st.note) : null,
        ]),
      ]);
    }));
  }

  function kv(rows) {
    return h("dl", "agent-kv", rows.filter(Boolean).flatMap(([k, v]) => [h("dt", null, k), h("dd", null, v)]));
  }

  function renderAgentResult(d) {
    const r = d.result || {};
    const kids = [];
    if (d.status === "error") {
      kids.push(h("div", "agent-verdict is-failed", [h("strong", null, "The run stopped"), h("span", null, r.error || "Something went wrong.")]));
      assistant(kids);
      scrollEnd();
      return;
    }
    const fixes = r.fixes && r.fixes.length > 1 ? r.fixes : null;
    const [label, single, cls] = VERDICTS[r.verdict] || VERDICTS.inconclusive;
    const held = fixes ? fixes.filter((x) => x.verdict === "verified").length : 0;
    const lead = !fixes ? single : r.verdict === "verified"
      ? `All ${fixes.length} vulnerabilities are fixed and the existing tests still pass.`
      : `${held} of ${fixes.length} fixes verified; read each one below.`;
    const reasons = (r.reasons || []).length ? h("ul", "agent-reasons", r.reasons.map((x) => h("li", null, x))) : null;
    kids.push(h("div", "agent-verdict " + cls, [h("strong", null, label), h("span", null, lead), reasons]));

    if (fixes) {
      fixes.forEach((x, i) => {
        const [vl, , vc] = VERDICTS[x.verdict] || VERDICTS.inconclusive;
        kids.push(h("div", "agent-fix-head", [h("span", "agent-fix-num", "Fix " + (i + 1) + " of " + fixes.length),
          h("strong", null, x.finding.title + " in " + x.finding.file + ":" + x.finding.line),
          h("span", "agent-fix-verdict " + vc, vl)]));
        kids.push(...fixSections(x));
      });
    } else {
      kids.push(...fixSections(r));
    }
    const base = r.baseline || [];
    if (base.length) {
      const after = r.patched || [];
      kids.push(h("section", "agent-section", [h("h4", null, fixes ? "Existing tests (all fixes applied)" : "Existing tests"),
        h("table", "agent-tests", [
          h("thead", null, h("tr", null, [h("th", null, "Suite"), h("th", null, "Before the fix"), h("th", null, "After the fix")])),
          h("tbody", null, base.map((b, i) => h("tr", null, [h("td", null, b.kind),
            h("td", null, b.ran ? b.passed + " passed, " + b.failed + " failed" : "did not run"),
            h("td", null, after[i] && after[i].ran ? after[i].passed + " passed, " + after[i].failed + " failed" : "-")]))),
        ])]));
    }
    const patch = r.patch || {};
    const actions = h("div", "agent-actions");
    if (d.status === "done") {
      const pdf = h("a", "btn btn-secondary btn-sm", "Download report");
      pdf.href = "/api/agent/runs/" + d.id + "/report.pdf";
      pdf.setAttribute("download", "");
      actions.append(pdf);
      if (r.pull_request && safeUrl(r.pull_request.url)) {
        const a = h("a", "btn btn-primary btn-sm", ["View pull request", svg("external", 14)]);
        a.href = r.pull_request.url; a.target = "_blank"; a.rel = "noopener noreferrer";
        actions.prepend(a);
      } else if (patch.diff && (r.verdict === "verified" || r.verdict === "inconclusive")) {
        const many = (r.fixes || []).filter((x) => x.verdict !== "failed").length;
        const pr = button(many > 1 ? `Commit all ${many} fixes (pull request)` : "Open pull request", "btn btn-primary btn-sm", async () => {
          pr.disabled = true;
          try {
            const out = await SF.api("/api/agent/runs/" + d.id + "/pull-request", { method: "POST" });
            const a = h("a", "btn btn-primary btn-sm", ["View pull request", svg("external", 14)]);
            a.href = out.url; a.target = "_blank"; a.rel = "noopener noreferrer";
            pr.replaceWith(a);
            if (GAME) GAME.refresh();
          } catch (err) { SF.toast(calm(err, "GitHub did not accept the pull request.")); pr.disabled = false; }
        });
        actions.prepend(pr);
      }
    }
    kids.push(actions);
    if (r.seconds != null) kids.push(p(`${r.seconds} s · ${r.commands || 0} commands · ${r.model_calls || 0} model calls · ${(r.tools || {}).model || ""}`, "agent-note"));
    assistant(kids);
    scrollEnd();
  }

  // What is wrong, the fix, the proof and the checks for one finding (a run's result, or one of its fixes).
  function fixSections(r) {
    const kids = [];
    const f = r.finding;
    if (f) {
      const rc = r.root_cause || {};
      kids.push(h("section", "agent-section", [
        h("h4", null, "What is wrong"),
        p(rc.plain || f.reason || ""),
        kv([["Weakness", (f.cwe ? f.cwe + " " : "") + f.title], ["Severity", (f.severity || "").replace(/^./, (c) => c.toUpperCase())],
          ["Where", f.file + ":" + f.line], ["Found by", f.scanner || "-"],
          rc.flow && rc.flow.length ? ["Data flow", rc.flow.join("  →  ")] : null]),
      ]));
    }
    const patch = r.patch || {};
    if (patch.diff) {
      kids.push(h("section", "agent-section", [h("h4", null, "The fix"), p(patch.explanation || ""),
        changeView(patch.diff), patch.attempts > 1 ? p("Written in " + patch.attempts + " attempts.", "agent-note") : null]));
    }
    const sec = r.security_test;
    if (sec && sec.code) {
      const det = h("details", "agent-code", [h("summary", null, "Exploit test: " + (sec.path || "")), h("pre", null, sec.code)]);
      kids.push(h("section", "agent-section", [h("h4", null, "Proof"),
        kv([["Attack", sec.attack || "-"],
          ["Original code", outcome(sec.before)], ["Patched code", outcome(sec.after)]]), det]));
    }
    if ((r.gates || []).length) {
      kids.push(h("section", "agent-section", [h("h4", null, "Checks"), h("ul", "agent-gates", r.gates.map((g) =>
        h("li", "is-" + g.status, [h("span", "agent-gate-mark", g.status === "pass" ? svg("check", 12) : g.status === "fail" ? svg("x", 12) : "–"),
          h("span", "agent-gate-label", g.label || g.key), h("span", "agent-gate-detail", g.detail || "")])))]));
    }
    const learn = r.learning;
    if (learn && (learn.resources || []).length) {
      kids.push(h("section", "agent-section", [h("h4", null, "Learn from this fix"),
        h("ul", "agent-reasons agent-learn-remember", (learn.remember || []).map((t) => h("li", null, t))),
        h("ul", "agent-learn", learn.resources.filter((x) => safeUrl(x.url)).map((x) => {
          const a = h("a", null, [h("span", "agent-learn-kind", x.kind), x.title, h("span", "agent-learn-src", " · " + x.source)]);
          a.href = x.url; a.target = "_blank"; a.rel = "noopener noreferrer";
          return h("li", null, a);
        }))]));
    }
    return kids;
  }

  function outcome(o) {
    if (!o) return "not run";
    const name = { assert_failed: "Exploit worked (test failed)", passed: "Exploit blocked (test passed)", error: "Test errored", not_run: "Not run" }[o.outcome] || o.outcome;
    return o.detail && o.outcome !== "passed" ? name + ": " + o.detail : name;
  }

  function afterAgent() {
    S.phase = "repo";
    setComposer("repo", "Ask about this result, type another repository (owner/name or a link), or pick from the list");
    setQuick([{ label: "Check another repository", primary: true, onClick: () => openRepoMenu(repoChip) },
      { label: "Start over", onClick: () => startNew(true) }]);
  }

  function route() {
    const params = new URLSearchParams(location.search);
    const runId = parseInt(params.get("run") || "", 10);
    const kept = savedThread() || {};
    const fixAll = parseInt(params.get("fixall") || "", 10);
    if (location.pathname === "/chat" && fixAll) {  // Fix all from the dashboard
      history.replaceState({}, "", "/chat");
      startFixAll(fixAll, kept.fixall === fixAll ? kept : null);
      return;
    }
    if (location.pathname === "/chat" && runId) {
      resetThread();
      renderHistory();
      const same = kept.run === runId;
      if (!same) remember({ run: runId }, true);
      showRun(runId, null, same ? () => replayTalk(kept.talk) : null);
      return;
    }
    const m = location.pathname.match(/^\/chat\/(\d+)\/?$/);
    if (m) { loadAnalysis(parseInt(m[1], 10), { push: false }); return; }
    // Plain /chat: the floating panel or the full-screen button. Continue the conversation of this tab, if any.
    if (kept.run) {
      history.replaceState({}, "", "/chat?run=" + kept.run);
      resetThread();
      renderHistory();
      showRun(kept.run, null, () => replayTalk(kept.talk));
    } else if (kept.analysis) {
      history.replaceState({}, "", "/chat/" + kept.analysis);
      loadAnalysis(kept.analysis, { push: false });
    } else if (kept.fixall) {
      startFixAll(kept.fixall, kept);
    } else if (kept.check && (kept.names || []).length) {
      checkManyRepos(kept.names, kept.check, () => replayTalk(kept.talk));
    } else {
      Promise.resolve(startNew(false)).then(() => { if ((kept.talk || []).length) replayTalk(kept.talk); });
    }
  }
  window.addEventListener("popstate", route);

  if (modeChip) {
    modeChip.textContent = MODES.learn && MODES.report ? "Learn + Report" : MODES.report ? "Report mode" : "Learn mode";
  }
  if (root.dataset.game === "1" && window.KaizenGame) {
    GAME = window.KaizenGame.init({ bar: $("game-bar"), post: (node) => { assistant([node]); scrollEnd(); } });
    GAME.refresh();
  }
  route();
})();
