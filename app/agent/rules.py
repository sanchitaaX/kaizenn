"""Built-in discovery rules: line patterns that point the agent at code worth a closer look.

They are deliberately broad (they find candidates, not verdicts); the model triages every hit against the code
around it. Types match app/fleet/catalog.py where one exists; CWE ids are attached here.
"""

from __future__ import annotations

import re

TYPES = {
    "SQL_INJECTION": ("SQL injection", "CWE-89", "critical"),
    "COMMAND_INJECTION": ("Command injection", "CWE-78", "critical"),
    "CODE_INJECTION": ("Code injection", "CWE-94", "critical"),
    "INSECURE_DESERIALIZATION": ("Unsafe deserialization", "CWE-502", "critical"),
    "PATH_TRAVERSAL": ("Path traversal", "CWE-22", "high"),
    "SSRF": ("Server-side request forgery", "CWE-918", "high"),
    "HARDCODED_SECRET": ("Hard-coded credentials", "CWE-798", "high"),
    "IDOR": ("Broken access control", "CWE-639", "high"),
    "MISSING_AUTH": ("Missing authorization", "CWE-862", "high"),
    "XSS": ("Cross-site scripting", "CWE-79", "medium"),
    "NOSQL_INJECTION": ("NoSQL injection", "CWE-943", "high"),
    "WEAK_HASH": ("Weak password hashing", "CWE-916", "medium"),
    "OPEN_REDIRECT": ("Open redirect", "CWE-601", "medium"),
}
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

_I = re.IGNORECASE
SECRET = (r"""sk_live_[0-9a-zA-Z]{8,}|AKIA[0-9A-Z]{16}|-----BEGIN (RSA |EC )?PRIVATE KEY|"""
          r"""\b(api_?key|secret(_key)?|password|passwd|token|auth_token)\b\s*[:=]\s*["'][^"'\s]{10,}["']""")

RULES: dict[str, dict[str, list[re.Pattern]]] = {
    "python": {
        "SQL_INJECTION": [re.compile(p, _I) for p in (
            r"""\b(execute|executemany|executescript|raw|query)\s*\(\s*(f["']|["'][^"'\n]*["']\s*(%|\+|\.format))""",
            r"""f"[^"\n]*\b(select|insert|update|delete)\b[^"\n]*\{""",
            r"""f'[^'\n]*\b(select|insert|update|delete)\b[^'\n]*\{""",
            r"""("[^"\n]*\b(select|insert|update|delete)\b[^"\n]*"|'[^'\n]*\b(select|insert|update|delete)\b[^'\n]*')"""
            r"""\s*(%\s*[\w(]|\+\s*\w|\.format\()""")],
        "COMMAND_INJECTION": [re.compile(p) for p in (
            r"shell\s*=\s*True", r"\bos\.(system|popen)\(", r"subprocess\.\w+\([^)\n]*(\+|%|f[\"'])")],
        "CODE_INJECTION": [re.compile(r"(?<![\w.])(eval|exec)\(\s*(?!['\"])")],
        "INSECURE_DESERIALIZATION": [re.compile(p) for p in (
            r"\bpickle\.loads?\(", r"\byaml\.load\((?![^)\n]*Loader\s*=\s*yaml\.SafeLoader)", r"\bmarshal\.loads\(",
            r"\bjsonpickle\.decode\(", r"\bshelve\.open\(")],
        "PATH_TRAVERSAL": [re.compile(p) for p in (
            r"\bopen\(\s*(os\.path\.join\(|f[\"']|[\w.]+\s*\+)", r"\bsend_file\(\s*(?![\"'])",
            r"\bos\.path\.join\([^)\n]*request\.")],
        "SSRF": [re.compile(p) for p in (
            r"\brequests\.(get|post|put|request|head)\(\s*(?![\"'])[\w.\[\]'\"]*url",
            r"\burllib\.request\.urlopen\(\s*(?![\"'])")],
        "HARDCODED_SECRET": [re.compile(SECRET, _I)],
        "XSS": [re.compile(p) for p in (
            r"\brender_template_string\(", r"\bMarkup\(\s*(?![\"'])", r"\bmark_safe\(", r"\|\s*safe\b",
            r"return\s+f[\"'][^\"'\n]*<\w[^\"'\n]*\{")],
        "WEAK_HASH": [re.compile(r"\bhashlib\.(md5|sha1)\(")],
        "OPEN_REDIRECT": [re.compile(r"\bredirect\(\s*request\.(args|form|values)")],
    },
    "javascript": {
        "SQL_INJECTION": [re.compile(p, _I) for p in (
            r"""\b(query|execute|raw|run|all|get|exec)\s*\(\s*(`[^`]*\$\{|["'][^"'\n]*["']\s*\+)""",
            r"""`[^`]*\b(select|insert|update|delete)\b[^`]*\$\{""",
            r"""["'][^"'\n]*\b(select|insert|update|delete)\b[^"'\n]*["']\s*\+\s*\w""")],
        "NOSQL_INJECTION": [re.compile(p) for p in (r"\$where", r"\.(find|findOne|update\w*|delete\w*)\(\s*req\.(body|query|params)")],
        "COMMAND_INJECTION": [re.compile(p) for p in (
            r"\b(exec|execSync)\s*\(\s*(`|[\w.]+\s*\+|[^)\n]*\$\{)", r"\bspawn\w*\([^)\n]*shell\s*:\s*true",
            r"child_process\.exec\(")],
        "CODE_INJECTION": [re.compile(p) for p in (r"(?<![\w.])eval\(\s*(?![\"'`])", r"\bnew\s+Function\(",
                                                     r"\bvm\.runIn\w*Context\(")],
        "INSECURE_DESERIALIZATION": [re.compile(p) for p in (r"\bunserialize\(", r"node-serialize")],
        "PATH_TRAVERSAL": [re.compile(p) for p in (
            r"\b(readFile|readFileSync|createReadStream|sendFile|unlink|unlinkSync|writeFile|writeFileSync)\w*\(\s*"
            r"(path\.(join|resolve)\([^)\n]*req\.|req\.|`[^`]*\$\{|[\w.]+\s*\+)",
            r"\bres\.(sendFile|download)\(\s*(?![\"'])")],
        "SSRF": [re.compile(r"\b(fetch|axios(\.(get|post|request))?|got|needle|request|http\.get|https\.get)\(\s*req\.(query|body|params)")],
        "HARDCODED_SECRET": [re.compile(SECRET, _I)],
        "XSS": [re.compile(p) for p in (
            r"\.(inner|outer)HTML\s*=\s*(?![\"'`]\s*;)", r"\bdocument\.write\(", r"dangerouslySetInnerHTML",
            r"\bres\.send\(\s*[`\"'][^)\n]*(\$\{|\"\s*\+|'\s*\+)", r"bypassSecurityTrust\w+\(")],
        "WEAK_HASH": [re.compile(r"""createHash\(\s*["'](md5|sha1)["']""")],
        "OPEN_REDIRECT": [re.compile(r"\bredirect\(\s*req\.(query|body|params)")],
    },
}
RULES["typescript"] = RULES["javascript"]


