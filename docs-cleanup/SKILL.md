---
name: docs-cleanup
description: Audit and clean a repo's planning/handoff/ADR/roadmap documents — delete vestigial ones, update stale ones to match the current code, mark superseded ADRs. Use when the user says "clean up the docs", "stale docs", "are these docs still accurate", or after a big phase merge leaves old plans behind.
---

# Docs cleanup

Planning docs rot fast in multi-phase autonomous work: phase plans outlive their phases, handoffs pile up, ADRs get silently superseded. Bring the docs back to truth.

## 1. Inventory

Find candidate docs: `docs/`, ADR dirs (`docs/adr/`, `docs/decisions/`), and root/stray files matching `PLAN*`, `ROADMAP*`, `HANDOFF*`, `TODO*`, `PHASE*`, `*_handoff*`, `*_plan*`, `DESIGN*`, `SPEC*`. Note each file's last touch: `git log -1 --format=%cs -- <file>`.

## 2. Classify each doc against reality

Cross-check claims against the code and git history — not vibes:
- Does it describe phases/features that already shipped? (check merged PRs, test names)
- Does it reference modules, flags, or files that no longer exist?
- Is it duplicated or obsoleted by a newer doc?

Buckets:
- **Current** — accurate, keep untouched.
- **Stale** — right doc, wrong content → update to present state.
- **Vestigial** — served its purpose, no future reader needs it → delete.
- **Superseded ADR** — never delete accepted ADRs; set `Status: Superseded by ADR-NNN` with a link.

## 3. Propose before destroying

Present the full plan (delete / update / mark-superseded lists, one-line justification each) and **get explicit confirmation before deleting anything**. Everything is recoverable from git history, but the user decides what goes.

## 4. Execute

- Apply updates; each surviving doc describes NOW.
- Delete the confirmed vestigial files.
- One focused PR on a `docs/` branch (`docs: prune stale planning docs`). Verify the commit landed before pushing.
