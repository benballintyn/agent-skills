---
name: handoff
description: Write or update a phase handoff document capturing verified project state — test/lint/CI status, what shipped, decisions, and the next-phase plan — plus the project's decision record. Takes an optional audience argument, `fresh` (default) or `compacted`. Use at the end of a work phase or session, when the user says "write a handoff", "hand off", "wrap up this phase", or "update the handoff doc", or before pausing a multi-phase project so the next session can resume cold.
---

# Phase handoff

Produce a handoff good enough that the next session can resume the project
without re-deriving anything. Two artifacts, both **local and untracked** unless
the project has an established, owner-approved convention for tracking them:

- `HANDOFF.md` — describes NOW: state, sharp edges, how to resume.
- `DECISIONS.md` — describes WHY: the load-bearing decisions that got us here.

Update existing docs in place — never accumulate dated copies. If the project
has data-sensitivity rules (financial data, PII, secrets), the docs inherit
them: exclude via `.git/info/exclude` and any project guard mechanism.

## 0. Audience argument

The skill takes an optional argument: `fresh` (default) or `compacted`.

- **`fresh`** — the reader is a cold session with zero context. Weight the
  orientation half: what the project is, why it exists, where everything
  lives, the mental model. Full kickoff prompt.
- **`compacted`** — the reader is a continuation of the current session whose
  context was summarized. It retains the high-level arc (goals, major
  decisions, tone of the collaboration) but has lost the details: exact
  numbers, file paths, recent command output, in-flight subtasks, unstated
  TODO items. Weight the precision half: current exact state, what changed
  most recently and why, precise paths/commands, anything decided or learned
  in the last stretch that a summary would flatten. Do not re-explain what
  the project is. The kickoff prompt shrinks to a "you are mid-flight;
  re-anchor on these" note.

Either way, keep `HANDOFF.md` itself serving BOTH audiences: a stable
structure whose top is orientation (fresh readers start there) and whose
"Current state" / "Sharp edges" sections carry the detail (compacted readers
jump straight there). The argument changes where you spend effort and what
the kickoff prompt looks like, not which file you write.

## 1. Verify state first (never document unverified claims)

- Run the full test suite and linters; record exact counts.
- Check CI on the default branch if a remote exists (`gh run list --limit 5`);
  say "no remote / local only" explicitly when that's the case.
- If anything is red, say so prominently under Known Issues — a handoff that
  hides red state is worse than none.

## 2. Gather what happened

- Current branch, commits since the last handoff (`git log --oneline`), open
  PRs and unmerged branches, TODOs introduced this phase.
- Anything the user decided or corrected during the phase — that feeds the
  decision record, not just the handoff.

## 3. HANDOFF.md structure

- **Current state** — date, branch, test counts, lint/CI. One glance = health.
- **What shipped this phase** — one line each.
- **Sharp edges** — gotchas that will bite a session that doesn't know them
  (data-handling rules, verification habits, trap patterns hit this phase).
- **Known issues / open items** — failing tests, deferred work, standing
  caveats.
- **Next phase plan** — concrete steps with acceptance criteria.
- **How to resume** — exact commands: env setup, tests, the acceptance gate.
- A pointer to `DECISIONS.md` near the top: decisions live there, not here.

The doc describes NOW — replace stale content. Durable rationale moves to the
decision record.

## 4. DECISIONS.md — the decision record

If the project has no decision record, create one; otherwise update it. This
is where "why are things the way they are" lives, and it must inform future
sessions **without entrenching** — course corrections stay possible and cheap,
but they must never silently erase the reasoning that got us here. The
mechanics below are adapted from industry ADR practice (Nygard/MADR):

- **One entry per load-bearing decision**, numbered (D1, D2, …; numbers are
  never reused), dated, with who made the call (owner vs. agent vs. evidence).
  Skip trivia — an entry for a formatter choice buries the entry for the data
  model.
- **Each entry answers three things**: the *context* (what forces were in
  play — this is what future readers need to judge whether circumstances have
  changed), the *decision* itself, and the *consequences* — costs and
  accepted trade-offs listed alongside benefits, never an advocacy document.
  Compressed prose is fine; three labelled paragraphs are not required if one
  tight paragraph carries all three.
- **Statuses**: entries are Accepted by default once written. To change
  course, do not edit the old entry — add a new entry that states the new
  decision and why circumstances changed, and mark the old one
  `Superseded by D-n` (or `Deprecated` when there's no replacement). The
  audit trail of reversals is itself information: a decision reversed twice
  is telling you something about the problem.
- **Corrections vs. reversals**: fixing a typo or adding a clarifying note is
  fine; changing what was decided or why is not. When in doubt, supersede.
- **Owner decisions are recorded verbatim where the phrasing matters** (a
  short quote captures intent better than a paraphrase) and are not
  re-litigated by future sessions without new evidence — if new evidence
  exists, surface it to the owner and record the outcome either way.
- Keep an eye out during every phase for decisions made in conversation that
  never got recorded — the record drifts stale by omission, not by edits.

## 5. Hand the successor a kickoff prompt

As the final step, print a ready-to-paste prompt in its own fenced code block.

For **fresh**: it must stand alone — what the project is and its goal, where
things stand, what to read first in order (HANDOFF.md, DECISIONS.md, key repo
docs), exact resume commands to confirm a green baseline, and the instruction
to review before editing. Tailor it to this project — name real paths, real
commands, the actual next task.

For **compacted**: a short re-anchoring note instead — "you are mid-task on X;
the details that didn't survive summarization are in HANDOFF.md §Current
state and DECISIONS.md D-n..D-m; re-verify with <command> before continuing" —
plus the one or two in-flight specifics most likely to have been flattened
(the exact failing test, the half-finished refactor, the number under
discussion).
