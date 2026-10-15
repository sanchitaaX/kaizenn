"""Proof harness for Python code: run behaviour tests and the attack against one version of a function.

Usage:  python -I harness_py.py <TYPE> <code-file>
Prints one JSON object: {"tests": [{"name", "ok", "detail"}], "attack": {"worked", "detail"} | null}

The code is the flagged function (before or after the fix). Its third-party imports (flask, stripe, requests,
bcrypt) are replaced by small stand-ins so it runs anywhere. The same tests run on both versions:
"tests" must pass before and after (the program still works), "attack" should work before and fail after.
"""

from __future__ import annotations

import base64
import builtins
import hashlib
import json
import os
import pickle
import sys
import tempfile
import types
from pathlib import Path


class Abort(Exception):
    """flask.abort / get_or_404 stand-in."""


def module(name: str, **attrs) -> types.ModuleType:
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


def load(code: str, **extra) -> dict:
    ns: dict = {"__name__": "flagged", **extra}
    exec(compile(code, "flagged.py", "exec"), ns)
    return ns


# ------------------------------------------------------------------ per type: (tests, attack)


def sql_injection(code: str):
    import sqlite3

    def db():
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE orders (customer TEXT, item TEXT, created INTEGER)")
        conn.executemany("INSERT INTO orders VALUES (?, ?, ?)",
                         [("alice", "book", 1), ("alice", "pen", 2), ("bob", "lamp", 3), ("carol", "desk", 4)])
        return conn

    ns = load(code)
    rows = ns["find_orders"](db(), "alice")
    tests = [("Returns only the customer's orders, newest first",
              [r["item"] for r in rows] == ["pen", "book"], f"got {[r['item'] for r in rows]}")]
    rows = ns["find_orders"](db(), "nobody")
    tests.append(("Unknown customer gets no orders", rows == [], f"got {len(rows)} rows"))
    try:
        leaked = ns["find_orders"](db(), "x' OR '1'='1")
    except Exception as exc:  # the query broke instead of leaking: not exploited this way
        return tests, (False, f"the query failed safely ({exc.__class__.__name__})")
    if leaked:
        return tests, (True, f"input x' OR '1'='1 returned {len(leaked)} orders of other customers")
    return tests, (False, "input x' OR '1'='1 returned nothing: it was treated as a plain name")


def command_injection(code: str):
    import subprocess

    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0)

    module("subprocess", run=fake_run, CompletedProcess=subprocess.CompletedProcess)
    ns = load(code)

    def flat(cmd):
        return (" ".join(cmd) if isinstance(cmd, list) else cmd).replace("\\", "/")

    out = ns["compress_report"]("q3.csv")
    cmd, kw = calls[-1]
    tests = [("Compresses the named report", "gzip" in flat(cmd) and "reports/q3.csv" in flat(cmd), flat(cmd)),
             ("Returns the archive name", out == "q3.csv.gz", f"got {out}")]
    ns["compress_report"]("x.csv; touch /tmp/pwned")
    cmd, kw = calls[-1]
    via_shell = kw.get("shell") is True and isinstance(cmd, str)
    attack = (via_shell and ";" in cmd,
              f"the shell received: {flat(cmd)}" if via_shell else f"no shell, arguments only: {flat(cmd)}")
    return tests, attack


def xss(code: str):
    req = types.SimpleNamespace(args={})
    module("flask", request=req)
    ns = load(code)
    req.args = {"q": "running shoes"}
    page = ns["search_page"]()
    tests = [("Shows the search term", "Results for running shoes" in str(page), str(page))]
    req.args = {"q": "<script>alert(1)</script>"}
    page = str(ns["search_page"]())
    return tests, ("<script>" in page, f"page HTML: {page}")


def path_traversal(code: str):
    tmp = Path(tempfile.mkdtemp())
    (tmp / "uploads").mkdir()
    (tmp / "uploads" / "report.txt").write_bytes(b"quarterly numbers")
    (tmp / "secret.txt").write_bytes(b"db password")
    ns = load(code)
    ns["UPLOADS"] = (tmp / "uploads").resolve() if isinstance(ns.get("UPLOADS"), Path) else str(tmp / "uploads")
    tests = [("Reads a file from the uploads folder", ns["read_upload"]("report.txt") == b"quarterly numbers", "")]
    try:
        got = ns["read_upload"]("../secret.txt")
        attack = (got == b"db password", "../secret.txt returned a file outside the uploads folder")
    except (PermissionError, ValueError, OSError) as exc:
        attack = (False, f"../secret.txt was refused ({exc.__class__.__name__})")
    return tests, attack


def hardcoded_secret(code: str):
    created = []
    charge = types.SimpleNamespace(create=lambda **kw: created.append(kw) or {"id": "ch_1", **kw})
    stripe = module("stripe", api_key=None, Charge=charge)
    os.environ["STRIPE_API_KEY"] = "sk_test_from_environment"
    ns = load(code)
    out = ns["charge"](500, "tok_visa")
    tests = [("Charges the amount given", out.get("amount") == 500 and out.get("source") == "tok_visa", str(out)),
             ("A payment key is configured", bool(stripe.api_key), "")]
    leaked = "sk_live_" in code
    return tests, (leaked, "a live key is readable in the source code" if leaked
                   else "no key in the source; it comes from the environment")


