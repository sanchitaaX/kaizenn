"""Step 3, Fix: ask the LLM for a patch (unified diff) that stops the attack.

The pipeline calls this up to 1 + MAX_FIX_RETRIES times, passing what went wrong last time.

TODO (engine owner): prompt with the file, the finding and the attack; apply the diff to a copy of
the checkout with `git apply --check` before handing it to Prove.
"""

from __future__ import annotations

from app.engine.types import AttackResult, Finding, FixResult, ScanContext


def patch(ctx: ScanContext, finding: Finding, attack: AttackResult, attempt: int,
          feedback: str | None = None) -> FixResult:
    raise NotImplementedError("Fix step is not implemented yet")
