# KAIZEN rebrand and chat gamification — design

Date: 2026-10-16 · Status: approved in chat, awaiting spec review

## 1. Goal

Make the chat flow engaging so users feel involved, and reward progress with discount coupons. Rename the
product from SecureFix to **KAIZEN** ("continuous improvement") with a distinctive favicon-style mark.

Success looks like:

- With the game switched on, a user running checks in chat sees XP, a level bar, quests, a "Guess the bug"
  quiz while the agent works, and a coupon card on each level-up.
- With the game switched off (the default), the app behaves exactly as it does today.
- Every user-visible "SecureFix" reads "KAIZEN", and browser tabs show the new mark.

## 2. Decisions

| Topic | Decision |
|---|---|
| Coupon source | Demo codes only. Every coupon carries the badge "DEMO — not redeemable". No real codes are claimed. |
| What earns points | Both real security wins and chat quests, plus a "Guess the bug" quiz. |
| Setting | `game_mode` per user, off by default, toggled on the Profile page. Off means no awards and no game UI. |
| Logo | Concept A2: clean geometric K in white on a periwinkle (`#5e6ad2`) rounded tile, with one small coral (`#ff8a7a`) spark dot top-right. |
| Architecture | Server-side XP ledger with idempotent awards. Quiz questions are deterministic (no LLM call). |

## 3. Setting

- New column `users.game_mode INTEGER NOT NULL DEFAULT 0`, added with the same migration pattern `app/db.py`
  uses for other late columns.
- `ProfileIn` gains `game_mode: bool | None`. `POST /api/profile/modes` saves it. It does not count toward the
  "Keep at least one mode on" rule (that rule covers only learn/report).
- Profile page: a third switch "Quests & rewards" with the hint "Earn XP and demo coupons while you check code".
- When `game_mode` is 0: `game.award()` returns without writing, `GET /api/game` returns `{"enabled": false}`,
  `POST /api/game/quiz/...` returns 409, and the chat renders none of the game UI.
- Switching it off keeps earned XP and rewards; switching it back on resumes from there.

## 4. Data

```sql
CREATE TABLE IF NOT EXISTS xp_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    login       TEXT NOT NULL,
    kind        TEXT NOT NULL,     -- run_verified, run_clean, pr_opened, brief_saved, quiz_right, quiz_wrong, daily_chat, quest
    ref         TEXT NOT NULL,     -- run id, date, quest key ... makes the award unique
    points      INTEGER NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (login, kind, ref)
);
CREATE TABLE IF NOT EXISTS rewards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    login       TEXT NOT NULL,
    level       INTEGER NOT NULL,
    platform    TEXT NOT NULL,
    discount    INTEGER NOT NULL,  -- percent
    code        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (login, level)
);
```

`INSERT OR IGNORE` on the unique key makes every award idempotent, so polling or retries never double-count.

## 5. Earning

| Kind | Trigger | XP | `ref` |
|---|---|---|---|
| `run_verified` | Agent run finishes with verdict `verified` | 100 | run id |
| `run_clean` | Agent run finishes with verdict `none_found` | 20 | run id |
| `pr_opened` | Pull request opened from an agent run or analysis | 50 | run/analysis id |
| `brief_saved` | Project brief saved for a repository | 40 | repo |
| `quiz_right` / `quiz_wrong` | "Guess the bug" answered | 30 / 5 | run id |
| `daily_chat` | First chat message or check of the day (UTC) | 10 | `YYYY-MM-DD` |
| `quest` | Quest completed (bonus) | per quest | quest key |

Awards are made where the event already happens: the agent controller when it saves the final verdict, the
pull-request endpoints, the brief save endpoints, `/api/chat/ask` and run start for `daily_chat`. Each call is
wrapped so a game failure can never break the real action.

## 6. Quests

Defined as data in `app/game.py`. Progress is computed from `xp_events`, so there is no extra table.

| Key | Name | Goal | Bonus XP |
|---|---|---|---|
| `first_check` | First check | 1 finished agent run | 25 |
| `bug_hunter` | Bug hunter | 1 `run_verified` | 50 |
| `sharp_eye` | Sharp eye | 3 `quiz_right` | 50 |
| `brief_writer` | Brief writer | 1 `brief_saved` | 25 |
| `ship_it` | Ship it | 1 `pr_opened` | 50 |
| `daily_dojo` | Daily dojo | 3 `daily_chat` | 40 |

