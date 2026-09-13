---
name: review-loop
description: How to run independent code-review rounds on a PR without the loop becoming the work. Use whenever a PR goes to a reviewer agent (the `reviewer` subagent or any adversarial review), when a review comes back with findings, and when deciding whether another round is warranted. Covers the severity contract, triage, the round cap, invariant-first fixes, and what a delta review may ask for.
---

# Review loop

A review round exists to find wrong behaviour a user or caller will hit. Once
a PR has none, further rounds cost more than they return. This skill fixes the
contract on both sides — what the reviewer may call IMPORTANT, and what the
author does with each verdict — so a PR converges in a few rounds instead of
ten. It was written after five PRs took 5–13 rounds each, where the first
6–8 rounds found real money-safety defects and every later round found
unreachable version-skew cases and unpinned guards that the author had asked
the reviewer to hunt.

## Severity contract (the reviewer reports, the author enforces)

- **BLOCKER** — wrong behaviour reachable against the current server, contract
  and shipped clients: data loss, a paid action repeated, a security or privacy
  hole, a run that fails where a user needs an answer. One BLOCKER means
  another round.
- **IMPORTANT** — a *reachable* defect or a test that would let a reachable
  regression ship green. "Reachable" means a caller can produce the state
  today; a status the database constrains away, a prototype-key lookup, a
  future refactor someone might make, or a survived mutant on an unreachable
  branch is **not** IMPORTANT. Neither is documentation wording, unless the
  doc instructs an operator to do something that fails.
- **NIT** — everything else: hygiene, wording, equivalent mutants, hypotheticals.
  NITs never cause a round; they are batched.

If a finding does not name a concrete input/state that produces the wrong
outcome today, it is a NIT. The reviewer must say which of the two it is.

## Round cap

1. Round 1 is a full review: gate, claims verified against installed packages,
   the author's failure patterns hunted, mutants from a green baseline.
2. After the first `MERGEABLE` verdict, allow **at most one** more round, and
   only for BLOCKERs and reachable IMPORTANTs. Merge on the next clean verdict.
3. A delta review verifies that the previous findings are closed and that the
   fix did not relocate the defect. It does **not** hunt new mutants on lines
   the delta did not touch unless the author asks for a specific line. Do not
   write "mutation-test lines I did not mutate and report every survivor" into
   a delta prompt: that guarantees a finding every round.
4. Past three rounds without a BLOCKER, stop and ask the owner whether the
   remaining items are worth a round or a follow-up issue.

## What the author does with a verdict

- Read every finding, then **triage before touching code**: BLOCKER and
  reachable IMPORTANT → this round; unreachable, hygiene, wording → one
  follow-up commit at the end or a tracked issue; equivalent mutants → note in
  the PR, do not "fix".
- For each finding taken, **write the invariant first**, as one sentence, and
  find every door it applies to (`grep` for the pattern) before editing. A fix
  applied at the door the finding names relocates the bug to the next door; the
  relocation is what makes round 7 look like round 3. If the same invariant has
  moved twice, stop patching exits: put the rule in one helper that every door
  calls at the moment it matters, then re-run the previous round's live
  scenarios.
- Pin the property, not the example: one test per invariant, started from a
  state where the assertion could fail, asserting the relation (which thing,
  when) — then run your own mutants on the lines you changed before pushing.
- Re-run the previous round's scenarios after every fix. Three of the thirteen
  mobile rounds were regressions introduced while fixing the round before.
- Keep the delta small: each fix that adds a new arm or note adds surface the
  next review will exercise. Settle the design in one round rather than
  adjusting wording across three.

## Prompting a review round

State: the head SHA, the worktree (read-only) and a unique scratch dir; the
exact gate and claimed counts; what the previous round found and what the
commit did about each item, in the reviewer's own numbering; your known
failure patterns; **what severity means** (paste the contract above); and for a
delta round, "verify the fix and the previous findings; new mutants only on
the changed lines". Ask what would make your assumptions false. Ask for the
verdict line first. Name every live resource the reviewer may touch (the test database URL and
port) and say that emulating a missing variable means pointing it at a dead port,
never unsetting it: a harness whose default URL is the developer's own database runs
against it the moment the variable is gone.

## Signals that the code, not the review, is the problem

- The same invariant found at a new door two rounds running → the state
  machine is spread across too many places; extract it (a reducer, one
  helper) before the next round.
- Mutants keep surviving on guards you wrote → tests were written from memory
  after the change; write the property test first next time.
- Findings cluster in one 1,000-line file → plan the split as its own issue;
  do not do it inside the PR under review.
