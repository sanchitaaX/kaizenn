"""Step 4, Prove: re-run the attack on the patched code (must be blocked) and the full test suite (must pass).

TODO (engine owner): run the same attack script and `pytest -q --json-report` (or parse the summary line)
inside the sandbox on the patched copy.
"""

from __future__ import annotations

from app.engine.types import AttackResult, Finding, FixResult, ProveResult, ScanContext


def prove(ctx: ScanContext, finding: Finding, attack: AttackResult, fix: FixResult) -> ProveResult:
    raise NotImplementedError("Prove step is not implemented yet")
