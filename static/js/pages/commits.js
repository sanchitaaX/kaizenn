// Commits tab: loads every team commit once, filters by person and state in the browser,
// and opens or starts a check in chat. All text is set with textContent (never innerHTML).

(() => {
  const listEl = document.getElementById("commit-list");
  if (!listEl) return;

  const SF = window.SecureFix;
  const SVG_NS = "http://www.w3.org/2000/svg";
  const LABELS = { new: "New", safe: "Safe", fixed: "Fixed", attention: "Needs attention" };
  const STATE_TEXT = { new: "new", safe: "safe", fixed: "fixed", attention: "needing attention" };

  let commits = [];
  const filters = { member: "", state: "" };
  let failed = false;
  let repo = "";          // "" = every repository already pulled in, else "owner/name"
  let loadRun = 0;        // ignore answers from an older repository pick
  const repoSelect = document.getElementById("repo-select");
  const memberFilter = document.getElementById("member-filter");
  const REPO_KEY = "securefix.commits.repo";
  const repoPath = (name) => String(name).split("/").map(encodeURIComponent).join("/");

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };

  const icon = (id) => {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const use = document.createElementNS(SVG_NS, "use");
    use.setAttribute("href", "#i-" + id);
    svg.appendChild(use);
    return svg;
  };

  const avatar = (person) => {
    if (person && person.avatar_url) {
      const img = el("img", "avatar");
      img.src = person.avatar_url;
      img.alt = "";
      img.loading = "lazy";
      return img;
    }
    const who = (person && (person.name || person.login)) || "?";
    return el("span", "avatar", who.charAt(0).toUpperCase());
  };

  const badge = (state) => {
    const key = LABELS[state] ? state : "new";
    const b = el("span", "badge badge-" + key);
    if (key === "safe" || key === "fixed") b.appendChild(icon("check"));
    if (key === "attention") b.appendChild(icon("alert"));
    b.appendChild(document.createTextNode(LABELS[key]));
    return b;
  };

  function emptyState(title, text, button) {
    const wrap = el("div", "empty");
    const tile = el("span", "icon-tile");
    tile.appendChild(icon(failed ? "alert" : "repo"));
    wrap.appendChild(tile);
    wrap.appendChild(el("h2", "empty-title", title));
    if (text) wrap.appendChild(el("p", "empty-text", text));
    if (button) wrap.appendChild(button);
    return wrap;
  }

  function actionFor(c) {
    if (c.analysis_id) {
      const a = el("a", "btn btn-secondary btn-sm", "Open in chat");
      a.href = "/chat/" + encodeURIComponent(c.analysis_id);
      return a;
    }
    const b = el("button", "btn btn-secondary btn-sm", "Check this commit");
    b.type = "button";
    b.addEventListener("click", () => startCheck(c, b));
    return b;
  }

  function rowFor(c) {
    const author = c.author || {};
    const row = el("div", "commit-row");

    const main = el("div", "commit-main");
    const msg = el("div", "commit-message", c.message || "(no message)");
    msg.title = c.message || "";
    main.appendChild(msg);

    const meta = el("div", "commit-meta");
    const who = el("span", "commit-meta-item");
    who.appendChild(avatar(author));
    who.appendChild(el("span", "", author.name || author.login || "Unknown"));
    meta.appendChild(who);
    if (c.repo) {
      const repo = el("span", "commit-meta-item");
      repo.appendChild(icon("repo"));
      repo.appendChild(el("span", "", c.repo));
      meta.appendChild(repo);
    }
    if (c.time_label) meta.appendChild(el("span", "", c.time_label));
    if (c.short_sha) meta.appendChild(el("span", "commit-sha", c.short_sha));
    main.appendChild(meta);

    const end = el("div", "commit-end");
    end.appendChild(badge(c.state));
    end.appendChild(actionFor(c));

    row.appendChild(main);
    row.appendChild(end);
    return row;
  }

  function render() {
    listEl.setAttribute("aria-busy", "false");
    listEl.replaceChildren();

    if (failed) {
      const retry = el("button", "btn btn-secondary btn-sm", "Try again");
      retry.type = "button";
      retry.addEventListener("click", load);
      listEl.appendChild(emptyState("We could not load the commits", "Check your connection and try again.", retry));
      return;
    }

    const shown = commits.filter((c) =>
      (!filters.member || (c.author && c.author.login === filters.member)) &&
      (!filters.state || c.state === filters.state));

    if (!commits.length) {
      if (repo) {
        listEl.appendChild(emptyState("No commits yet", "This repository has no commits on its main branch yet."));
      } else {
        listEl.appendChild(emptyState("No commits yet",
          "Pick a repository above, or check one in chat, and its commits will show up here."));
        const a = el("a", "btn btn-secondary btn-sm", "Go to chat");
        a.href = "/chat";
        listEl.firstChild.appendChild(a);
      }
      return;
    }

    if (!shown.length) {
      const clear = el("button", "btn btn-secondary btn-sm", "Show everything");
      clear.type = "button";
      clear.addEventListener("click", () => {
        filters.member = "";
        filters.state = "";
        syncPills();
        render();
      });
      const text = filters.state
        ? "Nothing here is " + STATE_TEXT[filters.state] + " for this selection."
        : "This person has no commits yet.";
      listEl.appendChild(emptyState("No commits match", text, clear));
      return;
    }

    const list = el("div", "list");
    list.setAttribute("role", "list");
    shown.forEach((c) => {
      const row = rowFor(c);
      row.setAttribute("role", "listitem");
      list.appendChild(row);
    });
    listEl.appendChild(list);
  }

  function renderMembers() {
    const seen = new Map();
    commits.forEach((c) => {
      const a = c.author || {};
      if (a.login && !seen.has(a.login)) seen.set(a.login, a);
    });
    if (filters.member && !seen.has(filters.member)) filters.member = "";
    memberFilter.querySelectorAll(".filter-pill:not([data-member=''])").forEach((n) => n.remove());
    [...seen.values()].sort((x, y) => (x.name || x.login).localeCompare(y.name || y.login)).forEach((m) => {
      const b = el("button", "filter-pill");
      b.type = "button";
      b.dataset.member = m.login;
      b.appendChild(avatar(m));
      b.appendChild(el("span", "", (m.name || m.login).split(" ")[0]));
      memberFilter.appendChild(b);
    });
    syncPills();
  }

  function syncPills() {
    document.querySelectorAll("#member-filter .filter-pill").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.member === filters.member)));
    document.querySelectorAll("#state-filter .filter-pill").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.state === filters.state)));
  }

  async function startCheck(c, button) {
    if (!c.repo) { SF.toast("This commit is missing its repository."); return; }
    button.disabled = true;
    const original = button.textContent;
    button.textContent = "Starting";
    try {
      const res = await SF.api("/api/analyses", { method: "POST", body: { repo: c.repo, sha: c.sha } });
      location.href = "/chat/" + encodeURIComponent(res.id);
    } catch (err) {
      button.disabled = false;
      button.textContent = original;
      SF.toast(err.message || "We could not start the check. Try again.");
    }
  }

  async function load() {
    const run = ++loadRun;
    failed = false;
    listEl.setAttribute("aria-busy", "true");
    try {
      // One repository: fetch its commits fresh (this pulls them from GitHub). All: what is already pulled in.
      const data = await SF.api(repo ? "/api/repos/" + repoPath(repo) + "/commits" : "/api/commits");
      if (run !== loadRun) return;
      commits = (Array.isArray(data.commits) ? data.commits : []).map((c) => (repo && !c.repo ? { ...c, repo } : c));
    } catch (err) {
      if (run !== loadRun) return;
      failed = true;
      commits = [];
    }
    renderMembers();
    render();
  }

  async function loadRepos() {
    let saved = "";
    try { saved = localStorage.getItem(REPO_KEY) || ""; } catch (_) { /* storage blocked */ }
    try {
      const data = await SF.api("/api/repos");
      (data.repos || []).forEach((r) => {
        const o = el("option", "", r.full_name);
        o.value = r.full_name;
        repoSelect.appendChild(o);
      });
      if (saved && [...repoSelect.options].some((o) => o.value === saved)) repo = saved;
    } catch (_) {
      SF.toast("I could not load your repositories. Showing everything already checked.");
    }
    repoSelect.value = repo;
  }

  repoSelect.addEventListener("change", () => {
    repo = repoSelect.value;
    try { localStorage.setItem(REPO_KEY, repo); } catch (_) { /* storage blocked */ }
    filters.member = "";
    load();
  });

  document.getElementById("member-filter").addEventListener("click", (e) => {
    const b = e.target.closest(".filter-pill");
    if (!b) return;
    filters.member = b.dataset.member || "";
    syncPills();
    render();
  });
  document.getElementById("state-filter").addEventListener("click", (e) => {
    const b = e.target.closest(".filter-pill");
    if (!b) return;
    filters.state = b.dataset.state || "";
    syncPills();
    render();
  });

  loadRepos().then(load);
})();
