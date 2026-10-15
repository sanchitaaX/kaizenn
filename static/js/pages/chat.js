// Chat: pick a repository, pick a commit, optionally add a brief, then watch the check and talk about the result.
// Everything is a message in one thread. No numbers, scores or charts are ever shown.

(function () {
  "use strict";

  const SF = window.SecureFix;
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
  };
  let composerMode = "off"; // off | brief | ask

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
    const on = mode === "brief" || mode === "ask";
    input.disabled = !on;
    input.maxLength = mode === "ask" ? 2000 : BRIEF_LIMIT;  // the server accepts 2,000 characters per question
    input.placeholder = placeholder || "";
    attachBtn.disabled = mode !== "brief";
    box.classList.toggle("is-off", !on);
    updateSend();
  }

  function updateSend() {
    const hasText = input.value.trim().length > 0;
    sendBtn.disabled = !(composerMode === "ask" ? hasText : composerMode === "brief" ? hasText || !!S.file : false);
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
    }
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
    renderAttachment();
    clearInput();
    thread.replaceChildren();
    scroller.scrollTop = 0;
  }

  // ---------------------------------------------------------------- new analysis: repo, commit, brief

  function startNew(push) {
    resetThread();
    if (push && location.pathname !== "/chat") history.pushState({}, "", "/chat");
    renderHistory();
    S.phase = "repo";
    setRepoChip(null);
    setComposer("off", "Pick a repository to begin");
    showWelcome(S.run);
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
      p("Pick a repository. I will find the risky change, try to break it, fix it and prove the fix works.", "welcome-sub"),
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

  // Picking a repository from the welcome cards, the dropdown or the composer chip.
  function chooseRepo(repo) {
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
    menu = h("div", "repo-menu", [h("div", "repo-menu-head", [svg("search", 14), search]), list]);
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
      if (!shown.length) list.append(h("p", "repo-menu-empty", "No repository matches that."));
      active = Math.min(active, Math.max(0, shown.length - 1));
      shown.forEach((r, i) => {
        const current = S.repo && S.repo.full_name === r.full_name;
        const opt = h("div", "repo-option" + (i === active ? " is-active" : "") + (current ? " is-current" : ""), [
          svg("repo", 14),
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
    };
    search.addEventListener("input", () => { active = 0; render(); });
    search.addEventListener("keydown", (e) => {
      const shown = matching();
      if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(active + 1, shown.length - 1); render(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(active - 1, 0); render(); }
      else if (e.key === "Enter") { e.preventDefault(); if (shown[active]) chooseRepo(shown[active]); return; }
      else if (e.key === "Escape") { e.preventDefault(); closeRepoMenu(); return; }
      list.querySelector(".is-active")?.scrollIntoView({ block: "nearest" });
    });
    list.append(skeleton());
    search.focus({ preventScroll: true });
    try {
      repos = await getRepos();
      if (menu) render();
    } catch (err) {
      if (menu) list.replaceChildren(h("p", "repo-menu-empty", calm(err, "I could not load your repositories.")));
    }
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

    (d.messages || []).forEach((msg) => {
      if (msg.role === "user") userMsg(msg.text);
      else assistant([p(msg.text, "msg-reply")]);
    });
    return first;
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

  // ---------------------------------------------------------------- open a stored conversation

  async function loadAnalysis(id, opts) {
    resetThread();
    const run = S.run;
    S.analysisId = id;
    if (opts && opts.push) history.pushState({}, "", "/chat/" + id);
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

  function route() {
    const m = location.pathname.match(/^\/chat\/(\d+)\/?$/);
    if (m) loadAnalysis(parseInt(m[1], 10), { push: false });
    else startNew(false);
  }
  window.addEventListener("popstate", route);

  if (modeChip) {
    modeChip.textContent = MODES.learn && MODES.report ? "Learn + Report" : MODES.report ? "Report mode" : "Learn mode";
  }
  route();
})();
