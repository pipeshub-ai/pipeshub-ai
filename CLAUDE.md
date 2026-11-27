@AGENTS.md

## Claude Code

- Reviewing a pull request or diff: use the `pr-review` skill (`.claude/skills/pr-review/SKILL.md`). CI's review workflow reads the same file.
- Adding or changing a connector: use the `add-connector` skill (`.claude/skills/add-connector/SKILL.md`), which follows [CONNECTOR.md](./CONNECTOR.md). Review a connector PR against [CONNECTOR_CHECKLIST.md](./CONNECTOR_CHECKLIST.md).
- Folders with a module guide (listed under "Module guides" in `AGENTS.md`) have a `CLAUDE.md` that imports their `AGENTS.md`, so it loads when you open a file there.
- Shared agent config is tracked in `.claude/{settings.json,rules,skills,agents}`. Personal settings go in `.claude/settings.local.json`, which stays untracked.
