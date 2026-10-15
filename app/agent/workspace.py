"""Repository intake: download a repository at a pinned commit into a disposable folder and index its files.

The workspace keeps the original text of every file the agent changes, so the patch diff, the reverts between
attempts and the evidence all come from real file contents rather than from the model's description.
"""

from __future__ import annotations

import difflib
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app import github

SOURCE_EXT = {".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
              ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "typescript"}
SKIP_DIRS = {".git", "node_modules", "dist", "build", "out", "vendor", "venv", ".venv", "env", "__pycache__",
             "coverage", ".next", ".nuxt", "site-packages", "bower_components", ".tox", ".mypy_cache",
             ".pytest_cache", "target", "public", "static", "assets", "frontend/dist", "securefix-agent"}
MAX_FILE_BYTES = 300_000
MAX_FILES = 5000
TEST_DIR_NAMES = {"test", "tests", "__tests__", "spec", "specs", "e2e", "testing"}
PROTECTED_NAMES = {"package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "requirements.txt",
                   "pyproject.toml", "setup.py", "setup.cfg", "pipfile", "pipfile.lock", "poetry.lock", "tox.ini",
                   "pytest.ini", "conftest.py", "dockerfile", ".gitlab-ci.yml", "makefile", "jest.config.js",
                   "vitest.config.ts", "vitest.config.js", ".npmrc", ".env"}


class IntakeError(Exception):
    """The repository could not be prepared. The message is shown to the user."""


def is_test_file(rel: str) -> bool:
    p = Path(rel)
    name = p.name.lower()
    if any(part.lower() in TEST_DIR_NAMES for part in p.parts[:-1]):
        return True
    return (name.startswith("test_") or name.endswith("_test.py") or ".test." in name or ".spec." in name
            or name in ("conftest.py",))


def is_protected(rel: str) -> bool:
    """Files a security patch must never touch: tests, CI, dependency manifests and build configuration."""
    p = Path(rel)
    low = rel.replace("\\", "/").lower()
    if is_test_file(rel) or p.name.lower() in PROTECTED_NAMES or p.name.lower().startswith("requirements"):
        return True
    return low.startswith((".github/", ".circleci/", ".gitlab/")) or "/.github/" in low


@dataclass
class Workspace:
    repo: str
    ref: str
    sha: str
    base: Path                      # temporary parent folder (venv, reports, archives)
    root: Path                      # the repository files
    originals: dict[str, str] = field(default_factory=dict)  # rel path -> original text of changed files
    _files: list[str] | None = None

    # ------------------------------------------------------------------ files

    def files(self) -> list[str]:
        """Source files (relative, forward slashes), skipping dependencies, builds and huge or minified files."""
        if self._files is None:
            out = []
            for p in sorted(self.root.rglob("*")):
                if len(out) >= MAX_FILES:
                    break
                rel = p.relative_to(self.root)
                if any(part in SKIP_DIRS for part in rel.parts[:-1]) or not p.is_file():
                    continue
                if p.suffix.lower() not in SOURCE_EXT or p.name.endswith((".min.js", ".bundle.js", ".d.ts")):
                    continue
                try:
                    if p.stat().st_size > MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                out.append(rel.as_posix())
            self._files = out
        return self._files

    def languages(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for f in self.files():
            lang = SOURCE_EXT[Path(f).suffix.lower()]
            counts[lang] = counts.get(lang, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def path(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if not p.is_relative_to(self.root.resolve()):
            raise IntakeError(f"{rel} is outside the repository")
        return p

    def read(self, rel: str) -> str:
        return self.path(rel).read_text(encoding="utf-8", errors="replace")

    def exists(self, rel: str) -> bool:
        try:
            return self.path(rel).is_file()
        except IntakeError:
            return False

    def write(self, rel: str, text: str) -> None:
        p = self.path(rel)
        if rel not in self.originals:
            self.originals[rel] = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="")

    def revert(self) -> None:
        """Undo every change (between patch attempts)."""
        for rel, text in self.originals.items():
            p = self.path(rel)
            if text == "" and p.exists():
                p.unlink()
            else:
                p.write_text(text, encoding="utf-8", newline="")
        self.originals.clear()

    def diff(self, only: set[str] | None = None) -> str:
        chunks = []
        for rel, before in sorted(self.originals.items()):
            if only is not None and rel not in only:
                continue
            after = self.read(rel) if self.exists(rel) else ""
            chunks.extend(difflib.unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                               fromfile=f"a/{rel}", tofile=f"b/{rel}"))
        text = "".join(chunks)
        return text if not text or text.endswith("\n") else text + "\n"

    def package_json(self) -> dict:
        p = self.root / "package.json"
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except ValueError:
            return {}

    def cleanup(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)


# --------------------------------------------------------------------------- intake


def from_github(token: str | None, repo: str, ref: str | None = None) -> Workspace:
    """Download `repo` at `ref` (default branch when empty), pinned to the commit SHA it resolves to."""
    try:
        info = github.get_repo(token, repo) if token else _public_repo(repo)
        ref = ref or info.get("default_branch") or "main"
        sha = github.get_commit(token, repo, ref)["sha"] if token else _public_sha(repo, ref)
    except github.GitHubError as exc:
        if exc.status_code == 404:
            raise IntakeError("That repository was not found, or this account cannot see it.") from exc
        raise IntakeError(f"GitHub refused the request: {exc}") from exc
    base = Path(tempfile.mkdtemp(prefix="securefix-agent-"))
    try:
        root = github.download_tree(token, repo, sha, base / "src")
    except github.GitHubError as exc:
        shutil.rmtree(base, ignore_errors=True)
        raise IntakeError(f"The repository could not be downloaded: {exc}") from exc
    return Workspace(repo=repo, ref=ref, sha=sha, base=base, root=root)


def from_folder(folder: Path, repo: str = "local/fixture", ref: str = "main", sha: str = "0" * 40) -> Workspace:
    """A copy of a local folder (tests and offline demos)."""
    base = Path(tempfile.mkdtemp(prefix="securefix-agent-"))
    root = base / "src" / Path(folder).name
    shutil.copytree(folder, root, ignore=shutil.ignore_patterns("node_modules", ".git", "__pycache__"))
    return Workspace(repo=repo, ref=ref, sha=sha, base=base, root=root)


def _public_repo(repo: str) -> dict:
    return github._repo(github._get(None, f"/repos/{repo}"))


def _public_sha(repo: str, ref: str) -> str:
    return github._get(None, f"/repos/{repo}/commits/{ref}")["sha"]
