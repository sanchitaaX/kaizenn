// Proof harness for JavaScript code: run behaviour tests and the attack against one version of a function.
//
// Usage:  node harness_js.js <TYPE> <code-file>
// Prints one JSON object: {"tests": [{"name", "ok", "detail"}], "attack": {"worked", "detail"} | null}
//
// The code runs in a fresh VM context. Its imports (child_process, node-serialize, bcrypt) and framework
// objects (app, router, document, fetch) are small stand-ins, so it runs without installing anything.
// "tests" must pass before and after the fix (the program still works); "attack" should work only before.

"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("vm");

function makeApp() {
  const routes = {};
  const app = { get: (p, ...handlers) => { routes[p] = handlers[handlers.length - 1]; } };
  return { app, routes };
}

function res() {
  const r = { status: 200, body: undefined, location: undefined };
  r.send = (b) => { r.body = b; return r; };
  r.json = (b) => { r.body = b; return r; };
  r.sendStatus = (s) => { r.status = s; return r; };
  r.redirect = (l) => { r.status = 302; r.location = l; return r; };
  return r;
}

function load(code, globals, modules) {
  const sandbox = {
    console, Buffer, URL, process: { env: { ...(globals.env || {}) } }, setTimeout,
    require: (name) => {
      if (modules && name in modules) return modules[name];
      if (name === "path" || name === "fs" || name === "crypto") return require(name);
      throw new Error("module not available in the proof sandbox: " + name);
    },
    ...globals,
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox, { filename: "flagged.js", timeout: 5000 });
  return sandbox;
}