After any award, completed quests are awarded (idempotent through `ref` = quest key).

## 7. Levels and coupons

| Level | Name | Total XP |
|---|---|---|
| 1 | Novice | 0 |
| 2 | Apprentice | 100 |
| 3 | Practitioner | 250 |
| 4 | Defender | 450 |
| 5 | Sensei | 700 |
| 6 | Master | 1000 |

Reaching each level from 2 upward grants one reward row. Platforms rotate by level: Udemy, Coursera,
Pluralsight, TryHackMe, edX. Discounts: 20, 25, 30, 40, 50 percent. The code is
`KZN-DEMO-<PLATFORM4>-<4 hex>`, where the hex comes from a hash of login and level, so it is stable per user.
Platform names appear as text only, with no logos. Every place a coupon shows also shows
"DEMO — not redeemable".

## 8. Guess the bug

- While an agent run is in progress, once its state holds a candidate finding (file, line, snippet, rule/CWE),
  chat shows a quiz card: the snippet and four choices.
- Choices: the finding's bug class, mapped from its rule/CWE to a short name (SQL injection, Path traversal,
  Command injection, XSS, SSRF, Insecure deserialization, Hard-coded secret, Broken access control), plus three
  distractors from that list. The order is shuffled by a hash of the run id, so it stays stable on reload.
- `GET /api/game/quiz/{run_id}` returns the question (without the answer) or 404 if none is ready.
  `POST /api/game/quiz/{run_id}` with `{"choice": "..."}` returns `{"correct", "answer", "points", "state"}`.
  One answer per run (a second answer returns the first result).
- The quiz only uses the run's own data. If no class can be mapped, no quiz is shown.

## 9. API

- `GET /api/game` → `{"enabled", "xp", "level": {"n", "name", "min", "next"}, "quests": [...], "rewards": [...],
  "recent": [last 5 events]}`.
- `GET/POST /api/game/quiz/{run_id}` as above.
- Responses from actions that award XP stay unchanged. The chat calls `GET /api/game` after them and shows a
  toast for any new events and a reward card for any new reward.

## 10. Chat UI (only when `data-game="1"`)

- A slim bar above the composer: level name, XP progress to the next level, and a "Quests" chip.
- "+N XP · reason" toasts using the existing toast element.
- Quests chip: posts a checklist card into the thread (done items ticked, progress counts).
- Level-up: a reward card in the thread with platform, discount, the code in mono, a Copy button and the DEMO badge.
- Quiz card as in section 8, styled like the existing choice lists.
- Profile page: the switch, and a "Rewards" list of earned codes (with DEMO badges) when any exist.

## 11. Rebrand

- Replace user-visible "SecureFix" with "KAIZEN" in templates, page titles, chat and static JS text, PDF
  reports, pull-request bodies and titles, server messages, `BRIEF.md` and `docs/`.
- JS global `window.SecureFix` becomes `window.Kaizen`.
- Kept unchanged, so existing data and setups keep working: env var names, the `securefix.db` default path,
  cookie/session names, the `demo-repos/securefix-demo-shop` folder, and run artifacts under `runs/`.
- Mark: `static/img/kaizen.svg` (A2). Used in `<link rel="icon" type="image/svg+xml">`, a `/favicon.ico` route
  serving it, and the nav and login brand chip (replacing the shield icon). Wordmark "KAIZEN" in Urbanist 700
  with slight letter spacing.

## 12. Testing

pytest, in a new `tests/test_game.py` plus small additions to existing tests:

- Awards are idempotent (same kind/ref twice gives one row).
- With `game_mode` off, nothing is recorded, `GET /api/game` reports disabled, and the quiz returns 409.
- Level thresholds and one reward per level, with codes containing `DEMO`.
- Quest completion awards its bonus once.
- Quiz: correct, wrong, and repeated answers; no quiz when the class cannot be mapped.
- The profile toggle saves `game_mode` without tripping the at-least-one-mode rule.
- Rendered pages contain "KAIZEN" and the favicon link, and no visible "SecureFix".

Then a browser check of chat and profile with the setting off and on.

## 13. Out of scope

Real coupon codes or partner integrations, leaderboards across users, LLM-written quizzes.
