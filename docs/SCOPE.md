# KAIZEN: Scope

**Hackathon:** HackOnTrack · **Domain:** Cyber Security
**Problem statement:** Given a vulnerable open-source repository, build an AI agent that locates the security bug, writes a patch, and proves the fix does not break the existing test suite.

## Goal

A web app where a developer or an organization signs in with GitHub and sees, in one place, which of their repositories carry security risk, what the problems are, and a reviewed fix for each one. A manager should understand the state of the whole organization in under five minutes. A developer should be able to go from "there is a problem" to "the fix is committed" in a few clicks.

## Users

| User | What they need |
|---|---|
| Executive or manager | One screen with overall risk, the riskiest repositories and the trend. A downloadable report for leadership. No jargon. |
| Developer (professional) | The vulnerabilities in their repositories, a preview of the fix (old code next to new code) and Commit, Save or Discard. |
| Student | The same, plus plain explanations and learning links, and tracking of repeated mistakes. |

## In scope

1. **GitHub sign-in** (OAuth App) with real repositories and commits from the signed-in account.
2.