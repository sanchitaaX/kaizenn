"""Shared engine types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

STEP_NAMES = ("find", "hack", "fix", "prove")

# Step status values shown as lights on the dashboard.
IDLE, RUNNING, ATTACK, PASS, FAIL = "idle", "running", "attack", "pass", "fail"

MAX_FIX_RETRIES = 2  # BRIEF: "If the attack still works or a test breaks, it retries (max 2 retries)."


class StepCallback(Protocol):
    def __call__(self, step: str, status: str, note: str = "") -> None: ...


@dataclass
class ScanContext:
    repo: str                      # "owner/name"
    sha: str                       # full or short commit sha
    author: str = ""
    message: str = ""
    rules: list[str] = field(default_factory=list)   # enabled rule texts from the project brief
    token: str | None = None       # GitHub token, server-side only
    source_dir: str | None = None  # local checkout of the repo at this commit (live mode)
    committed_at: str | None = None
    brief: str = ""                # what the user told the chat about the project (plain text, untrusted)


@dataclass
class Finding:
    bug_type: str                  # SQL_INJECTION | PATH_TRAVERSAL | IDOR | ...
    file: str
    line: int
    explanation: str
    source: str = "scanner"        # scanner | rule
    rule: str | None = None        # brief rule text when source == "rule"
    raw: dict = field(default_factory=dict)


@dataclass
class AttackResult:
    succeeded: bool
    payload: str = ""
    evidence: str = ""


@dataclass
class FixResult:
    diff: str
    attempts: int
    ok: bool
    note: str = ""


@dataclass
class ProveResult:
    attack_blocked: bool
    tests_passed: int
    tests_total: int


Notify = Callable[[str, str, str], None]
