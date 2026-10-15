"""BRIEF.md: the project brief written into the repository itself, so it is kept with the code.

The file starts with a marker line, so SecureFix only ever replaces a BRIEF.md it wrote, and the agent can read
the brief from a downloaded repository when nobody saved one on the dashboard.
"""

from __future__ import annotations

from pathlib import PurePosixPath

PATH = "BRIEF.md"
MARKER = "<!-- securefix-brief: written by SecureFix from the project brief. Edit it in SecureFix or here. -->"
MAX_FILE_CHARS = 8000   # each attached file in BRIEF.md
MAX_DOC_CHARS = 60_000

FENCE = {".py": "python", ".js": "javascript", ".jsx": "jsx", ".ts": "typescript", ".tsx": "tsx", ".json": "json",
         ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".ini": "ini", ".sql": "sql", ".html": "html", ".csv": "csv"}


def render(full_name: str, brief: str, files: list[dict]) -> str:
    """Markdown for BRIEF.md from the paragraph and the attached files ([{name, content}])."""
    out = [MARKER, "", f"# Project brief: {full_name}", ""]
    out += [brief.strip() or "_No description yet._", ""]
    if files:
        out += ["## Attached notes", ""]
        for f in files:
            text = f["content"].strip()
            cut = len(text) > MAX_FILE_CHARS
            text = text[:MAX_FILE_CHARS]
            ext = PurePosixPath(f["name"]).suffix.lower()
            out += [f"### {f['name']}", ""]
            if ext in (".md", ".txt", ".rst", ".pdf", ".docx", ""):
                out += [text, ""]
            else:
                fence = "````" if "```" in text else "```"
                out += [fence + FENCE.get(ext, ""), text, fence, ""]
            if cut:
                out += ["_(shortened; the full file is attached to the brief in SecureFix)_", ""]
    out += ["---", "SecureFix sends this brief with every fix it writes for this repository, so the changes keep "
                   "the behaviour described here."]
    return "\n".join(out)[:MAX_DOC_CHARS].rstrip() + "\n"


def is_ours(text: str) -> bool:
    return text.lstrip().startswith(MARKER)


def to_brief(text: str) -> str:
    """The brief an agent run should use, from a BRIEF.md SecureFix wrote ('' for any other file)."""
    if not is_ours(text):
        return ""
    body = text.lstrip()[len(MARKER):].strip()
    body = body.rsplit("\n---\n", 1)[0].strip()
    if body.startswith("# Project brief:"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
    return body.strip()
