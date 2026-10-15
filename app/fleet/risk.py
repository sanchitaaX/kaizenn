"""Risk score: how worried to be about a repository, from 0 (clean) to 100.

Each open vulnerability adds a weight by severity. Public repositories and vulnerabilities left open for more
than 30 days count more. The total is squeezed into 0-100 so the score always reads the same way.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

SEVERITIES = ("critical", "high", "medium", "low")
WEIGHTS = {"critical": 10.0, "high": 5.0, "medium": 2.0, "low": 0.5}
PUBLIC_FACTOR = 1.3
OLD_FACTOR = 1.2
OLD_AFTER = timedelta(days=30)
SCALE = 35.0

# (lowest score, band). Checked top to bottom.
BANDS = ((80, "critical"), (50, "high"), (20, "medium"), (1, "low"), (0, "clean"))
BAND_LABELS = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low", "clean": "Clean"}
BAND_MEANING = {
    "critical": "Act today: serious problems an attacker could use now.",
    "high": "Act this week: real problems that need a fix soon.",
    "medium": "Plan a fix: problems that matter but are harder to abuse.",
    "low": "Keep an eye on it: small issues only.",
    "clean": "No open problems found.",
}


def vuln_weight(severity: str, public: bool, first_seen: datetime | None, now: datetime) -> float:
    w = WEIGHTS.get(severity, 0.0)
    if public:
        w *= PUBLIC_FACTOR
    if first_seen and now - first_seen > OLD_AFTER:
        w *= OLD_FACTOR
    return w


def score_from_total(total: float) -> int:
    if total <= 0:
        return 0
    return max(1, min(100, round(100 * (1 - math.exp(-total / SCALE)))))


def repo_score(vulns: list[dict], public: bool, now: datetime) -> int:
    """vulns: open vulnerabilities as {severity, first_seen (datetime or None)}."""
    return score_from_total(sum(vuln_weight(v["severity"], public, v.get("first_seen"), now) for v in vulns))


def band(score: int | None) -> str:
    s = score or 0
    for low, name in BANDS:
        if s >= low:
            return name
    return "clean"


def org_score(scores: list[int]) -> int:
    """Root mean square of the repository scores: an average that gives risky repositories more weight."""
    if not scores:
        return 0
    return round(math.sqrt(sum(s * s for s in scores) / len(scores)))
