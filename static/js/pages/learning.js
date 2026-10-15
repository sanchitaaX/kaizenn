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
    return { icon: "guide", label: "Guide" };
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

    var res = m.resources || [];
    if (res.length) {
      var list = el("div", "list learn-resources");
      res.forEach(function (r) { list.appendChild(resourceRow(r)); });
      c.appendChild(list);
    }
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
    window.SecureFix.api("/api/learning").then(function (data) {
      var list = (data && data.mistakes) || [];
      show(list.length ? list.map(card) : [empty()]);
    }).catch(failed);
  }

  load();
})();
