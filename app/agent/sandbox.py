"""Running repository commands: temporary folders, a stripped environment, time limits, recorded results.

Every command a run executes goes through `Recorder.run`, which keeps its exit code, duration, the tail of its
output and a SHA-256 of the full output for the evidence package. The environment passed to commands holds only
what tools need to start (PATH, temp and home folders); GitHub tokens and API keys never reach repository code.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

ENV_KEEP = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "HOME",
            "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "LANG", "LC_ALL", "NUMBER_OF_PROCESSORS",
            "PROCESSOR_ARCHITECTURE")
SECRET_HINTS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIAL", "SESSION")


def clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k.upper() in ENV_KEEP and not any(h in k.upper() for h in SECRET_HINTS)}
    env.update({"CI": "1", "PYTHONDONTWRITEBYTECODE": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_NO_INPUT": "1", "NO_COLOR": "1", "FORCE_COLOR": "0", "NODE_ENV": "test",
                "npm_config_audit": "false", "npm_config_fund": "false", "npm_config_update_notifier": "false"})
    env.update(extra)
    return env


@dataclass
class Command:
    stage: str
    cmd: list[str]
    cwd: str
    exit: int | None
    seconds: float
    output: str            # last 6,000 characters
    sha256: str            # of the full output
    timed_out: bool = False

    def as_dict(self) -> dict:
        return {"stage": self.stage, "cmd": self.cmd, "cwd": self.cwd, "exit": self.exit, "seconds": self.seconds,
                "timed_out": self.timed_out, "sha256": self.sha256, "output": self.output}


@dataclass
class Recorder:
    """Runs commands for one agent run and remembers each one."""
    commands: list[Command] = field(default_factory=list)

    def run(self, stage: str, cmd: list[str], cwd: Path, timeout: int, env: dict | None = None) -> Command:
        exe = shutil.which(cmd[0]) or cmd[0]
        start = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run([exe, *cmd[1:]], cwd=str(cwd), env=env or clean_env(), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=timeout)
            out, code = (proc.stdout or "") + (proc.stderr or ""), proc.returncode
        except subprocess.TimeoutExpired as exc:
            raw = exc.stdout or ""
            out = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw) + "\n[stopped: time limit reached]"
            code, timed_out = None, True
        except OSError as exc:
            out, code = f"could not start {cmd[0]}: {exc}", 127
        c = Command(stage=stage, cmd=cmd, cwd=str(cwd), exit=code, seconds=round(time.monotonic() - start, 2),
                    output=out[-6000:], sha256=hashlib.sha256(out.encode("utf-8", "replace")).hexdigest(),
                    timed_out=timed_out)
        self.commands.append(c)
        return c


def version(cmd: list[str]) -> str | None:
    """First line of `<tool> --version`, or None when the tool is missing."""
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    try:
        out = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=20, env=clean_env())
        return ((out.stdout or out.stderr or "").strip().splitlines() or [""])[0][:120]
    except (OSError, subprocess.TimeoutExpired):
        return None
