# agent-skills

Personal skills for Claude Code and Codex, symlinked into `~/.claude/skills/`
and `~/.codex/skills/`. The canonical home is `~/.agents/skills/`; each skill
is a directory containing a `SKILL.md` with YAML frontmatter (`name`,
`description`) followed by the skill's instructions. Both platforms read that
same layout.

| Skill | Purpose |
|---|---|
| `agent-board` | Inter-agent coordination via the local message board |
| `dev-retrospective` | Capture durable learnings — general in `~/.agents/LEARNINGS.md`, project-specific in the repo's |
| `docs-cleanup` | Audit and prune stale planning/handoff/ADR documents |
| `handoff` | Phase handoff + decision record; `fresh`/`compacted` audiences |
| `make-memory` | Write entries to the persistent knowledge graph |
| `new-project` | Scaffold a Python project to house standards |
| `preflight` | Pre-build validation: prior art, approaches, acceptance criteria |
| `release-vox` | Release workflow for the vox library |
| `review-loop` | Severity contract, triage and round cap for reviewer rounds on a PR |

Install on a new machine — link both platforms:

```bash
git clone git@github.com:benballintyn/agent-skills.git ~/.agents/skills
mkdir -p ~/.claude/skills ~/.codex/skills
for d in ~/.agents/skills/*/; do
  name="$(basename "$d")"
  ln -sfn "$d" ~/.claude/skills/"$name"
  ln -sfn "$d" ~/.codex/skills/"$name"
done
```

`agents/reviewer.md` is the `reviewer` subagent definition, symlinked from
`~/.claude/agents/reviewer.md`; the `review-loop` skill is its author-side half.

`~/.agents/AGENTS.md` is the shared instruction file (`~/.codex/AGENTS.md`
symlinks to it, and `~/.claude/CLAUDE.md` imports it). It pulls in
`~/.agents/LEARNINGS.md`, which `dev-retrospective` maintains.
