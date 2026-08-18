---
name: preflight
description: >-
  Perform pre-build validation before starting a library, project, or ambiguous
  feature: search prior art, compare approaches, and define acceptance criteria
  before implementation. Use when the user says "preflight", wants to build a
  new project or substantial component, or has not yet validated the design.
---

# Preflight check

Goal: kill redundant or wrongly-shaped work before code exists. (Past saves: fsspec already implemented a planned filesystem-abstraction library; tomte's resolve_manual needed a full redesign because the approach wasn't validated first.)

## 1. Restate the problem

One paragraph: what's being built, for whom, and the success criterion. State assumptions explicitly. If the problem statement is fuzzy, stop and ask now — not after scaffolding.

## 2. Prior-art sweep (adopt > wrap > build)

- Search current primary sources for existing solutions; check PyPI and GitHub for maturity, recent releases, and issue activity; consult Context7 or official documentation for the strongest candidates when available.
- For each serious candidate: maturity, API fit, license, maintenance status, what fraction of the need it covers.
- Verdict with a recommendation:
  - **Adopt** — it does ≥80% of the job; building is wheel-reinvention.
  - **Wrap** — adopt the core, write a thin layer for the gap.
  - **Build** — nothing fits; say specifically why each candidate fails.

## 3. Approach options (only if building/wrapping)

2–3 genuinely different approaches with honest tradeoffs (complexity, performance, lock-in, testing difficulty). Recommend one and say why. Flag any decision that's expensive to reverse later.

## 4. Acceptance criteria

- Testable success criteria for v0 ("done when ...").
- Test milestones per phase if multi-phase.
- Explicit non-goals so scope can't silently creep.

## 5. Stop

Present the verdict, recommended approach, and criteria. **Do not write implementation code until the user confirms.** If the verdict is Adopt, record it in the persistent knowledge graph as a project-scoped decision so it is not re-litigated next session.
