/* Learning screen: loads /api/learning and draws one card per bug type. */
(function () {
  "use strict";

  var root = document.getElementById("learning");
  if (!root) return;

  var WORDS = ["", "once", "twice", "three times", "four times", "five times", "six times"];

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function icon(id) {
    var t = document.getElementById("ico-" + id);
    return t ? t.content.firstElementChild.cloneNode(true) : document.createTextNode("");
  }

  function howOften(count) {
    var w = WORDS[count] || (count + " times");
    return "You have made this " + w + ".";
  }

  function kindInfo(kind) {
    var k = String(kind || "").toLowerCase();
    if (k.indexOf("video") === 0) return { icon: "play", label: "Video" };
    if (k.indexOf("practice") === 0 || k.indexOf("lab") !== -1) return { icon: "lab", label: "Practice lab" };
    if (k.indexOf("reference") === 0) return { icon: "guide", label: "Reference" };
    return { icon: "guide", label: "Guide" };
  }

  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  var FIRST_TOPICS = 4;  // the most frequent topics first; the rest behind "Show more"

  // One card per kind of problem found in the person's own repositories: where, how many fixed, what to study.
  function repoCard(t) {
    var c = el("article", "card learn-card");
    var head = el("div", "learn-head");
    var left = el("div");
    var title = el("h2", "learn-name", t.name + " ");
    if (t.cwe) {
      var cwe = el(safeUrl(t.cwe_url) ? "a" : "span", "learn-cwe", t.cwe);
      if (safeUrl(t.cwe_url)) { cwe.href = t.cwe_url; cwe.target = "_blank"; cwe.rel = "noopener noreferrer"; cwe.title = "The " + t.cwe + " definition at MITRE"; }
      title.appendChild(cwe);
    }
    left.appendChild(title);
    if (t.plain) left.appendChild(el("p", "learn-plain", t.plain));
    var where = (t.repos || []).map(function (r) { return r.repo.split("/")[1] + " " + r.count; }).join(", ");
    var more = t.repo_count > (t.repos || []).length ? " and " + (t.repo_count - t.repos.length) + " more" : "";
    left.appendChild(el("p", "learn-count", "Found " + plural(t.total, "time", "times") + " in " +
      plural(t.repo_count, "repository", "repositories") + ": " + where + more + "."));
    var done = [];
    if (t.fixed) done.push(plural(t.fixed, "fixed", "fixed"));
    if (t.in_review) done.push(t.in_review + " waiting for review");
    if (t.agent_fixed) done.push(plural(t.agent_fixed, "fix", "fixes") + " by the KAIZEN agent");
    if (done.length) left.appendChild(el("p", "learn-count learn-done", "Progress: " + done.join(" · ") + "."));
    head.appendChild(left);
    var link = el("a", "learn-link", "See them ");
    link.href = "/dashboard";
    link.appendChild(icon("arrow"));
    head.appendChild(link);
    c.appendChild(head);

    if (t.unconfirmed) {
      var note = el("p", "learn-note");
      note.appendChild(icon("alert"));
      note.appendChild(el("span", null, (t.unconfirmed === t.total ? "All of these were" : t.unconfirmed + " of these were") +
        " flagged by KAIZEN's pattern scan and are not confirmed yet. Use Fix on the dashboard to have the agent check them."));
      c.appendChild(note);
    }
    if ((t.remember || []).length) {
      var box = el("div", "learn-remember");
      box.appendChild(el("h3", "learn-sub", "Remember"));
      var ul = el("ul");
      t.remember.forEach(function (x) { ul.appendChild(el("li", null, x)); });
      box.appendChild(ul);
      c.appendChild(box);
    }
    lessons(c, t.resources || []);
    return c;
  }

  // A YouTube video id from a watch / youtu.be link (for the thumbnail), or "".
  function youtubeId(url) {
    var m = /(?:youtube\.com\/watch\?(?:.*&)?v=|youtu\.be\/)([A-Za-z0-9_-]{11})/.exec(url || "");
    return m ? m[1] : "";
  }

  function thumbnailOf(r) {
    if (safeUrl(r.thumbnail)) return r.thumbnail;
    var id = youtubeId(r.url);
    return id ? "https://i.ytimg.com/vi/" + id + "/hqdefault.jpg" : "";
  }

  // A video lesson as a preview: its thumbnail with a play button, the title and the channel.
  function videoCard(r) {
    var a = el("a", "learn-video");
    a.href = safeUrl(r.url);
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    var shot = el("span", "learn-video-shot");
    var img = el("img");
    img.src = thumbnailOf(r);
    img.alt = "";
    img.loading = "lazy";
    img.addEventListener("error", function () { shot.classList.add("is-missing"); });
    shot.appendChild(img);
    var play = el("span", "learn-video-play");
    play.innerHTML = '<svg class="icon" width="20" height="20" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M8 5.5v13l10.5-6.5z"/></svg>';  // static markup
    shot.appendChild(play);
    a.appendChild(shot);
    var text = el("span", "learn-video-text");
    text.appendChild(el("span", "learn-video-kind", "Video"));
    text.appendChild(el("span", "learn-video-title", r.title || "Watch the video"));
    text.appendChild(el("span", "learn-video-source", r.source ? "by " + r.source : ""));
    text.appendChild(el("span", "sr-only", "Opens on YouTube in a new tab"));
    a.appendChild(text);
    return a;
  }

  function lessons(c, resources) {
    var videos = resources.filter(function (r) { return /^video/i.test(r.kind || "") && safeUrl(r.url) && thumbnailOf(r); });
    var others = resources.filter(function (r) { return videos.indexOf(r) < 0; });
    if (!videos.length && !others.length) return;
    var wrap = el("div", "learn-lessons" + (videos.length ? " has-video" : ""));
    videos.slice(0, 1).forEach(function (r) { wrap.appendChild(videoCard(r)); });
    if (others.length) {
      var list = el("div", "list learn-resources");
      others.forEach(function (r) { list.appendChild(resourceRow(r)); });
      wrap.appendChild(list);
    }
    c.appendChild(wrap);
  }

  function section(title, sub) {
    var s = el("div", "learn-section");
    s.appendChild(el("h2", "learn-section-title", title));
    if (sub) s.appendChild(el("p", "learn-section-sub", sub));
    return s;
  }

  function safeUrl(u) {
    return /^https?:\/\//i.test(u || "") ? u : null;
  }

  function resourceRow(r) {
    var info = kindInfo(r.kind);
    var href = safeUrl(r.url);
    var row = el(href ? "a" : "div", "list-row");
    if (href) {
      row.href = href;
      row.target = "_blank";
      row.rel = "noopener noreferrer";
    }
    var tile = el("span", "icon-tile");
    tile.appendChild(icon(info.icon));
    row.appendChild(tile);

    var main = el("div", "row-main");
    main.appendChild(el("div", "row-title", r.title || ""));
    var meta = info.label + (r.source ? " from " + r.source : "");
    main.appendChild(el("div", "row-meta", meta));
    row.appendChild(main);

    if (href) {
      var end = el("span", "row-end");
      end.appendChild(icon("external"));
      var sr = el("span", "sr-only", "Opens in a new tab");
      end.appendChild(sr);
      row.appendChild(end);
    }
    return row;
  }

  function card(m) {
    var c = el("article", "card learn-card");

    var head = el("div", "learn-head");
    var left = el("div");
    left.appendChild(el("h2", "learn-name", m.bug_name || "Security bug"));
    if (m.plain) left.appendChild(el("p", "learn-plain", m.plain));
    left.appendChild(el("p", "learn-count", howOften(m.count || 1)));
    head.appendChild(left);

    if (m.last_analysis_id) {
      var link = el("a", "learn-link", "See the commit ");
      link.href = "/chat/" + encodeURIComponent(m.last_analysis_id);
      link.appendChild(icon("arrow"));
      head.appendChild(link);
    }
    c.appendChild(head);

    if ((m.count || 1) > 1) {
      var note = el("p", "learn-note");
      note.appendChild(icon("alert"));
      note.appendChild(el("span", null, "This came up more than once. The practice lab is the fastest way to make it stick."));
      c.appendChild(note);
    }

    lessons(c, m.resources || []);
    return c;
  }

  function empty() {
    var box = el("div", "card empty");
    var tile = el("span", "icon-tile");
    tile.appendChild(icon("book-lg"));
    box.appendChild(tile);
    box.appendChild(el("h2", "empty-title", "No mistakes to learn from yet."));
    box.appendChild(el("p", "empty-text", "Check a commit in Chat."));
    var a = el("a", "btn btn-primary btn-sm", "Go to Chat");
    a.href = "/chat";
    box.appendChild(a);
    return box;
  }

  function show(nodes) {
    root.textContent = "";
    nodes.forEach(function (n) { root.appendChild(n); });
    root.setAttribute("aria-busy", "false");
  }

  function failed() {
    var box = el("div", "card empty");
    box.appendChild(el("h2", "empty-title", "Could not load your learning."));
    box.appendChild(el("p", "empty-text", "Please try again in a moment."));
    var b = el("button", "btn btn-secondary btn-sm", "Try again");
    b.type = "button";
    b.addEventListener("click", load);
    box.appendChild(b);
    show([box]);
  }

  function load() {
    window.Kaizen.api("/api/learning").then(function (data) {
      var list = (data && data.mistakes) || [];
      var repos = (data && data.repos) || [];
      var nodes = [];
      if (repos.length) {
        nodes.push(section("From your repositories",
          "Each kind of problem KAIZEN found in your GitHub repositories, how far the fixes have got, and what to study."));
        var shown = repos.slice(0, FIRST_TOPICS).map(repoCard);
        nodes = nodes.concat(shown);
        if (repos.length > FIRST_TOPICS) {
          var rest = repos.slice(FIRST_TOPICS);
          var more = el("button", "btn btn-secondary btn-sm learn-more", "Show " + plural(rest.length, "more topic", "more topics"));
          more.type = "button";
          more.addEventListener("click", function () {
            rest.forEach(function (t) { more.parentNode.insertBefore(repoCard(t), more); });
            more.remove();
          });
          nodes.push(more);
        }
      }
      if (list.length) {
        if (repos.length) nodes.push(section("From your commits", "Mistakes found in commits you wrote."));
        list.forEach(function (m) { nodes.push(card(m)); });
      }
      show(nodes.length ? nodes : [empty()]);
    }).catch(failed);
  }

  load();
})();
