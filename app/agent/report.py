"""Technical PDF for one agent run: finding, root cause, patch, exploit test, test comparison, gates, verdict."""

from __future__ import annotations

import io

from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer

from app.fleet.report_pdf import STYLES, WIDTH, _doc, _grid, _head, _p, _sev
from app.report_pdf import _kv_table, _t

VERDICT = {"verified": "VERIFIED", "failed": "FAILED", "inconclusive": "INCONCLUSIVE", "none_found": "NO FINDING"}
STATUS = {"pass": "Pass", "fail": "FAIL", "skip": "Not measured", "warn": "Warning"}


def _diff_block(diff: str):
    return Preformatted(diff[:12000] or "(no change)", STYLES["mono"], maxLineLength=110)


def render(run: dict, user: dict) -> bytes:
    res = run["state"].get("result") or {}
    ws = res.get("workspace") or {}
    buf = io.BytesIO()
    report_id = f"SF-AGENT-{run['id']}"
    doc, page = _doc(buf, f"{run['repo']} agent run {run['id']}", f"Security repair of {run['repo']}",
                     f"KAIZEN agent evidence report  {report_id}")
    out: list = []
    _head(out, "Security repair report", f"{run['repo']}  ·  commit {(ws.get('sha') or '')[:12]}", report_id)

    out.append(_p("1. Verdict", "h2"))
    out.append(_kv_table([
        ("Verdict", VERDICT.get(res.get("verdict") or "", (res.get("verdict") or run["status"]).upper())),
        ("Why", "; ".join(res.get("reasons") or [res.get("error") or "-"])),
        ("Repository", f"{run['repo']} at {ws.get('ref') or '-'} ({ws.get('sha') or '-'})"),
        ("Model", (res.get("tools") or {}).get("model") or "-"),
        ("Duration", f"{res.get('seconds', '-')} s, {res.get('commands', 0)} commands, {res.get('model_calls', 0)} model calls"),
        ("Isolation", (res.get("tools") or {}).get("isolation") or "-"),
        ("Prepared for", user.get("name") or user["login"]),
    ]))

    fixes = res.get("fixes") or []
    if len(fixes) > 1:
        out.append(_p("Vulnerabilities repaired in this run", "h2"))
        out.append(_grid(["#", "Vulnerability", "Location", "Verdict"],
                         [[str(n), x["finding"].get("title") or "-", f"{x['finding'].get('file')}:{x['finding'].get('line')}",
                           VERDICT.get(x["verdict"], x["verdict"].upper())] for n, x in enumerate(fixes, 1)],
                         [10 * mm, 65 * mm, 60 * mm, 35 * mm]))
        out.append(_p("Sections 2 to 5 and 7 describe fix 1; the patch in section 4 covers every fix that held.", "small"))

    f = res.get("finding")
    if f:
        out.append(_p("2. Finding", "h2"))
        out.append(_kv_table([
            ("Weakness", f"{f.get('cwe') or '-'}  {f.get('title')}"), ("Severity", (f.get("severity") or "").capitalize()),
            ("Location", f"{f.get('file')}, line {f.get('line')}"), ("Found by", f.get("scanner") or "-"),
            ("Source", f.get("source") or "-"), ("Sink", f.get("sink") or "-"),
        ]))
    rc = res.get("root_cause")
    if rc:
        out.append(_p("3. Root cause", "h2"))
        out.append(_p(rc.get("plain") or ""))
        out.append(Spacer(1, 4))
        out.append(_kv_table([("Data flow", "  ->  ".join(rc.get("flow") or []) or "-"),
                              ("Technical", rc.get("technical") or "-"), ("Fix strategy", rc.get("fix_strategy") or "-"),
                              ("Must keep working", rc.get("must_keep") or "-")]))
    patch = res.get("patch") or {}
    if patch.get("diff"):
        out.append(_p("4. Patch", "h2"))
        out.append(_p(f"{patch.get('explanation') or ''} Attempts: {patch.get('attempts')}.", "small"))
        out.append(Spacer(1, 3))
        out.append(_diff_block(patch["diff"]))
    sec = res.get("security_test") or {}
    if sec.get("code"):
        out.append(_p("5. Exploit test", "h2"))
        b, a = sec.get("before") or {}, sec.get("after") or {}
        out.append(_kv_table([("File", sec.get("path") or "-"), ("Attack input", sec.get("attack") or "-"),
                              ("Original code", f"{b.get('outcome', '-')}: {b.get('detail', '')}"),
                              ("Patched code", f"{a.get('outcome', '-')}: {a.get('detail', '')}" if a else "not run")]))
        out.append(Spacer(1, 3))
        out.append(Preformatted(sec["code"][:8000], STYLES["mono"], maxLineLength=110))
    out.append(_p("6. Existing tests", "h2"))
    rows = []
    for b, a in zip(res.get("baseline") or [], (res.get("patched") or [None] * len(res.get("baseline") or []))):
        rows.append([b["kind"], f"{b['passed']} passed, {b['failed']} failed" if b.get("ran") else "did not run",
                     f"{a['passed']} passed, {a['failed']} failed" if a and a.get("ran") else "-"])
    out.append(_grid(["Suite", "Before the patch", "After the patch"], rows or [["-", "no test suite found", "-"]],
                     [30 * mm, 70 * mm, 70 * mm]))
    cmp_ = res.get("comparison") or {}
    if cmp_.get("new_failures"):
        out.append(_p("New failures: " + "; ".join(cmp_["new_failures"][:20]), "small"))
    if res.get("gates"):
        out.append(_p("7. Gates", "h2"))
        out.append(_grid(["Gate", "Result", "Evidence"],
                         [[g.get("label") or g["key"], STATUS.get(g["status"], g["status"]), g.get("detail") or ""]
                          for g in res["gates"]], [45 * mm, 22 * mm, WIDTH - 67 * mm]))
    out.append(_p("8. Limitations", "h2"))
    from app.agent.evidence import LIMITATIONS
    for line in LIMITATIONS:
        out.append(Paragraph(_t(line), STYLES["bullet"], bulletText="•"))
    doc.build(out, onFirstPage=page, onLaterPages=page)
    return buf.getvalue()
