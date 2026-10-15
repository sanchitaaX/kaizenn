"""Project brief -> security rules.

With an LLM configured the model writes the rules. Without one, a simple template
turns each permission sentence into a rule, so the feature still works offline.
"""

from __future__ import annotations

import re

from app import fixtures as fx
from app.engine import llm

MAX_RULES = 10
BASELINE_RULE = "Passwords and secret keys are never sent to the browser."

SYSTEM = (
    "You are a security engineer. You turn a short description of a web app into a checklist of "
    "access-control and data-protection rules that an automated tester can attack. "
    "Each rule is one plain-English sentence in sentence case, at most 15 words, written as a fact "
    "that must always be true, for example 'Only admins can delete users.' "
    "Cover who can read, change and delete each kind of data. Do not invent features the brief does not mention."
)

SCHEMA = {
    "type": "object",
    "properties": {"rules": {"type": "array", "items": {"type": "string"}}},
    "required": ["rules"],
    "additionalProperties": False,
}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+|;\s*", text or "")
    return [p.strip(" -*\t") for p in parts if len(p.strip()) > 3]


def _clean(sentence: str) -> str:
    s = re.sub(r"\s+", " ", sentence).strip().rstrip(".!?")
    return (s[:1].upper() + s[1:] + ".") if s else ""


def template_rules(brief: dict) -> list[str]:
    # Demo brief: return the hand-written rules from the fixtures.
    if (brief.get("permissions") or "").strip() == fx.BRIEF["permissions"]:
        return [r["text"] for r in fx.RULES]
    rules: list[str] = []
    for s in _sentences(brief.get("permissions", "")):
        rule = _clean(s)
        if rule and rule.lower() not in {r.lower() for r in rules}:
            rules.append(rule)
    if BASELINE_RULE.lower() not in {r.lower() for r in rules}:
        rules.append(BASELINE_RULE)
    return rules[:MAX_RULES]


def generate_rules(brief: dict) -> tuple[list[str], str]:
    """Returns (rules, source) where source is "ai" or "template"."""
    prompt = (
        f"What the app does:\n{brief.get('summary', '')}\n\n"
        f"Who uses it:\n{brief.get('users', '')}\n\n"
        f"Who can do what:\n{brief.get('permissions', '')}\n\n"
        f"Write up to {MAX_RULES} rules. "
        'Return JSON like {"rules": ["Rule one.", "Rule two."]}.'
    )
    data = llm.complete_json(SYSTEM, prompt, SCHEMA)
    if data and isinstance(data.get("rules"), list):
        rules = [_clean(str(r)) for r in data["rules"] if str(r).strip()]
        rules = [r for r in rules if r][:MAX_RULES]
        if rules:
            return rules, "ai"
    return template_rules(brief), "template"
