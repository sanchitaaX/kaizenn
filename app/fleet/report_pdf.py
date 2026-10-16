"""Technical security reports as PDFs (reportlab platypus), in the style of the per-commit report.

render()       organization report: scope, results by severity and weakness class, repositories at or above
               the risk threshold, every major finding with its ID, CWE and location, remediation status
render_repo()  repository report: details, results, then every open finding with its CWE, location,
               description, remediation and code change, then fixed findings and references

Every finding has a stable ID (SF-<number>) so a report line can be traced back to the dashboard.
Blues and greys only, like the app.
"""

from __future__ import annotations

import difflib
import io
import json

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app import db
from app.fleet import catalog, risk
from app.report_pdf import ACCENT, ACCENT_TINT, FAINT, INK, LINE, SOFT, STYLES, SURFACE, _kv_table, _t

WIDTH = 170 * mm
DEEP = colors.HexColor("#2f3a9e")
MID = colors.HexColor("#5e6ad2")
PALE = colors.HexColor("#e3e6f8")
REMOVED = colors.HexColor("#eef0f4")
SEV_FILL = {"critical": DEEP, "high": MID, "medium": PALE, "low": SURFACE}
SEV_INK = {"critical": colors.white, "high": colors.white, "medium": DEEP, "low": SOFT}
SOURCE = {"demo": "KAIZEN demo data", "codeql": "GitHub code scanning (CodeQL)"}
STATUS = {"open": "Open", "fix_saved": "Fix written, waiting for review", "pr_open": "Pull request open",
          "fixed": "Fixed", "dismissed": "Not exploitable (checked by the agent)"}
MAX_DETAILED = 40  # findings written out in full in a repository report


def _p(text, style="body") -> Paragraph:
    return Paragraph(_t(text), STYLES[style])


def _n(x) -> str:
    return f"{x:,}" if isinstance(x, int) else str(x if x is not None else "-")


def _fid(vuln_id: int) -> str:
    return f"SF-{vuln_id}"


def _cwe_url(cwe: str | None) -> str | None:
    if not cwe or "-" not in cwe:
        return None
    return f"https://cwe.mitre.org/data/definitions/{cwe.split('-')[1]}.html"


def _grid(header: list[str], rows: list[list], widths: list[float], extra: list | None = None) -> Table:
    data = [[Paragraph(_t(h), STYLES["cellk"]) for h in header]]
    for row in rows:
        data.append([c if isinstance(c, (Paragraph, Table)) else Paragraph(_t(c), STYLES["cell"]) for c in row])
    tbl = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1)
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT_TINT),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        *(extra or []),
    ]))
    return tbl


def _sev(sev: str) -> Table:
    """A small filled severity label for a table cell."""
    cell = Table([[Paragraph(f"<font color='{SEV_INK.get(sev, INK).hexval()}'><b>{_t(sev.capitalize())}</b></font>",
                             STYLES["cell"])]], hAlign="LEFT")
    cell.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), SEV_FILL.get(sev, SURFACE)),
                              ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                              ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]))
    return cell


def _diff(old: str, new: str, start: int, path: str) -> Table:
    """Unified diff with line numbers: removed lines grey, added lines blue."""
    rows, styles = [["", "", Paragraph(_t(path), STYLES["cellk"])]], [("BACKGROUND", (0, 0), (-1, 0), SURFACE)]
    a, b = old.splitlines(), new.splitlines()
    old_no = new_no = start
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append([str(old_no), str(new_no), " " + a[i1 + k]])
                old_no, new_no = old_no + 1, new_no + 1
            continue
        for k in range(i1, i2):
            styles.append(("BACKGROUND", (0, len(rows)), (-1, len(rows)), REMOVED))
            rows.append([str(old_no), "", "-" + a[k]])
            old_no += 1
        for k in range(j1, j2):
            styles.append(("BACKGROUND", (0, len(rows)), (-1, len(rows)), ACCENT_TINT))
            rows.append(["", str(new_no), "+" + b[k]])
            new_no += 1
    data = [rows[0]] + [[Paragraph(r[0], STYLES["mono"]), Paragraph(r[1], STYLES["mono"]),
                         Paragraph(_t(r[2]).replace(" ", "&nbsp;") or "&nbsp;", STYLES["mono"])] for r in rows[1:200]]
    tbl = Table(data, colWidths=(10 * mm, 10 * mm, WIDTH - 20 * mm), hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TEXTCOLOR", (0, 1), (1, -1), FAINT),
        *styles,
    ]))
    return tbl


