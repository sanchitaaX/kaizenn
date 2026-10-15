"""Evidence package: everything a reviewer needs to check a run without trusting the agent's words.

Written to RUNS_DIR/agent/<run id>/:
  verification_report.json   repository, commit, model, tools, finding, root cause, gates, verdict, limitations
  patch.diff                 the source change (security test excluded)
  security_test.<ext>        the exploit test
  baseline_results.json      existing tests on the original code, per test
  patched_results.json       existing tests on the patched code, per test
  commands.json              every command: stage, arguments, exit code, duration, SHA-256 of its output
  logs/NN-<stage>.log        the tail of each command's output
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from app.config import settings

LIMITATIONS = [
    "Existing tests show that no regression was detected by those tests; behaviour they do not cover is not proven.",
    "The security test demonstrates one exploit path; other paths to the same weakness may exist.",
    "Commands ran on the SecureFix host in a temporary folder with a stripped environment, not in a container.",
    "The patch was written by an AI model and must be reviewed by a person before it is merged.",
]


def folder(run_id: int) -> Path:
    return Path(settings.runs_dir) / "agent" / str(run_id)


def write(run_id: int, result: dict, commands: list[dict], calls: list[dict]) -> Path:
    d = folder(run_id)
    (d / "logs").mkdir(parents=True, exist_ok=True)
    report = {k: v for k, v in result.items() if k not in ("files",)}
    report["model_calls"] = calls
    report["limitations"] = LIMITATIONS
    (d / "verification_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (d / "patch.diff").write_text((result.get("patch") or {}).get("diff") or "", encoding="utf-8")
    sec = result.get("security_test") or {}
    if sec.get("code"):
        (d / ("security_test" + Path(sec.get("path") or "x.py").suffix)).write_text(sec["code"], encoding="utf-8")
    for n, fix in enumerate(result.get("fixes") or [], 1):  # a multi-fix run: one folder per fix
        fd = d / f"fix_{n}"
        fd.mkdir(exist_ok=True)
        (fd / "patch.diff").write_text((fix.get("patch") or {}).get("diff") or "", encoding="utf-8")
        fsec = fix.get("security_test") or {}
        if fsec.get("code"):
            (fd / ("security_test" + Path(fsec.get("path") or "x.py").suffix)).write_text(fsec["code"], encoding="utf-8")
    (d / "baseline_results.json").write_text(json.dumps(result.get("baseline") or [], indent=2), encoding="utf-8")
    (d / "patched_results.json").write_text(json.dumps(result.get("patched") or [], indent=2), encoding="utf-8")
    slim = [{k: v for k, v in c.items() if k != "output"} for c in commands]
    (d / "commands.json").write_text(json.dumps(slim, indent=2), encoding="utf-8")
    for i, c in enumerate(commands, start=1):
        (d / "logs" / f"{i:02d}-{c['stage']}.log").write_text(
            f"$ {' '.join(c['cmd'])}\n# exit {c['exit']} in {c['seconds']} s, sha256 {c['sha256']}\n\n{c['output']}",
            encoding="utf-8")
    return d


def zip_bytes(run_id: int) -> bytes | None:
    d = folder(run_id)
    if not d.exists():
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(d.rglob("*")):
            if p.is_file():
                z.write(p, f"securefix-run-{run_id}/{p.relative_to(d).as_posix()}")
    return buf.getvalue()
