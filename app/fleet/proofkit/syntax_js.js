// Syntax check for one JavaScript or TypeScript file. Usage: node syntax_js.js <file>
// TypeScript types are stripped first (Node's own stripper), then `node --check` parses the result.
// Prints {"ok": true|false, "detail": "..."}.

"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const file = process.argv[2];
let code = fs.readFileSync(file, "utf8");
const ext = path.extname(file).toLowerCase();
const out = (ok, detail) => process.stdout.write(JSON.stringify({ ok, detail }) + "\n");

if ([".ts", ".mts", ".cts"].includes(ext)) {
  try {
    const { stripTypeScriptTypes } = require("node:module");
    process.removeAllListeners("warning");
    code = stripTypeScriptTypes(code, { mode: "strip" });
  } catch (err) {
    out(false, "TypeScript could not be parsed: " + err.message.split("\n")[0]);
    process.exit(0);
  }
}
const esm = ext === ".mjs" || ext === ".mts" || /^\s*(import|export)\s/m.test(code);
const tmp = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "syntax-")), "check" + (esm ? ".mjs" : ".cjs"));
fs.writeFileSync(tmp, code);
const r = spawnSync(process.execPath, ["--check", tmp], { encoding: "utf8" });
if (r.status === 0) out(true, "parses cleanly");
else {
  const line = (r.stderr || "").split("\n").find((l) => /Error/.test(l)) || "syntax error";
  out(false, line.trim());
}
