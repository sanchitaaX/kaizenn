"""Render a technical report (app/engine/report.py) as a PDF with reportlab.

Calm, print-friendly layout in the app's palette: ink text, periwinkle accents, coral only for the defect.
"""

from __future__ import annotations

import io
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

INK = colors.HexColor("#2b303b")
SOFT = colors.HexColor("#6a7180")
FAINT = colors.HexColor("#9aa0ac")
LINE = colors.HexColor("#e7e9ee")
SURFACE = colors.HexColor("#fafafb")
ACCENT = colors.HexColor("#4f5ac4")
ACCENT_TINT = colors.HexColor("#eef0fb")
ALERT = colors.HexColor("#a94a42")
ALERT_TINT = colors.HexColor("#faeeed")


def _fonts() -> tuple[str, str, str]:
    """Use Segoe UI and Consolas on Windows when present (full Unicode), else the PDF base fonts."""
    win = Path("C:/Windows/Fonts")
    try:
        if (win / "segoeui.ttf").exists() and (win / "segoeuib.ttf").exists() and (win / "consola.ttf").exists():
            if "SF-Sans" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("SF-Sans", str(win / "segoeui.ttf")))
                pdfmetrics.registerFont(TTFont("SF-Bold", str(win / "segoeuib.ttf")))
                pdfmetrics.registerFont(TTFont("SF-Mono", str(win / "consola.ttf")))
            return "SF-Sans", "SF-Bold", "SF-Mono"
    except Exception:  # a broken font file must not stop the report
        pass
    return "Helvetica", "Helvetica-Bold", "Courier"


SANS, BOLD, MONO = _fonts()
BASE_FONTS = SANS == "Helvetica"


def _t(text) -> str:
    """Escape for reportlab's mini-markup; with base fonts, swap characters they cannot draw."""
    s = escape(str(text if text is not None else ""))
    if BASE_FONTS:
        s = (s.replace("\u2192", "->").replace("\u2260", "!=").replace("\u2264", "<=").replace("\u2265", ">=")
             .replace("\u2014", "-").replace("\u2013", "-").replace("\u2018", "'").replace("\u2019", "'")
             .replace("\u201c", '"').replace("\u201d", '"').replace("\u23bf", "|"))
        s = s.encode("latin-1", "replace").decode("latin-1")
    return s


STYLES = {
    "title": ParagraphStyle("title", fontName=BOLD, fontSize=20, leading=25, textColor=INK, spaceAfter=2),
    "sub": ParagraphStyle("sub", fontName=SANS, fontSize=10, leading=14, textColor=SOFT),
    "h2": ParagraphStyle("h2", fontName=BOLD, fontSize=12.5, leading=16, textColor=INK, spaceBefore=12, spaceAfter=5),
    "body": ParagraphStyle("body", fontName=SANS, fontSize=9.6, leading=14.2, textColor=INK, alignment=TA_LEFT),
    "small": ParagraphStyle("small", fontName=SANS, fontSize=8.4, leading=11.5, textColor=SOFT),
    "cell": ParagraphStyle("cell", fontName=SANS, fontSize=8.8, leading=12, textColor=INK),
    "cellk": ParagraphStyle("cellk", fontName=BOLD, fontSize=8.8, leading=12, textColor=SOFT),
    "mono": ParagraphStyle("mono", fontName=MONO, fontSize=7.8, leading=10.2, textColor=INK),
    "bullet": ParagraphStyle("bullet", fontName=SANS, fontSize=9.6, leading=14, textColor=INK, leftIndent=12,
                             bulletIndent=2),
}

VERDICT = {"fixed": ("Fixed and verified", ACCENT, ACCENT_TINT),
           "attention": ("Needs attention", ALERT, ALERT_TINT),
           "safe": ("No defect confirmed", ACCENT, ACCENT_TINT)}


