---
name: make-memory
description: Create a new entry in the persistent knowledge graph. Use when the user wants to preserve knowledge, decisions, preferences, patterns, or lessons across sessions.
---

Save the requested information to the persistent knowledge graph using the available memory MCP tools.

## Your task

Given `$ARGUMENTS`, create a well-structured knowledge page using `mcp__claude-memory__write_knowledge`.

## Steps

1. **Parse the request.** Determine what the user wants to remember from `$ARGUMENTS`. If the arguments are vague or missing, ask the user what they want to save.

2. **Choose the right page type.** Pick the most appropriate type:
   - `person` — Information about a person (colleague, collaborator, contact)
   - `project` — Facts about a project, repo, or initiative
   - `preference` — User preferences, style choices, workflow decisions
   - `pattern` — Recurring code patterns, architectural patterns, or workflows
   - `lesson` — Something learned from experience (a bug, incident, or discovery)
   - `decision` — A technical or design decision and its rationale
   - `reference` — Pointer to an external resource (URL, doc, dashboard, tool)
   - `concept` — A domain concept, mental model, or explanation worth preserving

3. **Determine project scope.** If the knowledge is specific to a particular project/repo, set `project_scope` to that project name. If it's global (user preferences, people, general concepts), set `project_scope` to null.

4. **Write the page.** Call `mcp__claude-memory__write_knowledge` with:
   - `title`: A concise, searchable title (2-6 words)
   - `page_type`: From the list above
   - `content`: Markdown content. Be concise but include enough context for future retrieval. For decisions and lessons, always include the *why*. Keep under ~2000 tokens.
   - `project_scope`: As determined above
   - `links`: If this relates to existing pages you know about, include links with appropriate relation types (`relates_to`, `supersedes`, `depends_on`, `part_of`, `learned_from`, `conflicts_with`, `used_in`, `about`)

5. **Confirm.** Tell the user what was saved, including the title, type, and scope. Keep it brief.

## Guidelines

- Prefer updating an existing page over creating a duplicate. Use `mcp__claude-memory__search_knowledge` first if you suspect a related page already exists.
- Titles should be unique and descriptive enough to find via search.
- Don't save trivial or ephemeral information. If the knowledge won't be useful in a future session, push back gently and suggest an alternative (like a TODO or a code comment).
