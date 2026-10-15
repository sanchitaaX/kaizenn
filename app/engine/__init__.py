"""SecureFix engine: Find -> Hack -> Fix -> Prove.

Public entry points:
- run_scan(ctx, on_step)      run one commit through the four steps and return a report dict
- generate_rules(brief)       turn the project brief into security rules
- start_scan(...)             (app.engine.runner) run a scan in a background thread and store progress in SQLite

Modes (ENGINE_MODE): "replay" plays back a saved or fixture report step by step,
"live" runs the real steps, "auto" (default) replays when a recording exists and runs live otherwise.
"""

from app.engine.pipeline import run_scan
from app.engine.rules import generate_rules
from app.engine.types import STEP_NAMES, ScanContext

__all__ = ["run_scan", "generate_rules", "ScanContext", "STEP_NAMES"]