def _doc(buf: io.BytesIO, title: str, subject: str, footer: str) -> tuple[SimpleDocTemplate, callable]:
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=20 * mm, title=title, author="KAIZEN", subject=subject)

    def page(canvas, d):
        canvas.saveState()
        canvas.setFillColor(MID)
        canvas.rect(0, A4[1] - 6, A4[0], 6, stroke=0, fill=1)
        canvas.setFont(STYLES["small"].fontName, 7.5)
        canvas.setFillColor(FAINT)
        canvas.drawString(20 * mm, 12 * mm, footer)
        canvas.drawRightString(190 * mm, 12 * mm, f"Page {d.page}")
        canvas.setStrokeColor(LINE)
        canvas.line(20 * mm, 15 * mm, 190 * mm, 15 * mm)
        canvas.restoreState()

    return doc, page


def _head(out: list, title: str, sub: str, report_id: str) -> None:
    out.append(_p(title, "title"))
    out.append(_p(f"{sub}  ·  report {report_id}", "sub"))
    out.append(Spacer(1, 6))


def _severity_table(counts: dict, total: int) -> Table:
    rows = [[_sev(k), _n(counts.get(k, 0)), f"{round(100 * counts.get(k, 0) / total) if total else 0}%"]
            for k in risk.SEVERITIES]
    rows.append([_p("Total", "cellk"), _n(total), "100%" if total else "0%"])
    return _grid(["Severity", "Open findings", "Share"], rows, [40 * mm, 35 * mm, 25 * mm])


def _scoring_rows(threshold: int | None = None) -> list[tuple[str, str]]:
    rows = [
        ("Severity weights", "critical 10, high 5, medium 2, low 0.5 per open finding"),
        ("Weight factors", "x1.3 public repository; x1.2 finding open more than 30 days"),
        ("Repository score", f"round(100 x (1 - e^(-total weight / {int(risk.SCALE)}))), 0 to 100"),
        ("Organization score", "root mean square of repository scores"),
        ("Bands", "Critical 80+, High 50-79, Medium 20-49, Low 1-19, Clean 0"),
    ]
    if threshold is not None:
        rows.append(("Major finding", f"critical or high finding in a repository scoring {threshold} or more"))
    return rows


# --------------------------------------------------------------------------- organization report


