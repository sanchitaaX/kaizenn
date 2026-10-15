# KAIZEN design system

All shared styles live in `static/css/app.css`. Page CSS (if any) only adds layout for that page. Use the tokens, never raw hex.

## Rules
- Neutrals plus two hues. Accent periwinkle `--accent` (primary button, selection, links, tints). Alert coral `--alert` only for bug found / attack worked / needs attention. Safe and fixed use the accent tint with a check, never green.
- Urbanist; sizes 12, 13, 15 (body), 17, 22, 28. Sentence case. No numbers, charts or scores in the chat.
- 4px spacing grid. Radius 10 (controls), 14 (cards, bubbles), 999 (pills).

## Tokens
Colour: `--canvas --surface --surface-soft --hairline --ink --ink-soft --ink-faint --accent --accent-ink --accent-tint --accent-tint-2 --alert --alert-ink --alert-tint --alert-tint-2`
Type: `--fs-12 --fs-13 --fs-15 --fs-17 --fs-22 --fs-28`, `--font`, `--mono`
Space: `--s-1`(4) `--s-2`(8) `--s-3`(12) `--s-4`(16) `--s-5`(24) `--s-6`(32) `--s-7`(48)
Other: `--r-sm --r-md --r-pill --shadow-1 --shadow-2 --nav-h --w-chat --w-page`

Use `--accent-ink` / `--alert-ink` for text on tinted backgrounds (contrast). `--ink-faint` is for placeholders only.

## Layout
- `.page` (max 1040, padded) with `.page-narrow` (640) variant; `.page-head > (h1.page-title + p.page-sub) + actions`; `.section`, `.section-title`; `.grid` (auto-fill cards).
- Utilities: `.stack .stack-sm .stack-lg .row .row-wrap .spacer .muted .faint .small .xs .strong .sr-only .is-hidden`.

## Components
- Buttons: `.btn` + `.btn-primary | .btn-secondary | .btn-ghost`; sizes `.btn-sm .btn-lg`; `.btn-block`, `.btn-icon`, `.btn-danger-text`.
- Forms: `.field > .label + .input | .textarea | .select + .hint`. Toggle: `<label class="switch"><input type="checkbox"><span class="switch-track"></span><span>Text</span></label>`. Role picker: `.segmented > button[aria-pressed]`. File chip: `.file-chip > .file-chip-name + button.file-chip-remove`.
- Badges: `.badge.badge-new | .badge-safe | .badge-fixed | .badge-attention` (use the `badge()` macro). `.pill` for neutral tags.
- Avatars: `.avatar .avatar-sm(24) .avatar-md(32) .avatar-lg(48) .avatar-xl(72)`, `.avatar-stack`.
- Cards and lists: `.card`, `.card-raised`, `.card-flush`, `.card-head`, `.card-title`, `.card-sub`; `.list > .list-row` (a, button or div) with `.row-main > .row-title + .row-meta` and `.row-end`; `.icon-tile` (`.is-alert`).
- `.empty > .icon-tile + .empty-title + .empty-text` (use `empty_state()`).
- Feedback: `.toast` (via `Kaizen.toast(msg)`), `.skeleton` (`.skeleton-line-sm|lg`), `.spinner`.

## Chat
```
.chat-layout
  aside.chat-sidebar > .chat-sidebar-title + .history > a.history-item(.is-active) > .history-item-text
  .chat-main
    .thread-scroll > .thread
      .msg.msg-assistant > .msg-avatar + .msg-body (p, .choice-list, .quick-replies, .step-list, .result-card)
      .msg.msg-user > .msg-bubble
    form.composer > .composer-box > .composer-attachments(.file-chip) + .composer-row(button.btn-icon.btn-ghost, textarea.composer-input[data-autogrow], button.btn.btn-primary.btn-icon) ; .composer-hint
```
- Choices: `.choice-list(.is-scroll) > button.choice(.is-selected / aria-pressed) > .choice-main(.choice-title + .choice-meta) + .choice-end`.
- Quick replies: `.quick-replies > button.btn.btn-secondary.btn-sm`.
- Steps: `.step-list > .step[data-state="waiting|running|done|attack"] > .step-icon + span`. Change `data-state` to animate.
- Result: `.result-card > .result-section(.is-alert | .is-ok) > .result-title + text`; `.result-status`, `.result-lede`.
- Diff: `details.diff > summary + pre.diff-code > span.diff-line(.is-add | .is-del | .is-hunk)`.
- Mobile history drawer: a `button.btn-ghost.btn-icon.chat-sidebar-toggle[data-toggle-sidebar]` opens `.chat-sidebar`.

## Template
`{% extends "base.html" %}`; blocks: `title styles body_class nav content scripts`. Pass `active` ('chat'|'commits'|'learning'|'profile') and `user` (`name`, `login`, `avatar_url`). Nav links go to `/chat /commits /learning /profile`.

Macros (`{% from "_macros.html" import icon, badge, avatar, empty_state %}`):
`icon(name, size=16)`, `badge(state, label=none)`, `avatar(member, size='md')`, `empty_state(title, text='', action=none, icon_name='spark')` (action = `{label, href}`; also usable with `{% call %}`).
Icons: check alert spark git-commit repo paperclip send arrow user book play lock refresh external x chevron.

## JS (`window.Kaizen`)
`toast(msg)`, `api(path, {method, body})` (object bodies become JSON; FormData sent as is), `scrollToEnd(el)`. `textarea[data-autogrow]` grows automatically.
