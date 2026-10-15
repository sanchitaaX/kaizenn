"""Learning cards per bug type (Student / Learn mode). Taken from the fixture reports."""

from __future__ import annotations

from app import fixtures as fx


def _collect() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for report in fx.REPORTS.values():
        kind = report.get("bug_type")
        if kind and kind not in out and report.get("learning"):
            out[kind] = report["learning"]
    return out


LEARNING = _collect()


def learning_for(bug_type: str | None) -> list[dict]:
    return list(LEARNING.get(bug_type or "", []))
