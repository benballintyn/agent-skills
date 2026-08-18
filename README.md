# agent-skills

Personal skills for Claude Code (and other agents), symlinked into
`~/.claude/skills/`. The canonical home is `~/.agents/skills/`; each skill is a
directory containing a `SKILL.md` with YAML frontmatter (`name`, `description`)
followed by the skill's instructions.

| Skill | Purpose |
|---|---|
| `agent-board` | Inter-agent coordination via the local message board |
| `docs-cleanup` | Audit and prune stale planning/handoff/ADR documents |
| `handoff` | Phase handoff + decision record; `fresh`/`compacted` audiences |
| `make-memory` | Write entries to the persistent knowledge graph |
| `new-project` | Scaffold a Python project to house standards |
| `preflight` | Pre-build validation: prior art, approaches, acceptance criteria |
| `release-vox` | Release workflow for the vox library |

Install on a new machine:

```bash
git clone git@github.com:benballintyn/agent-skills.git ~/.agents/skills
mkdir -p ~/.claude/skills
for d in ~/.agents/skills/*/; do ln -sfn "$d" ~/.claude/skills/"$(basename "$d")"; done
```
