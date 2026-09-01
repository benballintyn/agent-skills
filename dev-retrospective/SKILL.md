---
name: dev-retrospective
description: Capture durable, reusable engineering learnings from a work session into a two-layer store — general lessons in ~/.agents/LEARNINGS.md and project-specific ones in the repo's LEARNINGS.md. Use at the end of a phase or session, after a review or postmortem surfaces something worth not relearning, when the user says "retro", "retrospective", "capture that lesson", or "what did we learn", and before a handoff so the next session inherits the lessons rather than repeating them.
---

# Dev retrospective

Turn what this session *cost you* into something the next session doesn't pay
for again. Two files, two audiences:

- `~/.agents/LEARNINGS.md` — **general**: true of any codebase, any language.
  Read by every agent on this machine, on every project.
- `<repo>/LEARNINGS.md` — **project-specific**: this codebase's traps, its
  toolchain's sharp edges, contracts that are easy to break here. Committed,
  so it travels with the repo and reaches whoever clones it.

Both are loaded through `AGENTS.md`, which Claude Code and Codex both read.

## 1. Harvest — what actually earns a line

A learning qualifies only if it **cost something real**: a bug shipped, a
review catch, a CI failure, an hour lost. Not a plan, not a preference, not
something you merely noticed.

Good sources, in descending order of value:

- **A fix that was wrong.** The most valuable entries in any retrospective are
  the times the repair introduced or relocated the problem.
- **A review finding you would not have found yourself**, and *why* your own
  process missed it.
- **A test that passed while constraining nothing** — and what revealed it.
- **A failure only one environment showed** (one Python version, CI but not
  local, a clean checkout).
- **Time lost to tooling** that a single sentence would have prevented.

Disqualify ruthlessly:

- Anything the code, types, or tests already enforce. If the repo can check it,
  the repo should check it — write the check, not the learning.
- Restatements of the language's or framework's own documentation.
- Anything true only of one task ("the seq was 325") — that's session state and
  belongs in a handoff, not here.
- Advice you cannot act on ("be careful with async").

## 2. Sort — general or project?

Ask: **would this be true in a different repo, in a different language?**

- Yes → general. "Mutate a test before trusting it" travels anywhere.
- No → project. "`npm test` runs `--coverage`; a bare `vitest run` skips the
  gate" is only true here.

When a lesson has a general core and a local instance, write the general form
in the global file and, if the local detail is load-bearing, a one-line pointer
in the project file. Do not duplicate the prose.

## 3. Write — pithy, actionable, evidenced

One line per learning. A bolded imperative, then the evidence that earned it,
in a single sentence. Group under a short heading.

```markdown
- **Mutate a test before trusting it.** Reading tests never revealed a vacuous
  assertion this session; mutating the code they cover revealed all five.
```

Rules:

- **Imperative, not observation.** "Verify X" beats "X can be wrong".
- **Keep the evidence.** A rule without its scar gets deleted by someone who
  doesn't believe it. One clause is enough.
- **No dates, no session references, no PR numbers** in the general file — it
  outlives them. The project file may cite a PR when the reasoning is long and
  lives there.
- **Two lines maximum.** If it needs a paragraph, it is a decision record
  (`DECISIONS.md`) or a document, not a learning.

## 4. Merge, don't accumulate

Before adding, read the existing file.

- If an entry already covers it, **sharpen that entry** — better evidence, a
  crisper imperative — rather than appending a near-duplicate.
- If two entries have become the same lesson, merge them.
- If an entry is now enforced by tooling, **delete it** and say so: the gate
  replaced it.
- If the file is drifting past ~30 lines, the weakest entries are no longer
  earning their place. Prune.

A learnings file that only grows is one nobody reads.

## 5. Wire it up (first time in a repo)

The global file exists already. For a project file:

1. Create `<repo>/LEARNINGS.md` with an `# <project> — learnings` heading.
2. Add a pointer in the repo's `AGENTS.md` so both platforms load it:
   `Hard-won lessons specific to this repo: [LEARNINGS.md](LEARNINGS.md).`
3. Commit it. It is durable knowledge like `AGENTS.md`, not session state like
   a handoff doc — those stay untracked.

## 6. Report

Tell the user what you filed and where, in a couple of lines. Quote the new
entries verbatim so they can veto or reword. If you pruned or merged anything,
say which and why — silently deleting someone's hard-won lesson is worse than
never writing it.