def _kv_table(rows: list[tuple[str, str]], widths=(42 * mm, 128 * mm)) -> Table:
    data = [[Paragraph(_t(k), STYLES["cellk"]), Paragraph(_t(v), STYLES["cell"])] for k, v in rows]
    tbl = Table(data, colWidths=widths, hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return tbl


def _diff_table(diff: str) -> Table:
    rows, styles = [], []
    old_no = new_no = 0
    for raw in diff.splitlines()[:400]:
        if raw.startswith(("--- ", "diff ", "index ")):
            continue
        if raw.startswith("+++ "):
            rows.append(["", "", Paragraph(_t(raw[4:].removeprefix("b/")), STYLES["cellk"])])
            styles.append(("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), SURFACE))
            continue
        if raw.startswith("@@"):
            try:
                parts = raw.split(" ")
                old_no = int(parts[1].split(",")[0].lstrip("-"))
                new_no = int(parts[2].split(",")[0].lstrip("+"))
            except (IndexError, ValueError):
                pass
            rows.append(["", "", Paragraph(_t(raw), STYLES["mono"])])
            styles.append(("TEXTCOLOR", (0, len(rows) - 1), (-1, len(rows) - 1), FAINT))
            continue
        a = b = ""
        if raw.startswith("+"):
            b, new_no = str(new_no), new_no + 1
            styles.append(("BACKGROUND", (0, len(rows)), (-1, len(rows)), ACCENT_TINT))
        elif raw.startswith("-"):
            a, old_no = str(old_no), old_no + 1
            styles.append(("BACKGROUND", (0, len(rows)), (-1, len(rows)), ALERT_TINT))
        else:
            a, b = str(old_no), str(new_no)
            old_no, new_no = old_no + 1, new_no + 1
        code = _t(raw).replace(" ", "&nbsp;") or "&nbsp;"
        rows.append([Paragraph(a, STYLES["mono"]), Paragraph(b, STYLES["mono"]), Paragraph(code, STYLES["mono"])])
    tbl = Table(rows or [["", "", Paragraph("No diff recorded", STYLES["small"])]],
                colWidths=(10 * mm, 10 * mm, 150 * mm), hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TEXTCOLOR", (0, 0), (1, -1), FAINT),
        *styles,
    ]))
    return tbl


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(SANS, 7.5)
    canvas.setFillColor(FAINT)
    canvas.drawString(20 * mm, 12 * mm, f"SecureFix technical report  {doc.title}")
    canvas.drawRightString(190 * mm, 12 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(LINE)
    canvas.line(20 * mm, 15 * mm, 190 * mm, 15 * mm)
    canvas.restoreState()


def render(report: dict) -> bytes:
    f, s = report["facts"], report["sections"]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=20 * mm, title=f["report_id"], author="SecureFix",
                            subject=f"Security review of {f['repo']} @ {f['commit']['short_sha']}")
    out = []
    p = lambda text, style="body": Paragraph(_t(text), STYLES[style])  # noqa: E731

    # Header
    label, ink, tint = VERDICT.get(f["verdict"], ("Not recorded", SOFT, SURFACE))
    out.append(p("Security review report", "title"))
    out.append(p(f"{f['repo']}  commit {f['commit']['short_sha']}  report {f['report_id']}", "sub"))
    out.append(Spacer(1, 8))
    badge = Table([[Paragraph(f"<font color='{ink.hexval()}'><b>{_t(label)}</b></font>", STYLES["cell"])]],
                  hAlign="LEFT")
    badge.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), tint), ("TOPPADDING", (0, 0), (-1, -1), 4),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 8)]))
    out.append(badge)

    out.append(p("Executive summary", "h2"))
    out.append(p(s["executive_summary"]))

    # Commit and analysis details
    c, a, fd = f["commit"], f["analysis"], f["finding"]
    out.append(p("Commit and review details", "h2"))
    out.append(_kv_table([
        ("Repository", f["repo"]),
        ("Commit", c["sha"]),
        ("Message", c["message"]),
        ("Author", c["author"]),
        ("Committed", c["committed_at"]),
        ("Requested by", a["requested_by"] or "Not recorded"),
        ("Review started", a["started"]),
        ("Review finished", a["finished"]),
        ("Engine mode", a["engine_mode"]),
        ("Duration", a["duration"]),
        ("Report generated", report["generated_at"]),
        ("Written by", f"{report['model']} (AI) from the recorded facts" if report["ai"]
         else f"Built-in template from the recorded facts ({report.get('ai_note') or 'AI off'})"),
    ]))
    if a.get("brief"):
        out.append(Spacer(1, 6))
        out.append(p("Project brief supplied by the user", "cellk"))
        out.append(p(a["brief"][:1500], "small"))

    # Finding
    out.append(p("Finding", "h2"))
    if fd.get("bug_type"):
        out.append(_kv_table([
            ("Type", fd["name"] or fd["bug_type"]),
            ("Weakness", f"{fd['cwe']}: {fd['cwe_title']}" if fd.get("cwe") else "Not classified"),
            ("OWASP Top 10", fd.get("owasp") or "Not mapped"),
            ("Severity", fd.get("severity") or "Not rated"),
            ("Location", f"{fd['file']}, line {fd['line']}" if fd.get("file") else "Not recorded"),
            ("Project rule broken", fd.get("rule") or "None"),
        ]))
    else:
        out.append(p("No defect was confirmed in this commit."))

    for key, title in (("root_cause", "Root cause"), ("vulnerable_code", "Vulnerable code"),
                       ("attack_analysis", "Attack analysis"), ("impact", "Impact")):
        out.append(KeepTogether([p(title, "h2"), p(s[key])]))

    if f.get("attack"):
        at = f["attack"]
        out.append(Spacer(1, 4))
        out.append(_kv_table([
            ("Recorded input", at.get("payload") or "Not recorded"),
            ("Recorded evidence", at.get("evidence") or "Not recorded"),
            ("Before the fix", at.get("before") or "Not recorded"),
            ("After the fix", at.get("after") or "Not recorded"),
        ]))

    # Fix
    out.append(p("Remediation", "h2"))
    out.append(p(s["remediation"]))
    if f.get("fix"):
        st = f["fix"]["stats"]
        out.append(Spacer(1, 4))
        out.append(p(f"Files changed: {', '.join(st['files']) or 'Not recorded'}.  Lines added: {st['added']}.  "
                     f"Lines removed: {st['removed']}.  Fix attempts: {f['fix'].get('attempts') or 'Not recorded'}.",
                     "small"))
        out.append(Spacer(1, 4))
        out.append(_diff_table(f["fix"]["diff"]))

    # Verification
    out.append(p("Verification", "h2"))
    out.append(p(s["verification"]))
    ver = f.get("verification")
    if ver:
        tests = ver.get("tests")
        out.append(Spacer(1, 4))
        out.append(_kv_table([
            ("Attack after fix", "Blocked" if ver.get("attack_blocked") else "Still works"),
            ("Existing tests", (f"{tests['passed']} of {tests['total']} pass" if tests else
                                ("Pass" if ver.get("tests_pass") else "Not passing"))),
        ]))

    # Actions taken
    out.append(p("Actions taken", "h2"))
    rows = [("Review requested", f"{a['started']} by {a['requested_by'] or 'unknown'}")]
    for step in f["steps"]:
        rows.append((step.get("label") or step.get("key", ""), f"{step.get('status', '')}: {step.get('note') or ''}"))
    dec = f.get("decision")
    if dec:
        if dec.get("action") == "commit":
            pr = dec.get("pull_request") or {}
            rows.append(("Decision", f"Fix committed to {dec.get('branch')}"
                         + (f" as {dec['commit_sha']}" if dec.get("commit_sha") else "")
                         + (f", pull request #{pr.get('number')}" if pr.get("number") else "")
                         + f" ({dec.get('at', '')[:16].replace('T', ' ')} UTC)"))
        else:
            rows.append(("Decision", f"Fix ignored ({dec.get('at', '')[:16].replace('T', ' ')} UTC)"))
    elif f.get("fix"):
        rows.append(("Decision", "Not taken yet"))
    if f.get("pull_request") and not (dec and dec.get("pull_request")):
        pr = f["pull_request"]
        rows.append(("Pull request", f"#{pr.get('number')} ({pr.get('status')})"))
    for q in f["follow_ups"][:8]:
        rows.append(("Follow-up question", q["text"][:200]))
    out.append(_kv_table(rows))

    # Recommendations and references
    out.append(p("Recommendations", "h2"))
    for rec in s.get("recommendations") or []:
        out.append(Paragraph(_t(rec), STYLES["bullet"], bulletText="\u2022"))
    refs = list(f.get("learning") or [])
    if fd.get("cwe"):
        num = fd["cwe"].split("-")[1]
        refs.insert(0, {"title": f"{fd['cwe']}: {fd['cwe_title']}", "source": "MITRE CWE",
                        "url": f"https://cwe.mitre.org/data/definitions/{num}.html"})
    if refs:
        out.append(p("References", "h2"))
        for r in refs[:8]:
            out.append(Paragraph(f"{_t(r.get('title'))} ({_t(r.get('source'))}): "
                                 f"<font color='{ACCENT.hexval()}'>{_t(r.get('url'))}</font>",
                                 STYLES["bullet"], bulletText="\u2022"))

    out.append(Spacer(1, 10))
    out.append(p("SecureFix does not run your application's code outside its sandbox. Every statement in this "
                 "report comes from the recorded review; anything not recorded is marked as such.", "small"))

    doc.build(out, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
