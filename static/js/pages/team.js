// Team commits screen: client-side filtering by member, state and search,
// a live overview panel, clickable rows and the "Sync commits" button.

(() => {
  const dataEl = document.getElementById("team-data");
  if (!dataEl) return;

  let data;
  try {
    data = JSON.parse(dataEl.textContent);
  } catch (err) {
    return;
  }

  const STATES = ["new", "safe", "fixed", "attention"];
  const commits = Array.isArray(data.commits) ? data.commits : [];
  const members = Array.isArray(data.members) ? data.members : [];
  const bugTypes = data.bug_types || {};
  const labels = data.state_labels || {};
  const modeQuery = data.mode ? `&mode=${encodeURIComponent(data.mode)}` : "";

  const $ = (sel) => document.querySelector(sel);
  const rows = Array.from(document.querySelectorAll("#team-rows .team-row"));
  const tiles = Array.from(document.querySelectorAll(".member-tile"));
  const chips = Array.from(document.querySelectorAll(".team-state"));
  const select = $("#team-member");
  const search = $("#team-q");
  const count = $("#team-count");
  const title = $("#team-commits-title");
  const table = $(".team-table");
  const empty = $("#team-empty");
  const clearBtn = $("#team-clear");
  const syncBtn = $("#team-sync");

  const toast = (msg) => window.SecureFix && window.SecureFix.toast(msg);

  const memberOf = (login) =>
    members.find((m) => m.login === login) || { login, name: login, avatar_url: `/avatar/${login}.svg` };

  const ordinal = (n) => {
    const v = n % 100;
    if (v >= 11 && v <= 13) return `${n}th`;
    return n + ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th");
  };

  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  // ---------- Filter state, seeded from the URL ----------

  const params = new URLSearchParams(location.search);
  const filters = {
    member: members.some((m) => m.login === params.get("member")) ? params.get("member") : "",
    state: STATES.includes(params.get("state")) ? params.get("state") : "",
    q: params.get("q") || "",
  };
  if (search) search.value = filters.q;

  function writeUrl() {
    const p = new URLSearchParams(location.search);
    filters.member ? p.set("member", filters.member) : p.delete("member");
    filters.state ? p.set("state", filters.state) : p.delete("state");
    filters.q.trim() ? p.set("q", filters.q.trim()) : p.delete("q");
    const qs = p.toString();
    try {
      history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
    } catch (err) {
      /* replaceState can fail in sandboxed previews; filtering still works */
    }
  }

  function matchesText(row, terms) {
    const hay = row.dataset.search || "";
    return terms.every((t) => hay.includes(t));
  }

  // ---------- Apply filters ----------

  function apply() {
    const terms = filters.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const stateCounts = { new: 0, safe: 0, fixed: 0, attention: 0 };
    let base = 0;
    let shown = 0;

    rows.forEach((row) => {
      const okMember = !filters.member || row.dataset.author === filters.member;
      const okText = matchesText(row, terms);
      if (okMember && okText) {
        base += 1;
        stateCounts[row.dataset.state] = (stateCounts[row.dataset.state] || 0) + 1;
      }
      const show = okMember && okText && (!filters.state || row.dataset.state === filters.state);
      row.hidden = !show;
      if (show) shown += 1;
    });

    // Chip counts reflect the person and search, so they say what each chip would show.
    chips.forEach((chip) => {
      const s = chip.dataset.state;
      chip.setAttribute("aria-pressed", String(s === filters.state));
      const n = chip.querySelector(".team-chip-count");
      if (n) n.textContent = s ? stateCounts[s] || 0 : base;
    });

    tiles.forEach((tile) => tile.setAttribute("aria-pressed", String(tile.dataset.member === filters.member)));
    if (select) select.value = filters.member;

    const who = filters.member ? memberOf(filters.member) : null;
    if (title) {
      title.textContent = who ? (who.login === data.me ? "Your commits" : `${who.name}'s commits`) : "All commits";
    }
    if (count) count.textContent = `Showing ${shown} of ${commits.length}`;
    if (table) table.hidden = shown === 0;
    if (empty) empty.hidden = shown !== 0;

    renderDetail(who);
    writeUrl();
  }

  // ---------- Right panel: overview for the selected person ----------

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function avatar(src) {
    const img = el("img", "avatar sm");
    img.src = src;
    img.alt = "";
    return img;
  }

  function miniItem(href, titleText, subText) {
    const li = el("li");
    const a = el("a", "team-mini-link");
    a.href = href;
    a.append(el("span", "team-mini-title", titleText), el("span", "faint team-small", subText));
    li.append(a);
    return li;
  }

  const SVG_NS = "http://www.w3.org/2000/svg";

  function icon(paths, width) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", width || "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    paths.forEach((d) => {
      const p = document.createElementNS(SVG_NS, "path");
      p.setAttribute("d", d);
      svg.append(p);
    });
    return svg;
  }

  // Dark task row for a commit that needs a human (circular icon well, check circle).
  function taskItem(href, titleText, subText) {
    const li = el("li", "task");
    const a = el("a", "team-task-link");
    a.href = href;
    const well = el("span", "task-well");
    well.setAttribute("aria-hidden", "true");
    well.append(icon(["M12 3 4 6v6c0 4.5 3.2 7.8 8 9 4.8-1.2 8-4.5 8-9V6z", "M12 9v4", "M12 16.5h.01"]));
    const body = el("span", "task-body");
    body.append(el("span", "task-title", titleText), el("span", "task-meta", subText));
    const check = el("span", "check is-fail");
    check.setAttribute("aria-hidden", "true");
    check.append(icon(["M12 6v7", "M12 18h.01"], "3"));
    a.append(well, body, check);
    li.append(a);
    return li;
  }

  function renderRing(list) {
    const ring = $("#team-ring");
    if (!ring) return;
    const proven = list.filter((c) => c.bug_type);
    const fixed = proven.filter((c) => c.state === "fixed").length;
    ring.style.setProperty("--value", proven.length ? (fixed / proven.length).toFixed(3) : "0");
    ring.setAttribute("aria-label", `${fixed} of ${proven.length} proven bugs fixed`);
    const num = $("#team-ring-num");
    const sub = $("#team-ring-sub");
    if (num) num.textContent = proven.length ? `${fixed}/${proven.length}` : "0";
    if (sub) sub.textContent = proven.length ? "bugs fixed" : "no bugs proven";
  }

  function renderDetail(who) {
    const list = who ? commits.filter((c) => c.author === who.login) : commits;
    const proven = list.filter((c) => c.bug_type).length;

    const titleEl = $("#team-detail-title");
    const sub = $("#team-detail-sub");
    const av = $("#team-detail-avatar");
    const bar = $("#team-detail-bar");
    if (!titleEl) return;

    titleEl.textContent = who ? (who.login === data.me ? `${who.name} (you)` : who.name) : "Everyone";
    sub.textContent = list.length
      ? `${plural(list.length, "commit")}, ${plural(proven, "bug")} proven`
      : "No commits yet";

    av.replaceChildren(...(who ? [who] : members.slice(0, 3)).map((m) => avatar(m.avatar_url)));
    av.classList.toggle("is-single", Boolean(who));

    const counts = { new: 0, safe: 0, fixed: 0, attention: 0 };
    list.forEach((c) => { counts[c.state] = (counts[c.state] || 0) + 1; });

    bar.replaceChildren(
      ...STATES.filter((s) => counts[s]).map((s) => {
        const seg = el("span", `state-bar-seg seg-${s}`);
        seg.style.flexGrow = counts[s];
        return seg;
      })
    );
    STATES.forEach((s) => {
      const n = document.querySelector(`[data-legend="${s}"]`);
      if (n) n.textContent = counts[s];
    });

    renderRing(list);

    const waiting = list.filter((c) => c.state === "attention");
    const waitingEl = $("#team-waiting");
    waitingEl.replaceChildren(
      ...(waiting.length
        ? waiting.map((c) => {
            const bug = bugTypes[c.bug_type];
            return taskItem(
              `/dashboard?commit=${encodeURIComponent(c.sha)}${modeQuery}`,
              c.message,
              `${bug ? bug.name : "Bug proven"}, auto-fix failed`
            );
          })
        : [el("li", "faint team-small", "Nothing waiting. Every proven bug has a verified fix.")])
    );

    const repeats = list.filter((c) => (c.repeat_count || 0) > 1);
    const repeatEl = $("#team-repeats");
    repeatEl.replaceChildren(
      ...(repeats.length
        ? repeats.map((c) => {
            const bug = bugTypes[c.bug_type];
            return miniItem(
              `/dashboard?commit=${encodeURIComponent(c.sha)}${modeQuery}`,
              `${bug ? bug.name : "Same bug"}, ${ordinal(c.repeat_count)} time`,
              `${memberOf(c.author).name} in ${c.sha}`
            );
          })
        : [el("li", "faint team-small", "No repeats yet.")])
    );
  }

  // ---------- Events ----------

  tiles.forEach((tile) =>
    tile.addEventListener("click", () => {
      const login = tile.dataset.member;
      // Clicking the selected person again goes back to everyone.
      filters.member = filters.member === login ? "" : login;
      apply();
    })
  );

  chips.forEach((chip) =>
    chip.addEventListener("click", () => {
      const s = chip.dataset.state;
      filters.state = filters.state === s ? "" : s;
      apply();
    })
  );

  if (select) {
    select.addEventListener("change", () => {
      filters.member = select.value;
      apply();
    });
  }

  if (search) {
    search.addEventListener("input", () => {
      filters.q = search.value;
      apply();
    });
    search.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && search.value) {
        search.value = "";
        filters.q = "";
        apply();
      }
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      filters.member = "";
      filters.state = "";
      filters.q = "";
      if (search) search.value = "";
      apply();
      if (search) search.focus();
    });
  }

  // Press "/" anywhere to jump to search.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    if (!search) return;
    e.preventDefault();
    search.focus();
  });

  // Whole row opens the report; the message link stays the keyboard target.
  const tbody = document.getElementById("team-rows");
  if (tbody) {
    tbody.addEventListener("click", (e) => {
      if (e.target.closest("a, button, input, select")) return;
      const row = e.target.closest(".team-row");
      if (!row || !row.dataset.href) return;
      const sel = window.getSelection && window.getSelection();
      if (sel && String(sel).length) return;
      if (e.ctrlKey || e.metaKey) {
        window.open(row.dataset.href, "_blank", "noopener");
      } else {
        location.href = row.dataset.href;
      }
    });
  }

  // ---------- Sync commits ----------

  if (syncBtn) {
    const label = syncBtn.querySelector(".team-sync-label");
    syncBtn.addEventListener("click", async () => {
      if (syncBtn.disabled) return;
      syncBtn.disabled = true;
      syncBtn.setAttribute("aria-busy", "true");
      syncBtn.classList.add("is-busy");
      if (label) label.textContent = "Syncing";

      let reload = false;
      try {
        if (!window.SecureFix || !window.SecureFix.api) throw new Error("App helpers missing");
        const res = await window.SecureFix.api("/api/commits/sync", { method: "POST" });
        const added = res && typeof res.added === "number" ? res.added : null;
        if (added === 0) {
          toast("Already up to date");
        } else {
          toast(added === null ? "Commits synced" : `${plural(added, "new commit")} synced`);
          reload = true;
        }
      } catch (err) {
        toast("Could not sync commits right now. Try again in a moment.");
      } finally {
        syncBtn.disabled = false;
        syncBtn.removeAttribute("aria-busy");
        syncBtn.classList.remove("is-busy");
        if (label) label.textContent = "Sync commits";
      }
      if (reload) setTimeout(() => location.reload(), 900);
    });
  }

  apply();
})();