def render(title: str, s: dict, top: list[dict], major: dict, user: dict) -> bytes:
    buf = io.BytesIO()
    now = db.now()
    report_id = f"SF-ORG-{now:%Y%m%d-%H%M}"
    doc, page = _doc(buf, f"{title} technical security report", f"Security assessment of {title}",
                     f"KAIZEN technical security report  {report_id}")
    out: list = []
    _head(out, "Technical security report", f"{title}  ·  {now:%d %B %Y, %H:%M} UTC", report_id)

    out.append(_p("1. Scope", "h2"))
    out.append(_kv_table([
        ("Organization", title),
        ("Repositories in scope", f"{_n(s['repos_total'])} ({_n(s['repos_scanned'])} with code scanning results)"),
        ("Data source", "GitHub code scanning (CodeQL) alerts; KAIZEN demo data for the demo organization"),
        ("Last scan", s["last_scan_label"]),
        ("Major finding threshold", f"risk score {major['min_score']} or more"),
        ("Prepared for", user.get("name") or user["login"]),
        ("Generated", f"{now:%Y-%m-%d %H:%M} UTC"),
    ]))

    out.append(_p("2. Results", "h2"))
    out.append(_kv_table([
        ("Organization risk score", f"{s['org_score']} / 100 ({s['org_band_label']}); "
                                    f"{'+' if s['score_change'] > 0 else ''}{s['score_change']} vs 4 weeks ago"),
        ("Repositories at Critical or High", f"{_n(s['repos_at_risk'])} of {_n(s['repos_total'])}"),
        ("Open findings", _n(s["open_total"])),
        ("Fixed in the last 30 days", f"{_n(s['fixed_30d'])}" + (f", mean time to remediate {s['avg_fix_days']} days"
                                                                 if s["avg_fix_days"] is not None else "")),
    ]))
    out.append(Spacer(1, 6))
    out.append(KeepTogether([_p("2.1 Open findings by severity", "cellk"), Spacer(1, 3),
                             _severity_table(s["open"], s["open_total"])]))
    out.append(Spacer(1, 8))
    band_rows = [[risk.BAND_LABELS.get(k, "Not scanned"), _n(s["bands"].get(k, 0))]
                 for k in ("critical", "high", "medium", "low", "clean", "unscanned")
                 if k != "unscanned" or s["bands"].get(k)]
    out.append(KeepTogether([_p("2.2 Repositories by risk band", "cellk"), Spacer(1, 3),
                             _grid(["Band", "Repositories"], band_rows, [40 * mm, 35 * mm])]))
    out.append(Spacer(1, 8))
    trend = s["trend"]
    out.append(KeepTogether([_p("2.3 Organization score, weekly", "cellk"), Spacer(1, 3), _grid(
        [p["label"] if i < len(trend) - 1 else "Now" for i, p in enumerate(trend[-7:], len(trend) - 7)],
        [[str(p["score"]) for p in trend[-7:]], [_n(p["open"]) for p in trend[-7:]]],
        [WIDTH / 7] * 7)]))
    out.append(_p("Row 1: organization score. Row 2: open findings at that date.", "small"))

    out.append(_p("3. Findings by weakness class", "h2"))
    rows = [[t["title"], catalog.info(t["type"]).get("cwe") or "-", _n(t["total"]), _n(t["critical"]), _n(t["high"]),
             _n(t["medium"]), _n(t["low"])] for t in s["by_type"]]
    out.append(_grid(["Weakness", "CWE", "Open", "Crit.", "High", "Med.", "Low"], rows,
                     [62 * mm, 22 * mm, 18 * mm, 17 * mm, 17 * mm, 17 * mm, 17 * mm]))
    out.append(Spacer(1, 6))
    out.append(_p("Remediation by class", "cellk"))
    out.append(Spacer(1, 3))
    out.append(_grid(["Weakness", "Remediation"],
                     [[t["title"], catalog.info(t["type"])["fix"]] for t in s["by_type"]],
                     [50 * mm, WIDTH - 50 * mm]))

    out.append(_p(f"4. Repositories at risk score {major['min_score']} or more", "h2"))
    out.append(_p(f"{_n(major['total'])} repositories, {_n(major['vulns_total'])} critical or high findings.", "small"))
    out.append(Spacer(1, 4))
    if major["items"]:
        out.append(_grid(["Repository", "Team", "Score", "Crit.", "High", "Open", "Last scan"],
                         [[r["full_name"], r["team"], f"{r['risk_score']} {r['band_label']}", _n(r["open_critical"]),
                           _n(r["open_high"]), _n(r["open_total"]), r["last_scan_label"]] for r in major["items"]],
                         [55 * mm, 26 * mm, 24 * mm, 13 * mm, 13 * mm, 14 * mm, 25 * mm]))
    else:
        out.append(_p("No repository is at or above the threshold."))

    out.append(_p("5. Major findings", "h2"))
    shown = sum(len(r["vulns"]) for r in major["items"])
    out.append(_p(f"Critical and high findings in the repositories above ({_n(shown)} listed"
                  + (f" of {_n(major['vulns_total'])}" if shown < major["vulns_total"] else "") + ").", "small"))
    out.append(Spacer(1, 4))
    rows = []
    for r in major["items"]:
        for v in r["vulns"]:
            rows.append([_fid(v["id"]), _sev(v["severity"]), v["title"], v["cwe"] or "-",
                         Paragraph(_t(r["full_name"]) + "<br/>" + _t(f"{v['file']}:{v['line']}"), STYLES["cell"]),
                         v["first_seen_label"]])
    if rows:
        out.append(_grid(["ID", "Severity", "Weakness", "CWE", "Location", "Found"], rows,
                         [16 * mm, 20 * mm, 36 * mm, 17 * mm, 61 * mm, 20 * mm]))

    out.append(_p("6. Riskiest repositories", "h2"))
    out.append(_grid(["#", "Repository", "Team", "Score", "Crit.", "High", "Open"],
                     [[str(i), r["full_name"], r["team"], f"{r['risk_score']} {r['band_label']}", _n(r["open_critical"]),
                       _n(r["open_high"]), _n(r["open_total"])] for i, r in enumerate(top, 1)],
                     [9 * mm, 62 * mm, 28 * mm, 25 * mm, 15 * mm, 15 * mm, 16 * mm]))

    out.append(_p("7. Remediation status", "h2"))
    out.append(_kv_table([
        ("Fixes committed through KAIZEN", _n(s["committed_fixes"])),
        ("Fixes written, waiting for review", _n(len(s["saved_fixes"]))),
        ("Findings fixed in the last 30 days", _n(s["fixed_30d"])),
    ]))
    if s["saved_fixes"]:
        out.append(Spacer(1, 4))
        out.append(_grid(["ID", "Severity", "Weakness", "Location"],
                         [[_fid(f["vuln_id"]), _sev(f["severity"]), f["title"], f"{f['full_name']}  {f['file']}:{f['line']}"]
                          for f in s["saved_fixes"][:30]],
                         [16 * mm, 20 * mm, 40 * mm, 94 * mm]))

    out.append(_p("Appendix A. Scoring", "h2"))
    out.append(_kv_table(_scoring_rows(major["min_score"])))
    refs = [(t["title"], catalog.info(t["type"]).get("cwe")) for t in s["by_type"]]
    refs = [(name, cwe) for name, cwe in refs if _cwe_url(cwe)]
    if refs:
        out.append(_p("Appendix B. References", "h2"))
        for name, cwe in refs:
            out.append(Paragraph(f"{_t(cwe)} {_t(name)}: <font color='{ACCENT.hexval()}'>{_t(_cwe_url(cwe))}</font>",
                                 STYLES["bullet"], bulletText="•"))

    doc.build(out, onFirstPage=page, onLaterPages=page)
    return buf.getvalue()


