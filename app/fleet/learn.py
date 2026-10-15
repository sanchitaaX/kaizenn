"""Learning material per vulnerability type: what to remember, and where to read and practise.

Every fix (dashboard fixes and agent runs) carries the resources for its type, and the Learning page groups the
problems found in a person's own repositories by type. Links point to OWASP, PortSwigger Web Security Academy,
MITRE CWE and official documentation only; tests/test_learn.py keeps the catalogue complete.
"""

from __future__ import annotations

import re

OWASP_CS = "https://cheatsheetseries.owasp.org/cheatsheets/"
PS = "https://portswigger.net/web-security/"


def _cwe(n: int) -> dict:
    return {"kind": "Reference", "title": f"CWE-{n} definition", "source": "MITRE CWE",
            "url": f"https://cwe.mitre.org/data/definitions/{n}.html"}


LEARN: dict[str, dict] = {
    "SQL_INJECTION": {
        "remember": [
            "Pass user input as query parameters (placeholders such as ? or $1), never by building the SQL string.",
            "If part of the query must change (a column or sort order), pick it from a fixed allow-list.",
            "Give the application's database account only the permissions it needs.",
        ],
        "resources": [
            {"kind": "Guide", "title": "SQL Injection Prevention Cheat Sheet", "source": "OWASP",
             "url": OWASP_CS + "SQL_Injection_Prevention_Cheat_Sheet.html"},
            {"kind": "Guide", "title": "SQL injection", "source": "PortSwigger Web Security Academy", "url": PS + "sql-injection"},
            {"kind": "Practice lab", "title": "SQL injection in a WHERE clause: retrieve hidden data",
             "source": "PortSwigger Web Security Academy", "url": PS + "sql-injection/lab-retrieve-hidden-data"},
            _cwe(89),
        ],
    },
    "COMMAND_INJECTION": {
        "remember": [
            "Do not run commands through a shell with user text (shell=True, os.system, exec with a string).",
            "Call the program with a list of arguments, or use a library instead of a command.",
            "If user text must be an argument, check it against an allow-list first.",
        ],
        "resources": [
            {"kind": "Guide", "title": "OS Command Injection Defense Cheat Sheet", "source": "OWASP",
             "url": OWASP_CS + "OS_Command_Injection_Defense_Cheat_Sheet.html"},
            {"kind": "Guide", "title": "OS command injection", "source": "PortSwigger Web Security Academy",
             "url": PS + "os-command-injection"},
            {"kind": "Practice lab", "title": "OS command injection, simple case", "source": "PortSwigger Web Security Academy",
             "url": PS + "os-command-injection/lab-simple"},
            _cwe(78),
        ],
    },
    "CODE_INJECTION": {
        "name": "Code injection",
        "plain": "Text from outside reaches eval, exec or a similar call, so an attacker can run their own code in the app.",
        "remember": [
            "Never pass user input to eval, exec, new Function or anything else that runs code.",
            "To read data, use a parser such as json.loads (or ast.literal_eval for Python literals).",
            "For behaviour chosen at run time, map allowed names to functions in a dictionary.",
        ],
        "resources": [
            {"kind": "Guide", "title": "Code injection", "source": "OWASP", "url": "https://community.owasp.org/attacks/Code_Injection"},
            {"kind": "Guide", "title": "Server-side template injection", "source": "PortSwigger Web Security Academy",
             "url": PS + "server-side-template-injection"},
            _cwe(94),
        ],
    },
    "INSECURE_DESERIALIZATION": {
        "remember": [
            "Do not unpickle or unserialize data that comes from users, files they upload or the network; use JSON.",
            "Load YAML with yaml.safe_load, never yaml.load with the default loader.",
            "If serialized objects must be accepted, sign them and check the signature before loading.",
        ],
        "resources": [
            {"kind": "Guide", "title": "Deserialization Cheat Sheet", "source": "OWASP", "url": OWASP_CS + "Deserialization_Cheat_Sheet.html"},
            {"kind": "Guide", "title": "Insecure deserialization", "source": "PortSwigger Web Security Academy",
             "url": PS + "deserialization"},
            {"kind": "Reference", "title": "pickle: the security warning", "source": "Python documentation",
             "url": "https://docs.python.org/3/library/pickle.html"},
            _cwe(502),
        ],
    },
    "PATH_TRAVERSAL": {
        "remember": [
            "Resolve the full path and check it is still inside the folder you meant to serve.",
            "Better still, look files up by an id instead of using the name the user sent.",
            "Reject absolute paths and '..' instead of trying to strip them out.",
        ],
        "resources": [
            {"kind": "Guide", "title": "Path traversal", "source": "OWASP", "url": "https://community.owasp.org/attacks/Path_Traversal"},
            {"kind": "Guide", "title": "Path traversal", "source": "PortSwigger Web Security Academy", "url": PS + "file-path-traversal"},
            {"kind": "Practice lab", "title": "File path traversal, simple case", "source": "PortSwigger Web Security Academy",
             "url": PS + "file-path-traversal/lab-simple"},
            _cwe(22),
        ],
    },
    "SSRF": {
        "remember": [
            "Only fetch URLs on an allow-list of hosts; do not fetch any address a user types.",
            "Block private, loopback and link-local addresses (including 169.254.169.254) after resolving the name.",
            "Turn off automatic redirects, or check every redirect target the same way.",
        ],
        "resources": [
            {"kind": "Guide", "title": "Server-Side Request Forgery Prevention Cheat Sheet", "source": "OWASP",
             "url": OWASP_CS + "Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html"},
            {"kind": "Guide", "title": "Server-side request forgery (SSRF)", "source": "PortSwigger Web Security Academy",
             "url": PS + "ssrf"},
            {"kind": "Practice lab", "title": "Basic SSRF against the local server", "source": "PortSwigger Web Security Academy",
             "url": PS + "ssrf/lab-basic-ssrf-against-localhost"},
            _cwe(918),
        ],
    },
    "HARDCODED_SECRET": {
        "remember": [
            "Keep keys and passwords out of the code: read them from environment variables or a secrets manager.",
            "A secret that was ever committed is leaked: revoke and replace it, because git history keeps the old value.",
            "Turn on secret scanning and push protection so new secrets are stopped before they land.",
        ],
        "resources": [
            {"kind": "Guide", "title": "Secrets Management Cheat Sheet", "source": "OWASP", "url": OWASP_CS + "Secrets_Management_Cheat_Sheet.html"},
            {"kind": "Guide", "title": "Secret scanning", "source": "GitHub Docs",
             "url": "https://docs.github.com/en/code-security/concepts/secret-security/secret-scanning"},
            {"kind": "Guide", "title": "Removing sensitive data from a repository", "source": "GitHub Docs",
             "url": "https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository"},
            _cwe(798),
        ],
    },
    "IDOR": {
        "remember": [
            "On every request, check on the server that the signed-in user may see or change that record.",
            "Scope queries to the current user (for example WHERE owner_id = the signed-in user).",
            "Hard-to-guess ids help, but they are not an access check.",
        ],
        "resources": [
            {"kind": "Guide", "title": "Insecure Direct Object Reference Prevention Cheat Sheet", "source": "OWASP",
             "url": OWASP_CS + "Insecure_Direct_Object_Reference_Prevention_Cheat_Sheet.html"},
            {"kind": "Guide", "title": "A01 Broken Access Control", "source": "OWASP Top 10",
             "url": "https://top10.owasp.org/2021/A01_2021-Broken_Access_Control/index.html"},
            {"kind": "Practice lab", "title": "User ID controlled by request parameter", "source": "PortSwigger Web Security Academy",
             "url": PS + "access-control/lab-user-id-controlled-by-request-parameter"},
            _cwe(639),
        ],
    },
    "MISSING_AUTH": {
        "name": "Missing authorization",
        "plain": "A route or action works without checking who is asking, so anyone who finds it can use it.",
        "remember": [
            "Deny by default: every route needs an explicit sign-in and permission check.",
            "Enforce the check on the server; hiding a button does not protect the endpoint behind it.",
            "Try each sensitive endpoint signed out and as a low-privilege user.",
        ],
        "resources": [
            {"kind": "Guide", "title": "Authorization Cheat Sheet", "source": "OWASP", "url": OWASP_CS + "Authorization_Cheat_Sheet.html"},
            {"kind": "Guide", "title": "Access control vulnerabilities", "source": "PortSwigger Web Security Academy",
             "url": PS + "access-control"},
            {"kind": "Practice lab", "title": "Unprotected admin functionality", "source": "PortSwigger Web Security Academy",
             "url": PS + "access-control/lab-unprotected-admin-functionality"},
            _cwe(862),
        ],
    },
    "XSS": {
        "remember": [
            "Let the template engine escape output; do not put user text into innerHTML, |safe, mark_safe or dangerouslySetInnerHTML.",
            "Set text with textContent; if HTML is really needed, clean it with a vetted sanitizer such as DOMPurify.",
            "Add a Content Security Policy as a second line of defence.",
        ],
        "resources": [
            {"kind": "Guide", "title": "Cross Site Scripting Prevention Cheat Sheet", "source": "OWASP",
             "url": OWASP_CS + "Cross_Site_Scripting_Prevention_Cheat_Sheet.html"},
            {"kind": "Guide", "title": "Cross-site scripting", "source": "PortSwigger Web Security Academy",
             "url": PS + "cross-site-scripting"},
            {"kind": "Practice lab", "title": "Reflected XSS into HTML context with nothing encoded",
             "source": "PortSwigger Web Security Academy", "url": PS + "cross-site-scripting/reflected/lab-html-context-nothing-encoded"},
            _cwe(79),
        ],
    },
    "NOSQL_INJECTION": {
        "name": "NoSQL injection",
        "plain": "Request data goes straight into a database query, so an attacker can add operators that change what it matches.",
        "remember": [
            "Do not pass request objects straight into a query; convert each value to the type you expect.",
            "Reject input that contains query operators such as $ne or $where, and avoid $where entirely.",
            "Validate requests against a schema before they reach the database.",
        ],
        "resources": [
            {"kind": "Guide", "title": "NoSQL injection", "source": "PortSwigger Web Security Academy", "url": PS + "nosql-injection"},
            {"kind": "Guide", "title": "Injection Prevention Cheat Sheet", "source": "OWASP",
             "url": OWASP_CS + "Injection_Prevention_Cheat_Sheet.html"},
            _cwe(943),
        ],
    },
    "WEAK_HASH": {
        "remember": [
            "Store passwords with a slow password hash: Argon2id, scrypt or bcrypt, never MD5, SHA-1 or a plain SHA-256.",
            "Use the library's built-in salt; do not invent your own scheme.",
            "For file or data integrity use SHA-256 or stronger; MD5 and SHA-1 have known collisions.",
        ],
        "resources": [
            {"kind": "Guide", "title": "Password Storage Cheat Sheet", "source": "OWASP", "url": OWASP_CS + "Password_Storage_Cheat_Sheet.html"},
            {"kind": "Reference", "title": "hashlib: scrypt and pbkdf2_hmac", "source": "Python documentation",
             "url": "https://docs.python.org/3/library/hashlib.html"},
            _cwe(916),
        ],
    },
    "OPEN_REDIRECT": {
        "remember": [
            "Only redirect to relative paths inside your site, or to an allow-list of destinations.",
            "Send an id for the destination instead of a full URL.",
            "Reject targets that start with // or contain a scheme such as https:.",
        ],
        "resources": [
            {"kind": "Guide", "title": "Unvalidated Redirects and Forwards Cheat Sheet", "source": "OWASP",
             "url": OWASP_CS + "Unvalidated_Redirects_and_Forwards_Cheat_Sheet.html"},
            {"kind": "Guide", "title": "DOM-based open redirection", "source": "PortSwigger Web Security Academy",
             "url": PS + "dom-based/open-redirection"},
            _cwe(601),
        ],
    },
}

GENERAL = {
    "remember": [
        "Treat everything that comes from a request, a file or another service as untrusted until it is checked.",
        "Fix the root cause in the code, then add a test that sends the bad input so it cannot come back.",
    ],
    "resources": [
        {"kind": "Guide", "title": "OWASP Top 10", "source": "OWASP", "url": "https://top10.owasp.org/"},
        {"kind": "Guide", "title": "OWASP Cheat Sheet Series", "source": "OWASP", "url": "https://cheatsheetseries.owasp.org/"},
    ],
}


def for_type(type_key: str | None, cwe: str | None = None) -> dict:
    """{remember: [...], resources: [...]} for a vulnerability type; general advice plus its CWE when unknown."""
    entry = LEARN.get(type_key or "")
    if entry:
        return {"remember": list(entry["remember"]), "resources": [dict(r) for r in entry["resources"]]}
    out = {"remember": list(GENERAL["remember"]), "resources": [dict(r) for r in GENERAL["resources"]]}
    m = re.search(r"(\d+)", cwe or "")
    if m:
        out["resources"].append(_cwe(int(m.group(1))))
    return out