const CHECKS = {
  async SQL_INJECTION(code) {
    const rows = [
      { customer: "alice", item: "book", created: 1 }, { customer: "alice", item: "pen", created: 2 },
      { customer: "bob", item: "lamp", created: 3 },
    ];
    // A tiny database: parameters are always data; text glued into the SQL is parsed like a real engine would.
    const db = {
      async query(sql, params) {
        let pick;
        if (params && params.length) pick = (r) => r.customer === params[0];
        else if (/'\s*OR\s*'1'\s*=\s*'1/i.test(sql)) pick = () => true;
        else { const m = sql.match(/customer = '([^']*)'/); pick = (r) => m && r.customer === m[1]; }
        return rows.filter(pick).sort((a, b) => b.created - a.created);
      },
    };
    const ctx = load(code, {});
    const mine = await ctx.findOrders(db, "alice");
    const tests = [["Returns only the customer's orders, newest first", mine.map((r) => r.item).join() === "pen,book", JSON.stringify(mine.map((r) => r.item))],
      ["Unknown customer gets no orders", (await ctx.findOrders(db, "nobody")).length === 0, ""]];
    const leaked = await ctx.findOrders(db, "x' OR '1'='1");
    return [tests, [leaked.length > 0, leaked.length ? `input x' OR '1'='1 returned ${leaked.length} orders of other customers`
      : "input x' OR '1'='1 returned nothing: it was passed as data"]];
  },

  async COMMAND_INJECTION(code) {
    const calls = [];
    const cp = {
      exec: (cmd, cb) => { calls.push({ shell: true, cmd }); if (cb) cb(null); },
      execFile: (file, args, cb) => { calls.push({ shell: false, cmd: [file, ...args].join(" ") }); if (cb) cb(null); },
    };
    const ctx = load(code, {}, { child_process: cp });
    ctx.compressReport("q3.csv", () => {});
    const first = calls[calls.length - 1];
    const tests = [["Compresses the named report", /gzip/.test(first.cmd) && first.cmd.replace(/\\/g, "/").includes("reports/q3.csv"), first.cmd]];
    ctx.compressReport("x.csv; touch /tmp/pwned", () => {});
    const last = calls[calls.length - 1];
    return [tests, [last.shell && last.cmd.includes(";"), last.shell ? `the shell received: ${last.cmd}` : `no shell, arguments only: ${last.cmd}`]];
  },

  async XSS(code) {
    const made = [];
    const document = { createElement: (tag) => { const e = { tag, textContent: "" }; made.push(e); return e; } };
    const el = () => {
      const e = { html: null, children: [] };
      Object.defineProperty(e, "innerHTML", { set(v) { e.html = v; }, get() { return e.html; } });
      e.replaceChildren = (...kids) => { e.children = kids; };
      return e;
    };
    const ctx = load(code, { document });
    const a = el();
    ctx.showGreeting(a, { displayName: "Ana" });
    const shown = a.html || a.children.map((c) => c.textContent).join("");
    const tests = [["Greets the user by name", /Welcome back, Ana/.test(shown), shown]];
    const b = el();
    ctx.showGreeting(b, { displayName: "<img src=x onerror=alert(1)>" });
    const markup = b.html && /<img/i.test(b.html);
    return [tests, [!!markup, markup ? `the page received markup: ${b.html}` : "the name was set as text, never as HTML"]];
  },

  async PATH_TRAVERSAL(code) {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "proof-"));
    fs.mkdirSync(path.join(tmp, "uploads"));
    fs.writeFileSync(path.join(tmp, "uploads", "report.txt"), "quarterly numbers");
    fs.writeFileSync(path.join(tmp, "secret.txt"), "db password");
    const { app, routes } = makeApp();
    load(code, { app, UPLOADS: path.join(tmp, "uploads") });
    const ok = res(); routes["/files/:name"]({ params: { name: "report.txt" } }, ok);
    const tests = [["Serves a file from the uploads folder", String(ok.body) === "quarterly numbers", String(ok.body)]];
    const bad = res(); routes["/files/:name"]({ params: { name: "../secret.txt" } }, bad);
    const leaked = String(bad.body) === "db password";
    return [tests, [leaked, leaked ? "../secret.txt returned a file outside the uploads folder" : `../secret.txt was refused (${bad.status})`]];
  },

  async HARDCODED_SECRET(code) {
    const made = [];
    function PaymentsClient(opts) { made.push(opts); }
    load(code, { PaymentsClient, env: { PAYMENTS_API_KEY: "sk_test_from_environment" } });
    const opts = made[0] || {};
    const tests = [["The payment client is configured", !!opts.apiKey && opts.timeout === 5000, JSON.stringify({ timeout: opts.timeout })]];
    const leaked = /sk_live_/.test(code);
    return [tests, [leaked, leaked ? "a live key is readable in the source code" : "no key in the source; it comes from the environment"]];
  },

  async SSRF(code) {
    const fetched = [];
    const fetch = async (u) => { fetched.push(String(u)); return { text: async () => "x".repeat(800) }; };
    const { app, routes } = makeApp();
    load(code, { app, fetch });
    const ok = res(); await routes["/preview"]({ query: { url: "https://images.example.com/cat.png" } }, ok);
    const tests = [["Previews an approved image host", ok.body === "x".repeat(500), `${String(ok.body || "").length} characters`]];
    const bad = res();
    try { await routes["/preview"]({ query: { url: "http://169.254.169.254/latest/meta-data/" } }, bad); } catch (_) { /* refused */ }
    const hit = fetched.some((u) => u.includes("169.254.169.254"));
    return [tests, [hit, hit ? "the server fetched the cloud metadata address" : `the internal address was refused (${bad.status})`]];
  },

  async INSECURE_DESERIALIZATION(code) {
    let ran = false;
    // node-serialize runs any value marked as a function; this stand-in does the same, harmlessly.
    const serialize = {
      serialize: (d) => JSON.stringify(d),
      unserialize: (s) => {
        const o = JSON.parse(s);
        for (const k of Object.keys(o)) if (typeof o[k] === "string" && o[k].startsWith("_$$ND_FUNC$$_")) ran = true;
        return o;
      },
    };
    const ctx = load(code, {}, { "node-serialize": serialize });
    const cookie = ctx.makeSession({ user: "alice", role: "student" });
    const back = ctx.loadSession(cookie);
    const tests = [["A session written by the app reads back", back.user === "alice" && back.role === "student", JSON.stringify(back)]];
    const evil = Buffer.from(JSON.stringify({ user: "_$$ND_FUNC$$_function(){ require('child_process').exec('id') }()" })).toString("base64");
    try { ctx.loadSession(evil); } catch (_) { /* rejected */ }
    return [tests, [ran, ran ? "a crafted cookie ran code on the server" : "the crafted cookie was read as plain data"]];
  },

  async IDOR(code) {
    const invoices = [{ _id: "1", ownerId: 7, total: 120 }, { _id: "2", ownerId: 9, total: 999 }];
    const Invoice = {
      findById: async (id) => invoices.find((i) => i._id === id) || null,
      findOne: async (q) => invoices.find((i) => Object.entries(q).every(([k, v]) => i[k] === v)) || null,
    };
    const routes = {};
    const router = { get: (p, ...h) => { routes[p] = h[h.length - 1]; } };
    load(code, { router, requireLogin: (q, r, n) => n(), Invoice });
    const own = res(); await routes["/invoices/:id"]({ params: { id: "1" }, user: { id: 7 } }, own);
    const tests = [["The owner opens their own invoice", own.body && own.body.total === 120, ""]];
    const other = res(); await routes["/invoices/:id"]({ params: { id: "2" }, user: { id: 7 } }, other);
    const leaked = other.body && other.body.ownerId === 9;
    return [tests, [!!leaked, leaked ? "user 7 opened invoice 2, which belongs to user 9" : `user 7 got ${other.status} for someone else's invoice`]];
  },

  async WEAK_HASH(code) {
    const crypto = require("crypto");
    const bcrypt = { hash: async (pw, rounds) => "$2b$" + rounds + "$" + crypto.randomBytes(8).toString("hex") + crypto.createHash("sha256").update(pw).digest("hex") };
    const ctx = load(code, {}, { bcrypt });
    const a = await ctx.hashPassword("hunter2");
    const b = await ctx.hashPassword("hunter2");
    const tests = [["Returns a stored hash, never the password", typeof a === "string" && a.length > 0 && !a.includes("hunter2"), ""]];
    const same = a === b && a.length === 32;
    return [tests, [same, same ? "identical unsalted MD5 for the same password, so lookup tables crack it"
      : "salted slow hash: the same password gives a different hash each time"]];
  },

  async OPEN_REDIRECT(code) {
    const { app, routes } = makeApp();
    load(code, { app });
    const go = (next) => { const r = res(); routes["/after-login"]({ query: next === undefined ? {} : { next } }, r); return r.location; };
    const tests = [["Goes back to the page you came from", go("/dashboard") === "/dashboard", go("/dashboard")],
      ["Defaults to the home page", go(undefined) === "/", go(undefined)]];
    const to = go("https://evil.example/login");
    return [tests, [String(to).startsWith("https://evil"), `redirected to ${to}`]];
  },
};

(async () => {
  const [kind, file] = process.argv.slice(2);
  let out;
  try {
    const code = fs.readFileSync(file, "utf8");
    const [tests, attack] = await CHECKS[kind](code);
    out = { tests: tests.map(([name, ok, detail]) => ({ name, ok: !!ok, detail: String(detail || "") })),
      attack: { worked: !!attack[0], detail: attack[1] } };
  } catch (err) {
    out = { tests: [{ name: "The code runs", ok: false, detail: `${err.name}: ${err.message}` }], attack: null };
  }
  process.stdout.write(JSON.stringify(out) + "\n");
})();
