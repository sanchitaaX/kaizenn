"""Step 2, Hack: write a small attack script for a finding and run it in a sandbox.

If the attack works, the bug is real. If not, the finding is dropped (zero-noise rule).

TODO (engine owner):
1. Ask the LLM for a pytest-style attack against the demo app (Flask test client), given the finding,
   the file and the brief rule it breaks.
2. Run it with `docker run --rm --network none` on the checkout (fallback: subprocess with a timeout,
   acceptable only because the demo repo is our own code).
3. Parse the result into AttackResult(succeeded, payload, evidence).
"""

from __future__ import annotations

from app.engine.types import AttackResult, Finding, ScanContext

UNAVAILABLE_NOTE = "Attack sandbox is not set up yet, so nothing could be proven"


def available() -> bool:
    return False


def attack(ctx: ScanContext, finding: Finding) -> AttackResult | None:
    """Run an attack for `finding`. Returns None when no attack could be run."""
    if not available():
        return None
    raise NotImplementedError
