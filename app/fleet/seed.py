"""The demo organization: Acme Corp with 1,000 repositories and their code scanning findings.

Deterministic: the same seed always builds the same organization, so the stage demo is predictable.
Times are relative to `now`, so the data always looks fresh. Some findings were fixed in the past, which
gives the dashboard a real trend and real fix times instead of made-up numbers.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from app.fleet import catalog

SEED = 20261015
ORG = "acme-corp"
ORG_NAME = "Acme Corp"
REPO_COUNT = 1000

TEAMS = {
    "Payments": ["billing", "invoices", "ledger", "checkout", "refunds", "payouts", "tax"],
    "Identity": ["auth", "sso", "accounts", "sessions", "mfa", "directory", "permissions"],
    "Mobile": ["ios-bridge", "android-sync", "push", "offline-store", "app-config", "deeplinks"],
    "Web Platform": ["storefront", "cms", "search", "gateway", "edge", "design-system", "images"],
    "Data": ["etl", "warehouse", "metrics", "reports", "ingest", "ml-features", "events"],
    "Infrastructure": ["deploy", "k8s-operator", "secrets", "dns", "monitoring", "backup", "queue"],
    "Growth": ["referrals", "campaigns", "experiments", "emails", "onboarding", "loyalty"],
    "Internal Tools": ["admin", "support-desk", "hr-portal", "expenses", "wiki", "inventory"],
}
SUFFIXES = ["api", "service", "worker", "web", "sdk", "cli", "jobs", "ui", "core", "sync", "proxy", "lib",
            "bot", "dashboard", "scheduler"]
VARIANTS = ["v2", "eu", "us", "apac", "legacy", "next", "internal", "lite"]
LANGUAGES = (("Python", 40), ("TypeScript", 35), ("JavaScript", 25))

# Overall severity mix of open findings, and the mix in the few "hot" repositories.
SEVERITY_MIX = {"critical": 6, "high": 22, "medium": 42, "low": 30}
HOT_MIX = {"critical": 30, "high": 40, "medium": 20, "low": 10}
CLEAN_SHARE = 0.25
HOT_SHARE = 0.03
PUBLIC_SHARE = 0.30


def _pick(rng: random.Random, weights: dict[str, int]) -> str:
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


def _repo_names(rng: random.Random) -> list[tuple[str, str]]:
    """(name, team) pairs, unique."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    combos = [(f"{area}-{suffix}", team) for team, areas in TEAMS.items() for area in areas for suffix in SUFFIXES]
    rng.shuffle(combos)
    for name, team in combos:
        if len(out) >= REPO_COUNT:
            break
        out.append((name, team))
        seen.add(name)
    while len(out) < REPO_COUNT:
        base, team = rng.choice(combos)
        name = f"{base}-{rng.choice(VARIANTS)}"
        if name not in seen:
            seen.add(name)
            out.append((name, team))
    return out


def _vuln(rng: random.Random, repo: dict, mix: dict[str, int], now: datetime, fixed: bool) -> dict:
    severity = _pick(rng, mix)
    candidates = {k: t["severity"][severity] for k, t in catalog.TYPES.items() if severity in t["severity"]}
    type_key = _pick(rng, candidates)
    t = catalog.TYPES[type_key]
    lang = catalog.snippet_language(repo["language"])
    area = repo["area"].replace("-", "_")
    path = rng.choice(t["files"][lang]).format(area=area)
    if repo["language"] == "TypeScript" and path.endswith(".js"):
        path = path[:-3] + ".ts"
    if fixed:
        # Fixing picked up over the last six weeks, mostly on findings that had been open a while.
        fixed_at = now - timedelta(days=rng.choices(range(0, 42), weights=[3] * 21 + [2] * 14 + [1] * 7)[0],
                                   hours=rng.randint(2, 23))
        first_seen = fixed_at - timedelta(days=rng.randint(3, 50), hours=rng.randint(0, 23))
    else:
        first_seen = now - timedelta(days=rng.randint(1, 170), hours=rng.randint(0, 23))
        fixed_at = None
    return {
        "type": type_key, "title": t["name"], "severity": severity, "cwe": t["cwe"], "file": path,
        "line": rng.randint(8, 240), "description": t["plain"], "source": "demo",
        "status": "fixed" if fixed else "open", "first_seen": first_seen, "fixed_at": fixed_at,
    }


def build(now: datetime) -> list[dict]:
    """The demo organization: [{full_name, team, language, public, ..., vulns: [...]}]."""
    rng = random.Random(SEED)
    repos = []
    for name, team in _repo_names(rng):
        area = name.rsplit("-", 1)[0] if name.split("-")[-1] in VARIANTS else name
        area = next((a for a in TEAMS[team] if area.startswith(a)), TEAMS[team][0])
        repo = {
            "full_name": f"{ORG}/{name}", "team": team, "area": area,
            "language": _pick(rng, dict(LANGUAGES)),
            "public": rng.random() < PUBLIC_SHARE,
            "last_scan_at": now - timedelta(minutes=rng.randint(5, 60 * 36)),
        }
        roll = rng.random()
        if roll < HOT_SHARE:
            n_open, mix = rng.randint(10, 22), HOT_MIX
        elif roll < HOT_SHARE + CLEAN_SHARE:
            n_open, mix = 0, SEVERITY_MIX
        else:
            n_open, mix = rng.choices(range(1, 9), weights=[22, 20, 16, 13, 10, 8, 6, 5])[0], SEVERITY_MIX
        n_fixed = rng.choices(range(0, 6), weights=[20, 25, 22, 15, 10, 8])[0]
        repo["vulns"] = ([_vuln(rng, repo, mix, now, fixed=False) for _ in range(n_open)]
                         + [_vuln(rng, repo, SEVERITY_MIX, now, fixed=True) for _ in range(n_fixed)])
        repos.append(repo)
    return repos


def build_one(full_name: str, now: datetime) -> dict:
    """One extra repository with findings, seeded by its name (the demo account's own repositories)."""
    rng = random.Random(f"{SEED}:{full_name}")
    name = full_name.split("/")[-1]
    repo = {"full_name": full_name, "team": "My repositories", "area": name.split("-")[0] or "app",
            "language": _pick(rng, dict(LANGUAGES)), "public": True, "last_scan_at": now}
    n_open = rng.randint(4, 12)
    mix = HOT_MIX if rng.random() < 0.5 else SEVERITY_MIX
    repo["vulns"] = ([_vuln(rng, repo, mix, now, fixed=False) for _ in range(n_open)]
                     + [_vuln(rng, repo, SEVERITY_MIX, now, fixed=True) for _ in range(rng.randint(0, 3))])
    return repo