# --------------------------------------------------------------------------- repository report


def render_repo(found: dict, fixes: dict, user: dict) -> bytes:
    r, vulns = found["repo"], found["vulns"]
    open_ = [v for v in vulns if v["status"] != "fixed"]
    fixed = [v for v in vulns if v["status"] == "fixed"]
    buf = io.BytesIO()
    now = db.now()
    report_id = f"SF-R{r['id']}-{now:%Y%m%d-%H%M}"
    doc, page = _doc(buf, f"{r['full_name']} technical security report", f"Security assessment of {r['full_name']}",
                     f"KAIZEN technical security report  {report_id}")
    out: list = []
    _head(out, "Repository security report", f"{r['full_name']}  ·  {now:%d %B %Y, %H:%M} UTC", report_id)

    out.append(_p("1. Repository", "h2"))
    out.append(_kv_table([
        ("Repository", r["full_name"]),
        ("Team / owner", r["team"] or "-"),
        ("Visibility", "Public" if r["public"] else "Private"),
        ("Language", r["language"] or "Not detected"),
        ("Default branch", r["default_branch"]),
        ("Data source", SOURCE.get(vulns[0]["source"], "GitHub code scanning (CodeQL)") if vulns
         else ("GitHub code scanning (CodeQL)" if r["source"] == "github" else SOURCE["demo"])),
        ("Last scan", r["last_scan_label"]),
        ("Code scanning", {"scanned": "Results read", "no_code_scanning": "Not available for this repository",
                           "error": "Last read failed"}.get(r["scan_status"], r["scan_status"])),
        ("Prepared for", user.get("name") or user["login"]),
    ]))

    out.append(_p("2. Results", "h2"))
    out.append(_kv_table([
        ("Risk score", f"{r['risk_score']} / 100 ({r['band_label']})"),
        ("Open findings", _n(r["open_total"])),
        ("Fixed findings", _n(len(fixed))),
    ]))
    out.append(Spacer(1, 6))
    counts = {k: r[f"open_{k}"] for k in risk.SEVERITIES}
    out.append(_severity_table(counts, r["open_total"]))
    if open_:
        out.append(Spacer(1, 8))
        out.append(_p("Open findings index", "cellk"))
        out.append(Spacer(1, 3))
        out.append(_grid(["ID", "Severity", "Weakness", "CWE", "Location", "Status"],
                         [[_fid(v["id"]), _sev(v["severity"]), v["title"], v["cwe"] or "-", f"{v['file']}:{v['line']}",
                           STATUS.get(v["status"], v["status"])] for v in open_],
                         [16 * mm, 20 * mm, 36 * mm, 17 * mm, 52 * mm, 29 * mm]))

    out.append(_p("3. Findings in detail", "h2"))
    if not open_:
        out.append(_p("No open findings."))
    for i, v in enumerate(open_[:MAX_DETAILED], start=1):
        info = catalog.info(v["type"])
        block = [_p(f"3.{i}  {_fid(v['id'])}  {v['title']}", "cellk"), Spacer(1, 3)]
        rows = [("Severity", v["severity"].capitalize()), ("Weakness", f"{v['cwe'] or 'Not classified'}  {info['name']}"),
                ("Location", f"{v['file']}, line {v['line']}"), ("First seen", v["first_seen_label"]),
                ("Status", STATUS.get(v["status"], v["status"])),
                ("Source", SOURCE.get(v["source"], v["source"]) + (f", alert #{v['alert_number']}" if v.get("alert_number") else ""))]
        if v.get("alert_url"):
            rows.append(("Alert", v["alert_url"]))
        rows += [("Description", v["description"] if v["description"] != info["plain"] else info["plain"]),
                 ("Remediation", info["fix"])]
        block.append(_kv_table(rows, widths=(32 * mm, WIDTH - 32 * mm)))
        fix = fixes.get(v["id"])
        if fix:
            state = {"draft": "drafted", "saved": "saved for review", "committed": "committed"}.get(fix["status"], fix["status"])
            label = f"Code change ({state})" + (f", branch {fix['branch']}" if fix.get("branch") else "")
            block += [Spacer(1, 4), _p(label, "small"), Spacer(1, 2),
                      _diff(fix["old_code"], fix["new_code"], fix["start_line"], v["file"])]
            proof = json.loads(fix["proof"]) if fix.get("proof") else None
            if proof and proof.get("status") == "done":
                verdict = {"proven": "all checks passed", "partial": "passed what could be checked",
                           "broken": "a check failed"}.get(proof.get("verdict"), "")
                block += [Spacer(1, 4), _p(f"Proof the fix is safe: {verdict}", "small"), Spacer(1, 2),
                          _grid(["Check", "Result", "Evidence"],
                                [[c["label"], {"pass": "Pass", "fail": "FAIL", "skip": "Not run"}.get(c["status"], c["status"]),
                                  c["detail"]] for c in proof.get("checks", [])],
                                [48 * mm, 18 * mm, WIDTH - 66 * mm])]
        elif v["source"] == "demo" and v["type"] in catalog.TYPES:
            lang = catalog.snippet_language(r["language"] or "")
            old, new = catalog.TYPES[v["type"]]["snippets"][lang]
            block += [Spacer(1, 4), _p("Remediation pattern for this weakness (no fix written for this finding yet)",
                                       "small"), Spacer(1, 2), _diff(old, new, 1, v["file"])]
        out.append(KeepTogether(block[:4]))
        out.extend(block[4:])
        out.append(Spacer(1, 10))
    if len(open_) > MAX_DETAILED:
        out.append(_p(f"{len(open_) - MAX_DETAILED} more open findings are listed in the index above.", "small"))

    if fixed:
        out.append(_p("4. Fixed findings", "h2"))
        out.append(_grid(["ID", "Severity", "Weakness", "Location", "Fixed"],
                         [[_fid(v["id"]), _sev(v["severity"]), v["title"], f"{v['file']}:{v['line']}",
                           db.humanize(v.get("fixed_at")) if v.get("fixed_at") else "-"] for v in fixed[:60]],
                         [16 * mm, 20 * mm, 40 * mm, 70 * mm, 24 * mm]))

    out.append(_p(f"{5 if fixed else 4}. Scoring", "h2"))
    out.append(_kv_table(_scoring_rows()))
    cwes = sorted({(v["cwe"], v["title"]) for v in vulns if _cwe_url(v["cwe"])})
    if cwes:
        out.append(_p("References", "h2"))
        for cwe, name in cwes:
            out.append(Paragraph(f"{_t(cwe)} {_t(name)}: <font color='{ACCENT.hexval()}'>{_t(_cwe_url(cwe))}</font>",
                                 STYLES["bullet"], bulletText="•"))

    doc.build(out, onFirstPage=page, onLaterPages=page)
    return buf.getvalue()
