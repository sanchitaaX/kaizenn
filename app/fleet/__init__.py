"""Organization view: every repository an organization owns, ranked by security risk.

risk.py scores, catalog.py describes vulnerability types, seed.py builds the demo organization, store.py keeps
it in SQLite, fixer.py writes fixes, sync.py reads real GitHub code scanning, report_pdf.py renders the
executive report. Routes are in app/fleet_api.py. Design: docs/superpowers/specs/2026-10-15-org-dashboard-design.md
"""