def lang_of(path: str) -> str | None:
    p = path.lower()
    if p.endswith(".py"):
        return "python"
    if p.endswith((".ts", ".mts", ".cts", ".tsx")):
        return "typescript"
    if p.endswith((".js", ".mjs", ".cjs", ".jsx")):
        return "javascript"
    return None


# A query built from pieces of SQL, with the values still passed as ? parameters, is not an injection:
#   pool.query(`SELECT ... WHERE ${conditions.join(' AND ')}`, params)   (conditions holds 'a.id = ?')
# These interpolations only splice such pieces in; anything else (a value, req.*, a call) keeps the finding.
# The name has to end in a clause word, so values such as ${orderId} or a user's ${sortColumn} still count.
_SQL_PIECE = r"[\w.]*(conditions?|clauses?|where|joins?|select|sql)"
_SAFE_PIECES = [re.compile(p, _I) for p in (
    _SQL_PIECE,                                                              # joinClause, assignmentSelect
    _SQL_PIECE + r"\.join\(\s*(['\"])[^'\"]*\2\s*\)",                        # conditions.join(' AND ')
    r"[\w.]+\.map\(\s*\(\s*\w*\s*\)\s*=>\s*(['\"])\?\1\s*\)\.join\(\s*(['\"])[^'\"]*\2\s*\)",  # ids.map(() => '?').join(',')
)]


def _sql_pieces_only(text: str, start: int) -> bool:
    """True when the template literal of a JavaScript SQL hit only splices in SQL pieces (see _SAFE_PIECES)."""
    open_ = text.find("`", start)
    close = text.find("`", open_ + 1) if open_ >= 0 else -1
    if open_ < 0 or close < 0:
        return False
    parts = re.findall(r"\$\{([^{}]*)\}", text[open_:close])
    return bool(parts) and all(any(rx.fullmatch(p.strip()) for rx in _SAFE_PIECES) for p in parts)


def scan_text(path: str, text: str) -> list[dict]:
    """[{type, line, match}] for every rule hit in one file (one hit per type and line)."""
    lang = lang_of(path)
    hits, seen = [], set()
    for kind, patterns in RULES.get(lang or "", {}).items():
        for rx in patterns:
            for m in rx.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                if (kind, line) in seen:
                    continue
                if kind == "SQL_INJECTION" and lang == "javascript" and _sql_pieces_only(text, m.start()):
                    continue
                seen.add((kind, line))
                hits.append({"type": kind, "line": line, "match": m.group(0)[:120]})
    return hits


def count(path: str, text: str, kind: str) -> int:
    return sum(1 for h in scan_text(path, text) if h["type"] == kind)
