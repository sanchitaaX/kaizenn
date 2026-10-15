"""The security repair agent: find a real bug, patch it minimally, prove the patch with recorded test runs.

controller.py runs the pipeline (intake, baseline, discover, root cause, patch, security test, verify,
verdict). The model only proposes; verdicts come from commands and parsed results.
Design: docs/superpowers/specs/2026-10-15-security-agent-design.md
"""