def ssrf(code: str):
    fetched = []

    def get(url, **kw):
        fetched.append(url)
        return types.SimpleNamespace(text="x" * 800)

    module("requests", get=get)
    ns = load(code)
    out = ns["preview"]("https://images.example.com/cat.png")
    tests = [("Previews an approved image host", out == "x" * 500, f"{len(out)} characters")]
    try:
        ns["preview"]("http://169.254.169.254/latest/meta-data/")
        attack = ("169.254.169.254" in fetched[-1], "the server fetched the cloud metadata address")
    except ValueError:
        attack = (False, "the internal address was refused before any request")
    return tests, attack


def insecure_deserialization(code: str):
    ns = load(code)
    cookie = ns["make_session"]({"user": "alice", "role": "student"})
    tests = [("A session written by the app reads back", ns["load_session"](cookie) == {"user": "alice", "role": "student"}, "")]
    builtins.PWNED = False

    class Exploit:
        def __reduce__(self):
            return (exec, ("import builtins; builtins.PWNED = True",))

    evil = base64.b64encode(pickle.dumps(Exploit()))
    try:
        ns["load_session"](evil)
    except Exception:
        pass
    ran = bool(getattr(builtins, "PWNED", False))
    return tests, (ran, "a crafted cookie ran code on the server" if ran else "the crafted cookie was rejected as data")


def idor(code: str):
    class Query:
        rows = {1: {"id": 1, "owner_id": 7, "total": 120}, 2: {"id": 2, "owner_id": 9, "total": 999}}

        def get_or_404(self, i):
            if i not in self.rows:
                raise Abort(404)
            return types.SimpleNamespace(to_dict=lambda r=self.rows[i]: dict(r))

        def filter_by(self, **kw):
            found = [r for r in self.rows.values() if all(r.get(k) == v for k, v in kw.items())]

            class One:
                def first_or_404(self_inner):
                    if not found:
                        raise Abort(404)
                    return types.SimpleNamespace(to_dict=lambda r=found[0]: dict(r))
            return One()

    app = types.SimpleNamespace(get=lambda *a, **k: (lambda f: f))
    ns = load(code, app=app, login_required=lambda f: f, Invoice=types.SimpleNamespace(query=Query()),
              current_user=types.SimpleNamespace(id=7))
    tests = [("The owner opens their own invoice", ns["get_invoice"](1)["total"] == 120, "")]
    try:
        other = ns["get_invoice"](2)
        attack = (other["owner_id"] == 9, "user 7 opened invoice 2, which belongs to user 9")
    except Abort:
        attack = (False, "user 7 got 404 for someone else's invoice")
    return tests, attack


def weak_hash(code: str):
    def hashpw(pw, salt):
        return salt + hashlib.pbkdf2_hmac("sha256", pw, salt, 10_000).hex().encode()

    module("bcrypt", hashpw=hashpw, gensalt=lambda: b"$2b$12$" + os.urandom(8).hex().encode())
    ns = load(code)
    a, b = ns["hash_password"]("hunter2"), ns["hash_password"]("hunter2")
    tests = [("Returns a stored hash, never the password", isinstance(a, str) and a and "hunter2" not in a, "")]
    same = a == b and len(a) == 32
    return tests, (same, "identical unsalted MD5 for the same password, so lookup tables crack it" if same
                   else "salted slow hash: the same password gives a different hash each time")


def open_redirect(code: str):
    req = types.SimpleNamespace(args={})
    module("flask", request=req, redirect=lambda target: ("redirect", target))
    ns = load(code)
    req.args = {"next": "/dashboard"}
    tests = [("Goes back to the page you came from", ns["after_login"]() == ("redirect", "/dashboard"), "")]
    req.args = {}
    tests.append(("Defaults to the home page", ns["after_login"]() == ("redirect", "/"), ""))
    req.args = {"next": "https://evil.example/login"}
    out = ns["after_login"]()
    return tests, (out[1].startswith("https://evil"), f"redirected to {out[1]}")


CHECKS = {
    "SQL_INJECTION": sql_injection, "COMMAND_INJECTION": command_injection, "XSS": xss,
    "PATH_TRAVERSAL": path_traversal, "HARDCODED_SECRET": hardcoded_secret, "SSRF": ssrf,
    "INSECURE_DESERIALIZATION": insecure_deserialization, "IDOR": idor, "WEAK_HASH": weak_hash,
    "OPEN_REDIRECT": open_redirect,
}


def main() -> None:
    kind, path = sys.argv[1], sys.argv[2]
    code = Path(path).read_text(encoding="utf-8")
    try:
        tests, attack = CHECKS[kind](code)
        out = {"tests": [{"name": n, "ok": bool(ok), "detail": d} for n, ok, d in tests],
               "attack": {"worked": bool(attack[0]), "detail": attack[1]}}
    except Exception as exc:  # the code itself is broken: every test fails
        out = {"tests": [{"name": "The code runs", "ok": False, "detail": f"{exc.__class__.__name__}: {exc}"}],
               "attack": None}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
