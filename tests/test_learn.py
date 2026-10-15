"""Learning material: every vulnerability type has study material, fixes carry it, and the Learning page groups
the problems in a person's own repositories.

Run from the project root:  .venv/Scripts/python -m pytest -q tests/test_learn.py
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

from app import db  # noqa: E402
from app.agent import rules  # noqa: E402
from app.config import settings  # noqa: E402
from app.fleet import catalog, learn, store  # noqa: E402
from app.main import app  # noqa: E402

TRUSTED = {"cheatsheetseries.owasp.org", "portswigger.net", "cwe.mitre.org", "community.owasp.org", "top10.owasp.org",
           "docs.github.com", "docs.python.org"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "runs_dir", str(tmp_path / "runs"))
    monkeypatch.setattr(settings, "demo_mode", True)
    db.reset_cache()
    store._ready.discard(str(tmp_path / "test.db"))
    yield
    db.reset_cache()


@pytest.mark.parametrize("kind", sorted(set(rules.TYPES) | set(catalog.TYPES)))
def test_every_type_has_a_short_list_of_lessons_from_trusted_sources(kind):
    entry = learn.for_type(kind)
    assert len(entry["remember"]) >= 2 and all(len(x) > 20 for x in entry["remember"])
    assert 2 <= len(entry["resources"]) <= learn.MAX_LESSONS
    for r in entry["resources"]:
        assert r["kind"] in ("Video", "Guide", "Practice lab", "Reference") and r["title"] and r["source"]
        u = urlparse(r["url"])
        assert u.scheme == "https" and u.netloc in TRUSTED | {"www.youtube.com"}, r["url"]
    cwe = (rules.TYPES.get(kind) or (None, catalog.TYPES.get(kind, {}).get("cwe")))[1]
    assert entry["cwe_url"] == f"https://cwe.mitre.org/data/definitions/{cwe.split('-')[1]}.html", (kind, cwe)
    videos = [r for r in entry["resources"] if r["kind"] == "Video"]
    if kind in learn.VIDEOS:  # the video comes first, with its thumbnail
        assert entry["resources"][0] == learn.VIDEOS[kind]
        vid = videos[0]["url"].rsplit("v=", 1)[1]
        assert len(vid) == 11 and videos[0]["thumbnail"] == f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    else:
        assert not videos


def test_unknown_types_get_general_advice_and_their_cwe():
    entry = learn.for_type("SOMETHING_ELSE", "CWE-352")
    assert entry["cwe_url"] == "https://cwe.mitre.org/data/definitions/352.html" and entry["resources"]
    assert learn.for_type(None)["resources"]


def test_a_demo_fix_carries_its_learning_material():
    with TestClient(app, base_url="http://testserver") as c:
        assert c.get("/auth/demo", follow_redirects=False).status_code == 302
        repo = c.get("/api/fleet/repos?source=demo&min_score=0").json()["items"][0]
        vuln = next(v for v in c.get(f"/api/fleet/repos/{repo['id']}").json()["vulns"] if v["status"] == "open")
        fix = c.post(f"/api/fleet/vulns/{vuln['id']}/fix").json()
        assert fix["learning"] == learn.for_type(vuln["type"], vuln.get("cwe"))


def test_learning_page_groups_problems_in_own_repositories():
    from app.api import _repo_learning

    alerts = [{"type": "SQL_INJECTION", "title": "SQL injection", "severity": "critical", "cwe": "CWE-89", "file": f"a{i}.py",
               "line": i + 1, "description": "x", "alert_number": None, "alert_url": None, "first_seen_at": None,
               "source": "securefix"} for i in range(3)]
    for name in ("me/one", "me/two"):
        store.save_github_repo("me", {"full_name": name, "html_url": f"https://github.com/{name}"}, alerts[:2 if name == "me/two" else 3],
                               "scanned")
    out = _repo_learning({"login": "me", "repos": []})
    assert len(out) == 1
    t = out[0]
    assert t["name"] == "SQL injection" and t["total"] == 5 and t["repo_count"] == 2 and t["unconfirmed"] == 5
    assert [r["repo"] for r in t["repos"]] == ["me/one", "me/two"]
    assert t["resources"] == learn.for_type("SQL_INJECTION")["resources"]
