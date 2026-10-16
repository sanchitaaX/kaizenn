// Organization dashboard: summary, charts, ranked repositories, repository drawer and fix review.
//
// API (all JSON, see app/fleet_api.py):
//   GET  /api/fleet/owners                   -> organizations and authors for the owner picker
//   GET  /api/fleet/summary?owners=          -> {title, summary, teams, github, bands}
//   (every list, number and report below takes the same ?owners=a,b; empty means every owner)
//   GET  /api/fleet/repos?source=&q=&band=&team=&sort=&page=&min_score=
//   PUT  /api/fleet/settings                 -> {min_score, auto_scan}
//   GET  /api/fleet/repos/{id}               -> {repo, vulns}
//   GET  /api/fleet/repos/{id}/report.pdf    -> one repository's report
//   POST /api/fleet/vulns/{id}/fix           -> {fix, rows, stats, vuln, plain}
//   GET  /api/fleet/fixes/{id}               -> same shape
//   POST /api/fleet/fixes/{id}/proof         -> start the checks; GET polls them
//   POST /api/fleet/fixes/{id}/decision      -> {fix, message, repo}
//   POST|GET /api/fleet/sync                 -> progress row
// Every label from the server goes in with textContent.

(function () {
  const root = document.getElementById("dashboard");
  if (!root) return;
  const SF = window.Kaizen;
  const $ = (id) => document.getElementById(id);

  const OWNERS_KEY = "securefix.dashboard.owners";
  const S = { source: "all", q: "", band: "", team: "", sort: "risk", page: 1, summary: null, drawerRepo: null,
    minScore: 80, settingsLoaded: false, openRows: new Set(), owners: loadOwners() };
  const BAND_LABEL = { critical: "Critical", high: "High", medium: "Medium", low: "Low", clean: "Clean", unscanned: "Not scanned" };
  const SEV_LABEL = { critical: "Critical", high: "High", medium: "Medium", low: "Low" };
  const fmt = (n) => (n == null ? "-" : Number(n).toLocaleString("en-US"));

  // ------------------------------------------------------------------ tiny DOM helpers

  function h(tag, cls, kids) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (kids != null) (Array.isArray(kids) ? kids : [kids]).forEach((k) => {
      if (k == null || k === false) return;
      el.append(k instanceof Node ? k : document.createTextNode(String(k)));
    });
    return el;
  }
  const NS = "http://www.w3.org/2000/svg";
  function s(tag, attrs) {
    const el = document.createElementNS(NS, tag);
    Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, v));
    return el;
  }
  const PATHS = {
    arrow: '<path d="M5 12h14"/><path d="m13 6 6 6-6 6"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    alert: '<path d="M12 4 2.8 19.5h18.4z"/><path d="M12 10v4.5"/><path d="M12 17h.01"/>',
    external: '<path d="M14 4h6v6"/><path d="M20 4 11 13"/><path d="M18 14v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10"/>',
    x: '<path d="M6 6l12 12"/><path d="M18 6 6 18"/>',
    spark: '<path d="M12 3.5 13.9 9l5.6 1.9-5.6 1.9L12 18.5l-1.9-5.7L4.5 10.9 10.1 9z"/>',
    down: '<path d="M12 5v14"/><path d="m6 13 6 6 6-6"/>',
    download: '<path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M5 20h14"/>',
    up: '<path d="M12 19V5"/><path d="m6 11 6-6 6 6"/>',
    clip: '<path d="m20 11-8.2 8.2a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4L15 6.7"/>',
    book: '<path d="M4 5.5C4 4.7 4.7 4 5.5 4H11v16H5.5A1.5 1.5 0 0 1 4 18.5z"/><path d="M20 5.5c0-.8-.7-1.5-1.5-1.5H13v16h5.5a1.5 1.5 0 0 0 1.5-1.5z"/>',
  };
  function icon(name, size) {
    const el = s("svg", { class: "icon", width: size || 16, height: size || 16, viewBox: "0 0 24 24", "aria-hidden": "true", focusable: "false" });
    el.innerHTML = PATHS[name] || ""; // static markup only, never data
    return el;
  }
  function loadOwners() {
    try { return JSON.parse(localStorage.getItem(OWNERS_KEY) || "[]").filter((o) => typeof o === "string"); }
    catch (_) { return []; }
  }
  const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== "" && v != null)).toString();
  const bandColor = (b) => getComputedStyle(document.body).getPropertyValue("--band-" + (b || "unscanned")).trim();

  // ------------------------------------------------------------------ summary

  async function loadSummary() {
    $("board").classList.add("is-loading");
    let data;
    try {
      data = await SF.api("/api/fleet/summary?" + qs({ source: S.source, owners: S.owners.join(",") }));
    } catch (err) {
      SF.toast(err.message);
      return;
    } finally {
      $("board").classList.remove("is-loading");
    }
    S.summary = data.summary;
    applySettings(data.settings);
    $("org-title").textContent = data.title;
    $("report-btn").href = "/api/fleet/report.pdf?" + qs({ source: S.source, owners: S.owners.join(",") });
    if (data.github && data.github.sync) showSync(data.github.sync, false);
    const empty = !data.summary || !data.summary.repos_total;
    $("empty").hidden = !empty;
    $("board").hidden = empty;
    if (empty) {
      $("org-sub").textContent = "No repositories in this view yet.";
      return;
    }
    const sm = data.summary;
    $("org-sub").textContent = `${fmt(sm.repos_total)} repositories · last scan ${sm.last_scan_label.toLowerCase()}`;
    renderTiles(sm);
    renderTrend(sm.trend);
    renderBands(sm.bands, data.bands);
    renderTop(sm.top);
    renderTypes(sm.by_type);
    renderTeams(sm.by_team);
    renderSaved(sm.saved_fixes);
    fillTeams(data.teams);
  }

  function tile(label, value, unit) {
    const v = h("span", "tile-value", value);
    if (unit) v.append(h("small", null, unit));
    return h("div", "tile card", [h("span", "tile-label", label), v]);
  }

  function renderTiles(sm) {
    $("tiles").replaceChildren(
      tile("Overall risk", String(sm.org_score), "/100"),
      tile("Vulnerable Repos", fmt(sm.repos_at_risk)),
      tile("Existing Vulnerabilities", fmt(sm.open_total)),
      tile("Fixed in the last 30 days", fmt(sm.fixed_30d)),
    );
  }

  // ------------------------------------------------------------------ risk score setting and auto-scan

  const slider = $("min-score");
  const autoScan = $("auto-scan");

  function applySettings(st) {
    if (!st) return;
    if (!S.settingsLoaded) {
      S.settingsLoaded = true;
      if (st.min_score !== S.minScore) {
        setScore(st.min_score);
        S.page = 1;
        loadRepos();
      }
    }
    if (autoScan) autoScan.checked = !!st.auto_scan;
    const note = $("watch-note");
    if (note) {
      note.hidden = false;
      note.textContent = st.auto_scan
        ? `Auto-scan is on: new pushes are read within a minute.` +
          (st.last_watch_label ? ` Last check ${st.last_watch_label.toLowerCase()}${st.last_watch_note ? ": " + st.last_watch_note : "."}` : "")
        : "Auto-scan is off. Turn it on to read every new push automatically.";
    }
  }

  function setScore(n) {
    S.minScore = n;
    slider.value = n;
    $("min-score-out").textContent = n;
  }

  let saveTimer = null;
  slider.addEventListener("input", () => { $("min-score-out").textContent = slider.value; });
  slider.addEventListener("change", () => {
    S.minScore = Number(slider.value);
    S.page = 1;
    loadRepos();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      SF.api("/api/fleet/settings", { method: "PUT", body: { min_score: S.minScore } }).catch((err) => SF.toast(err.message));
    }, 300);
  });
  if (autoScan) {
    autoScan.addEventListener("change", async () => {
      try {
        applySettings(await SF.api("/api/fleet/settings", { method: "PUT", body: { auto_scan: autoScan.checked } }));
        SF.toast(autoScan.checked ? "Auto-scan on push is on." : "Auto-scan on push is off.");
      } catch (err) { SF.toast(err.message); autoScan.checked = !autoScan.checked; }
    });
  }

  // ------------------------------------------------------------------ trend (single series, crosshair hover)

  function renderTrend(points) {
    const box = $("trend");
    box.replaceChildren();
    if (!points || !points.length) return;
    const W = 640, H = 220, L = 32, R = 36, T = 14, B = 26;
    const max = Math.min(100, Math.max(50, Math.ceil((Math.max(...points.map((p) => p.score)) + 8) / 10) * 10));
    const x = (i) => L + (W - L - R) * (i / Math.max(1, points.length - 1));
    const y = (v) => T + (H - T - B) * (1 - v / max);
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img",
      "aria-label": `Overall risk went from ${points[0].score} to ${points[points.length - 1].score} over 12 weeks` });
    const defs = s("defs");
    const grad = s("linearGradient", { id: "trend-fill", x1: 0, y1: 0, x2: 0, y2: 1 });
    grad.append(s("stop", { offset: "0%", "stop-color": "#5e6ad2", "stop-opacity": ".16" }),
      s("stop", { offset: "100%", "stop-color": "#5e6ad2", "stop-opacity": "0" }));
    defs.append(grad);
    svg.append(defs);
    const step = max > 50 ? 25 : 10;
    for (let v = 0; v <= max; v += step) {
      svg.append(s("line", { class: "grid-line", x1: L, x2: W - R, y1: y(v), y2: y(v) }));
      const t = s("text", { class: "axis-text", x: L - 8, y: y(v) + 4, "text-anchor": "end" });
      t.textContent = v;
      svg.append(t);
    }
    points.forEach((p, i) => {
      if (i % 3 !== 0 && i !== points.length - 1) return;
      const t = s("text", { class: "axis-text", x: x(i), y: H - 6, "text-anchor": "middle" });
      t.textContent = i === points.length - 1 ? "Now" : p.label;
      svg.append(t);
    });
    const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.score).toFixed(1)}`).join(" ");
    svg.append(s("path", { class: "area", d: `${d} L${x(points.length - 1)},${y(0)} L${x(0)},${y(0)} Z` }));
    svg.append(s("path", { class: "line", d }));
    const last = points[points.length - 1];
    svg.append(s("circle", { class: "dot", cx: x(points.length - 1), cy: y(last.score), r: 4.5 }));
    const end = s("text", { class: "end-label", x: x(points.length - 1) + 8, y: y(last.score) + 4 });
    end.textContent = last.score;
    svg.append(end);
    const cross = s("line", { class: "cross", x1: 0, x2: 0, y1: T, y2: H - B, visibility: "hidden" });
    const hover = s("circle", { class: "dot", r: 5, visibility: "hidden" });
    svg.append(cross, hover);
    const hit = s("rect", { class: "hit", x: L, y: 0, width: W - L - R, height: H });
    svg.append(hit);
    box.append(svg);

    const tip = h("div", "tip");
    tip.hidden = true;
    box.append(tip);
    box.tabIndex = 0;
    let idx = points.length - 1;
    function show(i) {
      idx = Math.max(0, Math.min(points.length - 1, i));
      const p = points[idx];
      cross.setAttribute("x1", x(idx)); cross.setAttribute("x2", x(idx));
      hover.setAttribute("cx", x(idx)); hover.setAttribute("cy", y(p.score));
      cross.setAttribute("visibility", "visible"); hover.setAttribute("visibility", "visible");
      const rect = svg.getBoundingClientRect();
      tip.replaceChildren(h("strong", null, `${p.score} / 100`),
        h("span", null, [h("span", "tip-key"), `${idx === points.length - 1 ? "Now" : "Week of " + p.label} · ${fmt(p.open)} open`]));
      tip.style.left = (x(idx) / W) * rect.width + "px";
      tip.style.top = (y(p.score) / H) * rect.height - 10 + "px";
      tip.hidden = false;
    }
    function hide() {
      cross.setAttribute("visibility", "hidden"); hover.setAttribute("visibility", "hidden");
      tip.hidden = true;
    }
    hit.addEventListener("pointermove", (e) => {
      const rect = svg.getBoundingClientRect();
      const px = ((e.clientX - rect.left) / rect.width) * W;
      show(Math.round(((px - L) / (W - L - R)) * (points.length - 1)));
    });
    hit.addEventListener("pointerleave", hide);
    box.addEventListener("focus", () => show(idx));
    box.addEventListener("blur", hide);
    box.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") { show(idx - 1); e.preventDefault(); }
      if (e.key === "ArrowRight") { show(idx + 1); e.preventDefault(); }
    });
  }

  // ------------------------------------------------------------------ risk levels

  function renderBands(bands, meta) {
    const order = ["critical", "high", "medium", "low", "clean", "unscanned"];
    const total = order.reduce((n, k) => n + (bands[k] || 0), 0) || 1;
    const meaning = Object.fromEntries((meta || []).map((b) => [b.key, b.meaning]));
    const bar = $("bands");
    bar.replaceChildren();
    const legend = $("bands-legend");
    legend.replaceChildren();
    order.forEach((k) => {
      const n = bands[k] || 0;
      if (!n) return;
      const pct = (100 * n) / total;
      const seg = h("button", "band-seg" + (S.band === k ? " is-active" : ""));
      seg.type = "button";
      seg.dataset.band = k;
      seg.style.flex = `${n} 1 0`;
      seg.title = `${BAND_LABEL[k]}: ${fmt(n)} repositories (${Math.round(pct)}%)` + (meaning[k] ? `. ${meaning[k]}` : "");
      seg.setAttribute("aria-label", seg.title);
      if (pct >= 6) seg.append(h("span", null, fmt(n)));
      seg.addEventListener("click", () => filterBand(k));
      bar.append(seg);
      const b = h("button", null, [h("span", "band-swatch"), h("span", "band-name", BAND_LABEL[k]),
        h("span", "band-count", `${fmt(n)} · ${meaning[k] || "Code scanning is off, so risk is unknown."}`)]);
      b.type = "button";
      b.querySelector(".band-swatch").dataset.band = k;
      b.addEventListener("click", () => filterBand(k));
      legend.append(h("li", null, b));
    });
  }

  function filterBand(band) {
    S.band = S.band === band ? "" : band;
    $("f-band").value = S.band;
    if (S.band) setScore(0);
    S.page = 1;
    loadRepos();
    document.querySelectorAll(".band-seg").forEach((el) => el.classList.toggle("is-active", el.dataset.band === S.band));
    $("repos-section").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ------------------------------------------------------------------ top five, types, teams, saved

  function bandPill(b, label) {
    return h("span", "pill-band b-" + (b || "unscanned"), label || BAND_LABEL[b] || "Not scanned");
  }

  function renderTop(top) {
    $("top").replaceChildren(...(top || []).map((r, i) => {
      const card = h("button", "top-card", [
        h("span", "top-rank", `#${i + 1} riskiest`),
        h("span", "top-name", r.full_name.split("/").pop()),
        h("span", "top-team", r.team || r.full_name.split("/")[0]),
        h("span", "top-score", [h("strong", null, String(r.risk_score)), bandPill(r.band, r.band_label)]),
        h("span", "top-counts", `${fmt(r.open_critical)} critical · ${fmt(r.open_high)} high · ${fmt(r.open_total)} open`),
        h("span", "top-cta", ["Review and fix", icon("arrow", 14)]),
      ]);
      card.type = "button";
      card.addEventListener("click", () => openRepo(r.id));
      return card;
    }));
  }

  function renderTypes(types) {
    const list = (types || []).slice(0, 10);
    const max = Math.max(1, ...list.map((t) => t.total));
    $("types").replaceChildren(...list.map((t) => {
      const hi = t.critical + t.high, lo = t.medium + t.low;
      const track = h("div", "type-track");
      track.style.width = `${(100 * t.total) / max}%`;
      if (hi) { const seg = h("span", "type-seg sev-hi"); seg.style.flex = `${hi} 1 0`; track.append(seg); }
      if (lo) { const seg = h("span", "type-seg sev-lo"); seg.style.flex = `${lo} 1 0`; track.append(seg); }
      const row = h("div", "type-row", [h("span", "type-name", t.title), h("div", null, track), h("span", "type-total", fmt(t.total))]);
      row.title = `${t.title}: ${fmt(t.total)} open (${fmt(t.critical)} critical, ${fmt(t.high)} high, ${fmt(t.medium)} medium, ${fmt(t.low)} low)`;
      return row;
    }));
  }

  function renderTeams(teams) {
    $("teams").replaceChildren(...(teams || []).map((t) => {
      const tr = h("tr", null, [
        h("td", "t-name", t.team), h("td", "num", String(Math.round(t.avg_score || 0))),
        h("td", "num" + (t.at_risk ? " c-strong" : ""), fmt(t.at_risk)), h("td", "num", fmt(t.open || 0)),
      ]);
      return tr;
    }));
  }

  function renderSaved(saved) {
    $("saved-section").hidden = !saved || !saved.length;
    $("saved").replaceChildren(...(saved || []).map((f) => {
      const btn = h("button", "btn btn-secondary btn-sm", "Review");
      btn.type = "button";
      btn.addEventListener("click", () => openFix({ fixId: f.id }));
      return h("li", "saved-row", [
        h("span", "sev sev-" + f.severity, SEV_LABEL[f.severity]),
        h("div", "saved-main", [h("p", "saved-title", `${f.title} in ${f.full_name.split("/").pop()}`),
          h("p", "saved-meta", `${f.file}:${f.line} · saved ${f.created_label.toLowerCase()}`)]),
        btn,
      ]);
    }));
  }

  function fillTeams(teams) {
    const sel = $("f-team");
    const keep = sel.value;
    sel.replaceChildren(h("option", null, "All teams"));
    sel.firstChild.value = "";
    (teams || []).forEach((t) => { const o = h("option", null, t); o.value = t; sel.append(o); });
    sel.value = (teams || []).includes(keep) ? keep : "";
  }

  // ------------------------------------------------------------------ ranked table

  let reqId = 0;
  async function loadRepos() {
    const mine = ++reqId;
    const body = $("repos");
    body.style.opacity = ".55";
    let data;
    try {
      data = await SF.api("/api/fleet/repos?" + qs({ source: S.source, owners: S.owners.join(","), q: S.q, band: S.band,
        team: S.team, sort: S.sort, page: S.page, min_score: S.minScore }));
    } catch (err) {
      SF.toast(err.message);
      return;
    } finally {
      if (mine === reqId) body.style.opacity = "";
    }
    if (mine !== reqId) return;
    const filtered = S.q || S.band || S.team;
    $("f-clear").hidden = !filtered;
    const scope = S.minScore > 0 ? ` at risk score ${S.minScore} or above` : "";
    $("repos-count").textContent = `${fmt(data.total)} ${data.total === 1 ? "repository" : "repositories"}${scope}` +
      ` · ${fmt(data.major_total)} critical or high problems` + (filtered ? " (filtered)" : "");
    if (!data.items.length) {
      const tr = h("tr", "empty-row", h("td", null, S.minScore > 0
        ? `No repository scores ${S.minScore} or more here. Move the slider left to see more.`
        : "No repositories match. Try clearing the filters."));
      tr.firstChild.colSpan = COLS;
      body.replaceChildren(tr);
    } else {
      body.replaceChildren(...data.items.flatMap(repoRows));
    }
    syncExpandAll();
    renderPager(data);
  }

  const COLS = 11;

  function count(n, alert) {
    return h("td", "num" + (n ? (alert ? " c-strong" : "") : " zero"), n ? fmt(n) : "0");
  }

  // Each repository is a row that opens to show its critical and high problems (closed at first).
  function repoRows(r) {
    const meta = h("div", "repo-meta", [r.team, r.language, r.public ? "Public" : "Private"].filter(Boolean).map((t) => h("span", null, t)));
    if (r.source === "github") meta.prepend(h("span", "repo-src", "GitHub"));
    const fill = h("span", "risk-fill");
    fill.style.width = `${r.risk_score}%`;
    fill.style.background = bandColor(r.band);
    const riskCell = r.band === "unscanned"
      ? h("td", null, bandPill("unscanned"))
      : h("td", null, h("div", "risk-cell", [h("span", "risk-num", String(r.risk_score)), h("span", "risk-track", fill), bandPill(r.band, r.band_label)]));
    const caret = h("td", "caret", icon("chevron", 16));
    const tr = h("tr", "repo-row", [
      caret,
      h("td", "num rank", String(r.rank)),
      h("td", null, [h("div", "repo-name", r.full_name), meta]),
      riskCell,
      count(r.open_critical, true), count(r.open_high, true),
      h("td", "num hide-sm" + (r.open_medium ? "" : " zero"), fmt(r.open_medium)),
      h("td", "num hide-sm" + (r.open_low ? "" : " zero"), fmt(r.open_low)),
      h("td", "hide-sm muted", r.last_scan_label),
      h("td", "fix-all-cell", r.open_total ? fixAllButton(r) : null),
      h("td", "report-cell", reportLink(r)),
    ]);
    const cell = h("td", "detail-cell");
    cell.colSpan = COLS;
    const detail = h("tr", "detail-row", cell);
    detail.hidden = true;
    detail.id = `repo-detail-${r.id}`;
    tr.tabIndex = 0;
    tr.setAttribute("aria-controls", detail.id);
    tr.setAttribute("aria-label", `${r.full_name}, risk ${r.risk_score}, ${r.band_label}. Show problems.`);
    const setOpen = (on) => {
      tr.classList.toggle("is-open", on);
      detail.hidden = !on;
      tr.setAttribute("aria-expanded", String(on));
      if (on) { S.openRows.add(r.id); loadDetail(r, cell); } else S.openRows.delete(r.id);
      syncExpandAll();
    };
    tr._setOpen = setOpen;
    tr.addEventListener("click", () => setOpen(!tr.classList.contains("is-open")));
    tr.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(!tr.classList.contains("is-open")); }
    });
    if (S.openRows.has(r.id)) setOpen(true);
    else tr.setAttribute("aria-expanded", "false");
    return [tr, detail];
  }

  async function loadDetail(r, cell) {
    if (!cell.childElementCount) cell.append(h("div", "detail-loading", h("span", "spinner")));
    let data;
    try {
      data = await SF.api(`/api/fleet/repos/${r.id}`);
    } catch (err) {
      cell.replaceChildren(h("p", "fix-error", err.message));
      return;
    }
    const open = data.vulns.filter((v) => !CLOSED(v));
    const major = open.filter((v) => v.severity === "critical" || v.severity === "high");
    const list = h("ul", "major-vulns", major.map((v) => {
      const fixable = v.status === "open" || v.status === "fix_saved";
      const btn = fixable
        ? h("button", "btn btn-sm " + (v.fix_id ? "btn-secondary" : "btn-primary"), v.fix_id ? "Review fix" : ["Fix", icon("spark", 14)])
        : h("span", "vuln-state", [icon("check", 12), STATUS_TEXT[v.status] || v.status]);
      if (fixable) {
        btn.type = "button";
        btn.addEventListener("click", () => fixVuln(v));
      }
      return h("li", "major-vuln", [
        h("span", "sev sev-" + v.severity, SEV_LABEL[v.severity]),
        h("div", "major-vuln-main", [
          h("span", "major-vuln-title", [v.title, v.cwe ? h("span", "faint xs", v.cwe) : null]),
          h("span", "major-vuln-where", `${v.file}:${v.line} · found ${v.first_seen_label.toLowerCase()}`),
        ]),
        btn,
      ]);
    }));
    const all = h("button", "btn btn-ghost btn-sm", [`All ${fmt(open.length)} open problems`, icon("arrow", 14)]);
    all.type = "button";
    all.addEventListener("click", () => openRepo(r.id));
    const report = h("a", "btn btn-ghost btn-sm", [icon("download", 14), "Report"]);
    report.href = `/api/fleet/repos/${r.id}/report.pdf`;
    report.setAttribute("download", "");
    cell.replaceChildren(h("div", "detail", [
      briefBox(data.repo, data.brief_files),  // first: the brief shapes every fix below
      major.length ? list : h("p", "detail-empty", "No critical or high problems in this repository."),
      h("div", "detail-actions", [all, report]),
    ]));
  }

  function syncExpandAll() {
    const rows = [...document.querySelectorAll("#repos .repo-row")];
    const allOpen = rows.length > 0 && rows.every((t) => t.classList.contains("is-open"));
    const btn = $("expand-all");
    btn.hidden = !rows.length;
    btn.textContent = allOpen ? "Collapse all" : "Expand all";
  }
  $("expand-all").addEventListener("click", () => {
    const rows = [...document.querySelectorAll("#repos .repo-row")];
    const open = !rows.every((t) => t.classList.contains("is-open"));
    rows.forEach((t) => t._setOpen(open));
  });

  // Fix all for one repository happens in the assistant: it lists every problem as it is handled, then shows all
  // the fixes with Commit all (demo: the prepared fixes; GitHub: one agent run and its pull request).
  function fixAllButton(r) {
    const btn = h("button", "btn btn-primary btn-sm fix-all", [icon("spark", 14), "Fix all"]);
    btn.type = "button";
    btn.title = `Fix every open problem in ${r.full_name}`;
    btn.addEventListener("keydown", (e) => e.stopPropagation());
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      SF.openChat("/chat?embed=1&fixall=" + r.id);
    });
    return btn;
  }

  // The assistant tells the dashboard when Fix all committed something, so the numbers and the list catch up.
  window.addEventListener("message", (e) => {
    if (e.origin !== location.origin || !e.data || e.data.type !== "kaizen:fleet-changed") return;
    loadSummary();
    loadRepos();
    if (drawer.open && S.drawerRepo) openRepo(S.drawerRepo);
  });

  function reportLink(r) {
    const a = h("a", "btn btn-ghost btn-icon btn-sm report-link", icon("download", 16));
    a.href = `/api/fleet/repos/${r.id}/report.pdf`;
    a.setAttribute("download", "");
    a.title = `Download the report for ${r.full_name}`;
    a.setAttribute("aria-label", a.title);
    a.addEventListener("click", (e) => e.stopPropagation());
    a.addEventListener("keydown", (e) => e.stopPropagation());
    return a;
  }

  function renderPager(data) {
    const nav = $("pager");
    const from = data.total ? (data.page - 1) * data.per_page + 1 : 0;
    const to = Math.min(data.total, data.page * data.per_page);
    const btns = h("div", "pager-btns");
    const go = (p) => { S.page = p; loadRepos(); $("repos-section").scrollIntoView({ behavior: "smooth", block: "start" }); };
    const mk = (label, page, opts) => {
      const b = h("button", "btn btn-ghost btn-sm" + (opts && opts.current ? " is-current" : ""), label);
      b.type = "button";
      if (opts && opts.current) b.setAttribute("aria-current", "page");
      b.disabled = page < 1 || page > data.pages || (opts && opts.current);
      b.addEventListener("click", () => go(page));
      return b;
    };
    btns.append(mk("Previous", data.page - 1));
    const pages = new Set([1, data.pages, data.page - 1, data.page, data.page + 1].filter((p) => p >= 1 && p <= data.pages));
    let prev = 0;
    [...pages].sort((a, b) => a - b).forEach((p) => {
      if (p - prev > 1) btns.append(h("span", "faint", "…"));
      btns.append(mk(String(p), p, { current: p === data.page }));
      prev = p;
    });
    btns.append(mk("Next", data.page + 1));
    nav.replaceChildren(h("span", null, `Showing ${fmt(from)}–${fmt(to)} of ${fmt(data.total)}`), btns);
  }

  // ------------------------------------------------------------------ repository drawer

  const drawer = $("drawer");
  async function openRepo(id) {
    S.drawerRepo = id;
    $("drawer-report").href = `/api/fleet/repos/${id}/report.pdf`;
    $("drawer-title").textContent = "Loading…";
    $("drawer-team").textContent = "";
    $("drawer-sub").replaceChildren();
    $("drawer-body").replaceChildren(h("div", "fix-loading", h("span", "spinner")));
    if (!drawer.open) drawer.showModal();
    let data;
    try {
      data = await SF.api(`/api/fleet/repos/${id}`);
    } catch (err) {
      $("drawer-body").replaceChildren(h("p", "fix-error", err.message));
      return;
    }
    renderDrawer(data);
  }

  function renderDrawer(data) {
    const r = data.repo;
    $("drawer-team").textContent = r.team || "";
    $("drawer-title").textContent = r.full_name;
    const sub = [bandPill(r.band, r.band_label), h("span", null, `Risk ${r.risk_score}/100`),
      h("span", null, `· ${r.public ? "Public" : "Private"} · ${r.language || "Unknown language"} · scanned ${r.last_scan_label.toLowerCase()}`)];
    if (r.html_url && r.source === "github") {
      const a = h("a", null, ["GitHub", icon("external", 12)]);
      a.href = r.html_url; a.target = "_blank"; a.rel = "noopener noreferrer";
      sub.push(a);
    }
    $("drawer-sub").replaceChildren(...sub);
    const body = $("drawer-body");
    body.replaceChildren();
    const stat = (n, label) => h("div", "drawer-stat", [h("strong", null, fmt(n)), h("span", null, label)]);
    body.append(briefBox(r, data.brief_files));  // first: the brief shapes every fix below
    body.append(h("div", "drawer-stats", [stat(r.open_critical, "Critical"), stat(r.open_high, "High"),
      stat(r.open_medium, "Medium"), stat(r.open_low, "Low")]));
    if (r.scan_status === "no_code_scanning") {
      body.append(h("p", "fix-error", "Code scanning is not turned on for this repository, so KAIZEN cannot see its problems yet. Turn on CodeQL in the repository's Settings, Code security."));
    }
    const open = data.vulns.filter((v) => !CLOSED(v));
    const done = data.vulns.filter((v) => v.status === "fixed");
    const cleared = data.vulns.filter((v) => v.status === "dismissed");
    if (!open.length && r.scan_status === "scanned") {
      body.append(h("div", "empty", [h("span", "icon-tile", icon("check", 20)), h("p", "empty-title", "No open problems"),
        h("p", "empty-text", "Nothing to fix in this repository right now.")]));
    }
    if (open.length) body.append(h("p", "drawer-group", `Open problems (${open.length}), most serious first`));
    open.forEach((v) => body.append(vulnCard(v)));
    if (done.length) body.append(h("p", "drawer-group", `Fixed (${done.length})`));
    done.slice(0, 20).forEach((v) => body.append(vulnCard(v)));
    if (cleared.length) body.append(h("p", "drawer-group", `Not exploitable (${cleared.length}): the agent read the code and found no way in`));
    cleared.slice(0, 20).forEach((v) => body.append(vulnCard(v)));
  }

  // The owner's project brief (optional): a paragraph plus attached files (docs, notes, API descriptions, code).
  // KAIZEN sends it with every fix and agent run for this repository, so the AI understands what the code is for.
  const BRIEF_ACCEPT = ".txt,.md,.rst,.pdf,.docx,.json,.yaml,.yml,.toml,.ini,.csv,.sql,.py,.js,.ts,.tsx,.jsx,.html";
  const MAX_BRIEF_FILES = 5;

  function kb(n) { return n >= 1024 ? Math.round(n / 1024) + " KB" : n + " B"; }

  function briefBox(r, files) {
    const box = h("div", "brief");
    const saved = { brief: r.brief || "", files: (files || []).slice() };

    // One line once a brief exists (so the problems below stay in view); Show opens the text and the files.
    function summary(open) {
      const has = saved.brief || saved.files.length;
      box.classList.toggle("is-compact", !!has && !open);
      const edit = h("button", "btn btn-ghost btn-sm", has ? "Edit" : ["Add brief", icon("arrow", 14)]);
      edit.type = "button";
      edit.addEventListener("click", (e) => { e.stopPropagation(); form(); });
      if (!has) {
        box.replaceChildren(h("div", "brief-head", [h("div", null, [h("strong", null, "Project brief"),
          h("span", "brief-hint", " Optional: tell KAIZEN what this repository does and what must keep working")]), edit]));
        return;
      }
      const toggle = h("button", "btn btn-ghost btn-sm", open ? "Hide" : "Show");
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", String(!!open));
      toggle.addEventListener("click", (e) => { e.stopPropagation(); summary(!open); });
      const files = saved.files.length ? ` · ${saved.files.length} file${saved.files.length === 1 ? "" : "s"}` : "";
      const line = h("div", "brief-head", [
        h("div", "brief-line", [icon("check", 14), h("strong", null, "Project brief added"), h("span", "brief-hint", files),
          !open && saved.brief ? h("span", "brief-peek", saved.brief) : null]),
        h("div", "brief-tools", [toggle, edit, r.source === "github" ? h("span", "brief-push", pushButton()) : null])]);
      const kids = [line];
      if (open) {
        if (saved.brief) kids.push(h("p", "brief-text", saved.brief));
        if (saved.files.length) kids.push(h("ul", "brief-files", saved.files.map((f) => h("li", "brief-file", [icon("clip", 12), f.name, h("span", "faint", kb(f.size))]))));
        if (r.source === "github") kids.push(h("p", "brief-hint", "Push BRIEF.md: the AI turns the brief into a clear BRIEF.md for " +
          (r.default_branch || "main") + ", and you review it before it is committed."));
      }
      box.replaceChildren(...kids);
    }

    // BRIEF.md: the AI rewrites the paragraph and the attached files into a clean brief, the owner reviews and edits
    // it, and Push commits it to the default branch and opens it on GitHub.
    function pushButton() {
      const btn = h("button", "btn btn-ghost btn-sm", [icon("up", 14), "Push BRIEF.md"]);
      btn.title = "Push the brief to GitHub as BRIEF.md";
      btn.type = "button";
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        btn.disabled = true;
        btn.replaceChildren(h("span", "spinner"), "Writing BRIEF.md…");
        try {
          const draft = await SF.api(`/api/fleet/repos/${r.id}/brief/draft`, { method: "POST" });
          preview(draft);
        } catch (err) {
          SF.toast(err.message);
          btn.disabled = false;
          btn.replaceChildren(icon("up", 14), "Push BRIEF.md");
        }
      });
      return btn;
    }

    function preview(draft) {
      const ta = h("textarea", "input brief-md");
      ta.value = draft.body;
      ta.spellcheck = false;
      ta.setAttribute("aria-label", "BRIEF.md to push");
      ta.addEventListener("keydown", (e) => e.stopPropagation());
      const push = h("button", "btn btn-primary btn-sm", [icon("up", 14), "Push to GitHub"]);
      push.type = "button";
      const back = h("button", "btn btn-ghost btn-sm", "Cancel");
      back.type = "button";
      back.addEventListener("click", (e) => { e.stopPropagation(); summary(); });
      push.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!window.confirm(`Commit ${draft.path} to ${r.full_name} on ${draft.branch}?`)) return;
        push.disabled = true;
        // Open the tab now, while the click still counts as the user's action (browsers block pop-ups opened
        // after a wait), and send it to the file on GitHub once the push is done.
        const tab = window.open("", "_blank");
        if (tab) tab.document.write("<p style=\"font:15px system-ui;margin:2rem\">Pushing BRIEF.md to GitHub…</p>");
        try {
          const out = await SF.api(`/api/fleet/repos/${r.id}/brief/github`, { method: "POST", body: { body: ta.value } });
          SF.toast(out.unchanged ? "BRIEF.md on GitHub already says this. Opening it." : "BRIEF.md pushed to GitHub. Opening it.");
          if (tab && out.url) { tab.opener = null; tab.location.replace(out.url); } else if (tab) tab.close();
          summary();
          if (out.url) {
            const a = h("a", "btn btn-ghost btn-sm", ["View BRIEF.md on GitHub", icon("external", 12)]);
            a.href = out.url; a.target = "_blank"; a.rel = "noopener noreferrer";
            a.addEventListener("click", (ev) => ev.stopPropagation());
            const old = box.querySelector(".brief-push");
            if (old) old.replaceWith(a);
          }
        } catch (err) {
          if (tab) tab.close();
          if (/sign in with github/i.test(err.message)) {  // not connected to GitHub: go and sign in, then come back
            location.href = "/auth/login?next=" + encodeURIComponent("/dashboard?repo=" + r.id);
            return;
          }
          SF.toast(err.message);
          push.disabled = false;
        }
      });
      box.replaceChildren(
        h("div", "brief-head", [h("strong", null, draft.path + " to push"),
          h("span", "brief-hint", draft.ai ? "Written by the AI from your brief and files. Check and edit it before pushing."
            : "The AI was not available, so this is your brief laid out as it is. Edit it before pushing.")]),
        ta,
        h("div", "brief-actions", [h("span", "brief-hint", `Commits to ${r.full_name} on ${draft.branch}, then opens it on GitHub.`),
          h("span", "spacer"), back, push]));
      ta.focus();
    }

    function form() {
      let keep = saved.files.slice();
      let adding = [];
      const ta = h("textarea", "input");
      ta.maxLength = 4000;
      ta.value = saved.brief;
      ta.setAttribute("aria-label", "Project brief");
      ta.placeholder = "Describe the project in a paragraph: what it does, who uses it, how sign-in and permissions " +
        "work, and what must keep working. For example: a Flask API for school timetables; teachers sign in with " +
        "Google; students may only see their own class; the /export endpoint must keep returning CSV.";
      const list = h("ul", "brief-files");
      const picker = h("input");
      picker.type = "file"; picker.multiple = true; picker.accept = BRIEF_ACCEPT; picker.hidden = true;
      const attach = h("button", "btn btn-ghost btn-sm", [icon("clip", 14), "Attach files"]);
      attach.type = "button";
      const submit = h("button", "btn btn-primary btn-sm", "Submit");
      submit.type = "button";
      const cancel = h("button", "btn btn-ghost btn-sm", "Cancel");
      cancel.type = "button";
      const note = h("span", "brief-saved", "");

      function drawFiles() {
        const chip = (name, size, onRemove) => {
          const x = h("button", "brief-file-x", icon("x", 12));
          x.type = "button";
          x.setAttribute("aria-label", "Remove " + name);
          x.addEventListener("click", (e) => { e.stopPropagation(); onRemove(); drawFiles(); });
          return h("li", "brief-file", [icon("clip", 12), name, h("span", "faint", kb(size)), x]);
        };
        list.replaceChildren(...keep.map((f) => chip(f.name, f.size, () => { keep = keep.filter((k) => k !== f); })),
          ...adding.map((f) => chip(f.name, f.text.length, () => { adding = adding.filter((a) => a !== f); })));
        attach.disabled = keep.length + adding.length >= MAX_BRIEF_FILES;
      }

      attach.addEventListener("click", (e) => { e.stopPropagation(); picker.click(); });
      picker.addEventListener("change", async () => {
        const chosen = [...picker.files];
        picker.value = "";
        for (const file of chosen) {
          if (keep.length + adding.length >= MAX_BRIEF_FILES) { SF.toast(`Attach at most ${MAX_BRIEF_FILES} files.`); break; }
          note.textContent = "Reading " + file.name + "…";
          const fd = new FormData();
          fd.append("file", file);
          try {
            const out = await SF.api("/api/brief/extract?for_repo=1", { method: "POST", body: fd });
            adding.push({ name: out.filename || file.name, text: out.text });
          } catch (err) { SF.toast(file.name + ": " + err.message); }
        }
        note.textContent = "";
        drawFiles();
      });
      cancel.addEventListener("click", (e) => { e.stopPropagation(); summary(); });
      submit.addEventListener("click", async (e) => {
        e.stopPropagation();
        submit.disabled = true;
        note.textContent = "Saving…";
        try {
          const out = await SF.api(`/api/fleet/repos/${r.id}/brief`, { method: "PUT", body: {
            brief: ta.value, keep_files: keep.map((f) => f.id), add_files: adding } });
          saved.brief = out.brief; saved.files = out.files;
          r.brief = out.brief;
          SF.toast(out.brief || out.files.length ? "Project brief saved. Fixes for this repository will use it." : "Project brief cleared.");
          summary();
        } catch (err) { SF.toast(err.message); note.textContent = ""; submit.disabled = false; }
      });
      [ta, list, box].forEach((el) => el.addEventListener("click", (e) => e.stopPropagation()));
      ta.addEventListener("keydown", (e) => e.stopPropagation());

      box.replaceChildren(
        h("div", "brief-head", [h("strong", null, "Project brief"), h("span", "brief-hint", "Optional · a paragraph and up to 5 files (text, Markdown, PDF, Word, code)")]),
        ta, list, picker,
        h("div", "brief-actions", [attach, h("span", "spacer"), note, cancel, submit]));
      drawFiles();
      ta.focus();
    }

    summary();
    return box;
  }

  const STATUS_TEXT = { fix_saved: "Fix saved, waiting for review", pr_open: "Pull request open", fixed: "Fixed",
    dismissed: "Not exploitable: the agent checked it" };
  const CLOSED = (v) => v.status === "fixed" || v.status === "dismissed";

  function vulnCard(v) {
    const top = h("div", "vuln-top", [h("span", "sev sev-" + v.severity, SEV_LABEL[v.severity] || v.severity),
      h("span", "vuln-title", v.title), v.cwe ? h("span", "faint xs", v.cwe) : null]);
    const main = h("div", "vuln-main", [top, h("p", "vuln-plain", v.plain || v.description),
      h("p", "vuln-where", `${v.file}:${v.line} · found ${v.first_seen_label.toLowerCase()}`)]);
    if (STATUS_TEXT[v.status]) main.append(h("span", "vuln-state", [icon("check", 12), STATUS_TEXT[v.status]]));
    const card = h("div", "vuln" + (CLOSED(v) ? " is-fixed" : ""), main);
    if (v.status === "open" || v.status === "fix_saved") {
      const btn = h("button", "btn " + (v.status === "fix_saved" ? "btn-secondary" : "btn-primary") + " btn-sm",
        v.status === "fix_saved" ? "Review fix" : ["Fix", icon("spark", 14)]);
      btn.type = "button";
      btn.addEventListener("click", () => fixVuln(v));
      card.append(btn);
    }
    return card;
  }

  // Real code scanning alerts go to the security repair agent (it patches, tests and proves the fix);
  // demo repositories use the prepared fixes and the instant proof.
  async function fixVuln(v) {
    if (v.source === "demo") { openFix(v.fix_id ? { fixId: v.fix_id } : { vulnId: v.id }); return; }
    try {
      const run = await SF.api("/api/agent/runs", { method: "POST", body: { vuln_id: v.id } });
      if (drawer.open) drawer.close();
      SF.openChat("/chat?embed=1&run=" + run.id);
      SF.toast("The KAIZEN agent is fixing this alert. Follow it in the assistant.");
    } catch (err) { SF.toast(err.message); }
  }

  // ------------------------------------------------------------------ fix review

  const fixDlg = $("fix");
  async function openFix(ref) {
    $("fix-title").textContent = "Writing the fix…";
    $("fix-where").replaceChildren();
    $("fix-foot").replaceChildren();
    $("fix-body").replaceChildren(h("div", "fix-loading", [h("span", "spinner"),
      h("p", null, "KAIZEN is reading the code and writing a fix. This can take a few seconds for real repositories.")]));
    if (!fixDlg.open) fixDlg.showModal();
    let data;
    try {
      data = ref.fixId ? await SF.api(`/api/fleet/fixes/${ref.fixId}`)
        : await SF.api(`/api/fleet/vulns/${ref.vulnId}/fix`, { method: "POST" });
    } catch (err) {
      $("fix-title").textContent = "No fix this time";
      $("fix-body").replaceChildren(h("p", "fix-error", err.message));
      const close = h("button", "btn btn-secondary", "Close");
      close.type = "button";
      close.addEventListener("click", () => fixDlg.close());
      $("fix-foot").replaceChildren(h("span", "fix-foot-note"), close);
      return;
    }
    renderFix(data);
  }

  // What to remember about this kind of problem, and where to read and practise (OWASP, PortSwigger, CWE).
  function learnBox(learning) {
    if (!learning || !(learning.resources || []).length) return null;
    const links = learning.resources.map((r) => {
      const a = h("a", "learn-res", [h("span", "learn-res-kind", r.kind), h("span", "learn-res-title", r.title),
        h("span", "learn-res-src", r.source), icon("external", 12)]);
      a.href = r.url; a.target = "_blank"; a.rel = "noopener noreferrer";
      return h("li", null, a);
    });
    return h("section", "learn-box", [h("h3", null, [icon("book", 14), "Learn from this fix"]),
      h("ul", "learn-remember", (learning.remember || []).map((t) => h("li", null, t))),
      h("ul", "learn-links", links)]);
  }

  function renderFix(data) {
    const v = data.vuln, f = data.fix;
    $("fix-title").textContent = v.title;
    $("fix-where").replaceChildren(h("span", "sev sev-" + v.severity, SEV_LABEL[v.severity]),
      h("span", null, v.full_name), h("code", null, `${v.file}:${v.line}`), v.cwe ? h("span", "faint", v.cwe) : null);

    const explain = h("div", "fix-explain", [
      h("div", "explain is-bad", [h("h3", null, [icon("alert", 14), "What is wrong"]), h("p", null, data.plain),
        v.description && v.description !== data.plain ? h("p", "muted", v.description) : null]),
      h("div", "explain is-good", [h("h3", null, [icon("check", 14), "What the fix does"]), h("p", null, f.explanation)]),
    ]);

    const table = h("table", "diff2-table");
    const cols = h("colgroup");
    ["46px", "", "46px", ""].forEach((w) => { const c = h("col"); if (w) c.style.width = w; cols.append(c); });
    table.append(cols);
    const tbody = h("tbody");
    data.rows.forEach((r) => {
      const oldCls = r.kind === "change" || r.kind === "del" ? "is-old" : r.kind === "add" ? "is-blank" : "";
      const newCls = r.kind === "change" || r.kind === "add" ? "is-new" : r.kind === "del" ? "is-blank" : "";
      // Changed lines show the exact words that changed (parts), other lines show plain text.
      const code = (text, cls, mark, parts) => {
        const td = h("td", cls);
        if (text == null) return td;
        if (mark) td.append(h("span", "mark", mark));
        if (parts && parts.length) parts.forEach(([t, changed]) => td.append(changed ? h("mark", "word", t) : t));
        else td.append(text || " ");
        return td;
      };
      tbody.append(h("tr", null, [
        h("td", "ln " + oldCls, r.old_no != null ? String(r.old_no) : ""),
        code(r.old, "code-old " + oldCls, oldCls === "is-old" ? "−" : " ", r.old_parts),
        h("td", "ln ln-new " + newCls, r.new_no != null ? String(r.new_no) : ""),
        code(r.new, "code-new " + newCls, newCls === "is-new" ? "+" : " ", r.new_parts),
      ]));
    });
    table.append(tbody);
    const nums = (key) => data.rows.map((r) => r[key]).filter((n) => n != null);
    const range = (key) => { const n = nums(key); return n.length ? `lines ${n[0]}–${n[n.length - 1]}` : "no lines"; };
    const changedRows = data.rows.filter((r) => r.kind !== "same").length;
    const diff = h("div", "diff2", [
      h("div", "diff2-file", [h("code", null, v.file), h("span", null, `${data.rows.length} lines shown · ${changedRows} changed · flagged line ${v.line}`)]),
      h("div", "diff2-head", [
        h("div", null, [h("strong", null, "Current code"), h("span", "diff2-range", range("old_no")), h("span", "del-count", `−${data.stats.removed} lines`)]),
        h("div", null, [h("strong", null, "With the fix"), h("span", "diff2-range", range("new_no")), h("span", "add-count", `+${data.stats.added} lines`)])]),
      h("div", "diff2-body", table),
      h("div", "diff2-legend", [h("span", "lg lg-old", "Removed or changed"), h("span", "lg lg-new", "Added or changed"),
        h("span", "lg lg-word", "Exact words that changed")]),
    ]);
    const proofBox = h("section", "proof", [h("div", "proof-head", [h("h3", null, "Proof the fix is safe"),
      h("span", "proof-verdict")]), h("ul", "proof-list"), h("p", "proof-note")]);
    $("fix-body").replaceChildren(explain, diff, proofBox, learnBox(data.learning));

    const demo = data.source === "demo";
    const note = h("span", "fix-foot-note", demo
      ? "Commit records the fix on a new branch and closes the problem. Save keeps it for later. Discard throws it away."
      : "Commit opens a pull request on GitHub so a teammate can review it. Save keeps it for later. Discard throws it away.");
    const discard = h("button", "btn btn-ghost btn-danger-text", "Discard");
    const save = h("button", "btn btn-secondary", f.status === "saved" ? "Keep saved" : "Save for later");
    const commit = h("button", "btn btn-primary", [icon("check", 16), demo ? "Commit fix" : "Commit as pull request"]);
    [discard, save, commit].forEach((b) => { b.type = "button"; });
    save.addEventListener("click", () => (f.status === "saved" ? fixDlg.close() : decide(f.id, "save", [discard, save, commit])));
    discard.addEventListener("click", () => decide(f.id, "discard", [discard, save, commit]));
    commit.addEventListener("click", () => decide(f.id, "commit", [discard, save, commit], v));
    $("fix-foot").replaceChildren(note, discard, save, commit);
    watchProof(f.id, data.proof, proofBox, commit);
  }

  // ------------------------------------------------------------------ proof

  const PROOF_ICON = { pass: "check", fail: "x", skip: "dash" };
  const VERDICT = {
    proven: ["All checks passed", "is-good"],
    partial: ["Passed what could be checked", "is-partial"],
    broken: ["This fix breaks something", "is-bad"],
  };
  let proofTimer = null;

  function testList(label, run) {
    if (!run) return null;
    const names = (run.tests || []).map((t) => h("li", t.ok ? "is-pass" : "is-fail", [t.ok ? "✓ " : "✕ ", t.name]));
    const kids = [h("span", "proof-run-title", `${label}: ${fmt(run.passed)} passed, ${fmt(run.failed)} failed` +
      (run.timed_out ? " (time limit reached)" : ""))];
    if (names.length) kids.push(h("ul", "proof-tests", names));
    if (run.output) {
      const d = h("details", "proof-output", [h("summary", null, "Test output"), h("pre", null, run.output)]);
      kids.push(d);
    }
    return h("div", "proof-run", kids);
  }

  function renderProof(box, proof, commit) {
    const list = box.querySelector(".proof-list");
    const verdict = box.querySelector(".proof-verdict");
    const noteEl = box.querySelector(".proof-note");
    const running = !proof || proof.status === "running";
    const checks = (proof && proof.checks) || [];
    list.replaceChildren(...checks.map((c) => {
      const main = h("div", "proof-main", [
        h("span", "proof-label", [c.label, h("span", "proof-status", { pass: "Passed", fail: "Failed", skip: "Not run", warn: "Warning" }[c.status] || c.status)]),
        h("span", "proof-detail", c.detail),
      ]);
      if (c.method) main.append(h("p", "proof-method", [h("strong", null, "How it was checked: "), c.method]));
      if (c.evidence && c.evidence.length) main.append(h("ul", "proof-evidence", c.evidence.map((e) => h("li", null, e))));
      if (c.table && c.table.length) {
        const mark = (ok) => ok == null ? "–" : ok ? "✓ Pass" : "✕ Fail";
        main.append(h("table", "proof-table", [
          h("thead", null, h("tr", null, [h("th", null, "Test"), h("th", null, "Old code"), h("th", null, "Fixed code")])),
          h("tbody", null, c.table.map((t) => h("tr", null, [h("td", null, t.name),
            h("td", t.before ? "is-pass" : "is-fail", mark(t.before)), h("td", t.after ? "is-pass" : "is-fail", mark(t.after))]))),
        ]));
      } else if (c.key === "tests" && (c.before || c.after)) {
        main.append(h("div", "proof-runs", [testList("Before the fix", c.before), testList("After the fix", c.after)]));
      }
      return h("li", "proof-check is-" + c.status, [
        h("span", "proof-icon", c.status === "skip" ? "–" : icon(PROOF_ICON[c.status] || "check", 14)), main]);
    }));
    if (running) {
      list.append(h("li", "proof-check is-running", [h("span", "proof-icon", h("span", "spinner")),
        h("div", "proof-main", h("span", "proof-label", (proof && proof.note) || "Running the checks"))]));
      verdict.className = "proof-verdict is-running";
      verdict.textContent = "Checking…";
    } else {
      const [text, cls] = VERDICT[proof.verdict] || VERDICT.partial;
      verdict.className = "proof-verdict " + cls;
      verdict.textContent = text;
    }
    noteEl.textContent = proof && proof.status === "done" && proof.seconds != null ? `Checked in ${proof.seconds} s` + (proof.note ? ` · ${proof.note}` : "") : "";
    if (commit) {
      const broken = proof && proof.verdict === "broken";
      commit.disabled = running || broken;
      commit.title = running ? "Wait for the checks to finish" : broken ? "The checks failed, so this fix cannot be committed" : "";
    }
  }

  async function watchProof(fixId, proof, box, commit) {
    clearTimeout(proofTimer);
    if (!proof || proof.status === "none") {
      try { proof = await SF.api(`/api/fleet/fixes/${fixId}/proof`, { method: "POST" }); }
      catch (err) { renderProof(box, { status: "done", verdict: "partial", checks: [], note: err.message }, commit); return; }
    }
    renderProof(box, proof, commit);
    const poll = async () => {
      if (!fixDlg.open || !box.isConnected) return;
      try { proof = await SF.api(`/api/fleet/fixes/${fixId}/proof`); } catch (_) { return; }
      renderProof(box, proof, commit);
      if (proof.status === "running") proofTimer = setTimeout(poll, 1200);
    };
    if (proof.status === "running") proofTimer = setTimeout(poll, 900);
  }

  async function decide(fixId, action, buttons, vuln) {
    buttons.forEach((b) => { b.disabled = true; });
    const before = S.summary && S.summary.org_score;
    let out;
    try {
      out = await SF.api(`/api/fleet/fixes/${fixId}/decision`, { method: "POST", body: { action } });
    } catch (err) {
      SF.toast(err.message);
      buttons.forEach((b) => { b.disabled = false; });
      return;
    }
    await Promise.all([loadSummary(), loadRepos()]);
    if (drawer.open && S.drawerRepo === out.repo.id) openRepo(out.repo.id);
    if (action !== "commit") {
      SF.toast(out.message);
      fixDlg.close();
      return;
    }
    const after = S.summary && S.summary.org_score;
    const kids = [h("span", "icon-tile", icon("check", 26)), h("h3", null, "Fix committed"), h("p", null, out.message)];
    if (out.fix.pr_url) {
      const a = h("a", "btn btn-secondary", ["View pull request", icon("external", 14)]);
      a.href = out.fix.pr_url; a.target = "_blank"; a.rel = "noopener noreferrer";
      kids.push(a);
    }
    kids.push(h("p", "small", `${vuln.full_name} now has ${fmt(out.repo.open_total)} open problems and a risk of ${out.repo.risk_score} (${out.repo.band_label}).`));
    if (before != null && after != null && before !== after) {
      kids.push(h("div", "score-move", [h("span", "from", String(before)), icon("arrow", 22), h("span", null, `${after} overall`)]));
    }
    $("fix-body").replaceChildren(h("div", "fix-done", kids));
    const close = h("button", "btn btn-primary", "Done");
    close.type = "button";
    close.addEventListener("click", () => fixDlg.close());
    $("fix-foot").replaceChildren(h("span", "fix-foot-note"), close);
  }

  // Close buttons and backdrop clicks for both dialogs.
  [drawer, fixDlg].forEach((dlg) => {
    dlg.addEventListener("click", (e) => {
      if (e.target.closest("[data-close]") || e.target === dlg) dlg.close();
    });
  });

  // ------------------------------------------------------------------ GitHub scan

  let syncTimer = null;
  function showSync(st, live) {
    const bar = $("sync-bar");
    if (!st || st.status === "idle") { bar.hidden = true; return; }
    if (st.status !== "running" && !live) { bar.hidden = true; return; }
    bar.hidden = false;
    bar.classList.toggle("is-done", st.status !== "running");
    const pct = st.total ? Math.round((100 * st.done) / st.total) : 4;
    $("sync-fill").style.width = pct + "%";
    $("sync-text").textContent = st.status === "running"
      ? (st.total ? `Reading your GitHub: ${fmt(st.done)} of ${fmt(st.total)} repositories` : "Finding your GitHub repositories…")
      : (st.message || "Done.");
    if (st.status === "running" && !syncTimer) pollSync();
  }

  function pollSync() {
    syncTimer = setTimeout(async () => {
      syncTimer = null;
      let st;
      try { st = await SF.api("/api/fleet/sync"); } catch (_) { return; }
      showSync(st, true);
      if (st.status === "running") return;
      if ($("sync-btn")) $("sync-btn").disabled = false;
      loadSummary();
      loadRepos();
      setTimeout(() => { $("sync-bar").hidden = true; }, 6000);
    }, 1500);
  }

  const syncBtn = $("sync-btn");
  if (syncBtn) {
    syncBtn.addEventListener("click", async () => {
      syncBtn.disabled = true;
      try {
        showSync(await SF.api("/api/fleet/sync", { method: "POST" }), true);
      } catch (err) {
        SF.toast(err.message);
        syncBtn.disabled = false;
      }
    });
  }

  // ------------------------------------------------------------------ filters and source

  let typing = null;
  $("f-q").addEventListener("input", (e) => {
    clearTimeout(typing);
    typing = setTimeout(() => { S.q = e.target.value.trim(); S.page = 1; loadRepos(); }, 250);
  });
  $("f-band").addEventListener("change", (e) => {
    S.band = e.target.value; S.page = 1;
    if (S.band) setScore(0);
    loadRepos();
    document.querySelectorAll(".band-seg").forEach((el) => el.classList.toggle("is-active", el.dataset.band === S.band));
  });
  $("f-team").addEventListener("change", (e) => { S.team = e.target.value; S.page = 1; loadRepos(); });
  $("f-sort").addEventListener("change", (e) => { S.sort = e.target.value; S.page = 1; loadRepos(); });
  $("f-clear").addEventListener("click", () => {
    S.q = S.band = S.team = ""; S.page = 1;
    $("f-q").value = ""; $("f-band").value = ""; $("f-team").value = "";
    document.querySelectorAll(".band-seg").forEach((el) => el.classList.remove("is-active"));
    loadRepos();
  });

  // ------------------------------------------------------------------ owner picker

  // Pick organizations and authors; the numbers, the table and Download report then count only theirs.
  const ownerBtn = $("owner-btn");
  const ownerMenu = $("owner-menu");
  let ownerList = [];
  let draft = new Set();

  function ownerLabel() {
    const n = S.owners.length;
    const names = S.owners.map((o) => (ownerList.find((x) => x.owner === o) || {}).label || o);
    $("owner-label").textContent = !n ? "All organizations and authors" : n <= 2 ? names.join(", ") : `${n} organizations and authors`;
    ownerBtn.classList.toggle("is-set", n > 0);
  }

  function renderOwners() {
    const q = $("owner-search").value.trim().toLowerCase();
    const shown = ownerList.filter((o) => !q || o.owner.toLowerCase().includes(q) || o.label.toLowerCase().includes(q));
    $("owner-list").replaceChildren(...(shown.length ? shown.map((o) => {
      const box = h("input");
      box.type = "checkbox";
      box.checked = draft.has(o.owner);
      box.addEventListener("change", () => { if (box.checked) draft.add(o.owner); else draft.delete(o.owner); syncOwnerAll(); });
      return h("label", "owner-row", [box, h("span", "owner-name", [o.label, o.label !== o.owner ? h("span", "owner-sub", o.owner) : null,
        o.mine ? h("span", "owner-you", "You") : null]), h("span", "owner-count", `${fmt(o.repos)} repos`)]);
    }) : [h("p", "owner-empty", "No organization or author matches.")]));
    syncOwnerAll();
  }

  function syncOwnerAll() {
    const all = $("owner-all");
    all.checked = ownerList.length > 0 && ownerList.every((o) => draft.has(o.owner));
    all.indeterminate = draft.size > 0 && !all.checked;
    $("owner-all-count").textContent = `${fmt(ownerList.length)}`;
    $("owner-picked").textContent = !draft.size || all.checked ? "Every organization and author" : `${fmt(draft.size)} selected`;
    $("owner-apply").disabled = draft.size === 0;
  }

  async function openOwners() {
    ownerMenu.hidden = false;
    ownerBtn.setAttribute("aria-expanded", "true");
    draft = new Set(S.owners.length ? S.owners : ownerList.map((o) => o.owner));
    renderOwners();
    $("owner-search").focus();
    try {
      ownerList = (await SF.api("/api/fleet/owners?" + qs({ source: S.source }))).owners;
      if (!S.owners.length) draft = new Set(ownerList.map((o) => o.owner));
      renderOwners();
    } catch (err) { SF.toast(err.message); }
  }

  function closeOwners() {
    ownerMenu.hidden = true;
    ownerBtn.setAttribute("aria-expanded", "false");
  }

  ownerBtn.addEventListener("click", () => (ownerMenu.hidden ? openOwners() : closeOwners()));
  $("owner-search").addEventListener("input", renderOwners);
  $("owner-all").addEventListener("change", (e) => {
    draft = e.target.checked ? new Set(ownerList.map((o) => o.owner)) : new Set();
    renderOwners();
  });
  $("owner-apply").addEventListener("click", () => {
    const every = ownerList.length > 0 && ownerList.every((o) => draft.has(o.owner));
    S.owners = every ? [] : [...draft];
    try { localStorage.setItem(OWNERS_KEY, JSON.stringify(S.owners)); } catch (_) { /* private window */ }
    closeOwners();
    ownerLabel();
    S.page = 1; S.team = ""; $("f-team").value = "";
    S.openRows.clear();
    loadSummary();
    loadRepos();
  });
  document.addEventListener("mousedown", (e) => {
    if (!ownerMenu.hidden && !$("owner-pick").contains(e.target)) closeOwners();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !ownerMenu.hidden) { closeOwners(); ownerBtn.focus(); }
  });
  SF.api("/api/fleet/owners?" + qs({ source: S.source })).then((d) => {
    ownerList = d.owners;
    const known = new Set(ownerList.map((o) => o.owner));
    if (S.owners.some((o) => !known.has(o))) {  // a saved pick that no longer exists: show everything
      S.owners = S.owners.filter((o) => known.has(o));
      loadSummary();
      loadRepos();
    }
    ownerLabel();
  }).catch(() => {});
  ownerLabel();

  loadSummary();
  loadRepos();

  // New pushes are read in the background; refresh the numbers every minute while the page is open.
  setInterval(() => {
    if (document.hidden || drawer.open || fixDlg.open) return;
    loadSummary();
    loadRepos();
  }, 60000);

  // /dashboard?repo=<id> (from the chat) opens that repository straight away.
  const wanted = Number(new URLSearchParams(location.search).get("repo"));
  if (wanted) openRepo(wanted);
})();
