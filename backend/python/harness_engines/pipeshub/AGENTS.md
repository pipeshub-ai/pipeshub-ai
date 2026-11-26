# PipesHub in this sandbox

PipesHub gives this run its company knowledge and connected apps (Jira, Slack, Drive and others) as tools. Reach
them with the `ph` command from the shell, or as your MCP tools: both go through the same gateway, and every call is
checked, approved and recorded the same way. This sandbox has no other internet access.

Everything under /pipeshub is read-only, and nothing in it holds a secret: `ph` sends a placeholder that the
sandbox's network proxy swaps for the run's short-lived token on the way to the gateway.

## Search company knowledge

```sh
ph search q3 revenue forecast           # the best-matching passages, each labelled [index|refN]
ph fetch <record id> [<record id>...]   # read whole records a search showed you
```

## Find and call a tool

```sh
ph tools list                           # every tool of this run, one line each: name: what it does
ph tools search jira issues assigned    # the best matches for what you need
ph tools describe jira__search_issues   # its description and input schema
ph tools call jira__search_issues --json '{"jql": "assignee = currentUser()"}'
ph git clone https://github.com/<owner>/<repo>   # a public repository, fetched after the person allows it
echo '{"jql": "project = PP"}' | ph tools call jira__search_issues --json -
```

A result is printed on stdout. A large one (over 24,000 characters) is saved instead under
`notes/tool-results/`, and `ph` prints the file's path and its opening: read only the parts you need from that file.
`--save` saves any result; `--print` prints it whole.

## Use a skill

A skill is a folder of instructions (its `SKILL.md`) and the files they use. When your own tools have no way to
load one, read it with `ph`:

```sh
ph skill list                           # every skill of this run, one line each: name: when to use it
ph skill show report                    # its SKILL.md, then the paths of its other files
```

## Ask the run's policy

Your own file edits and commands are checked against the run's policy before they run (in engines that support it,
automatically). You can ask the same question yourself:

```sh
ph policy check shell                   # may this run run commands?
ph policy check edit --path /workspace/notes/plan.md
```

It prints `allowed` and exits 0, or prints why not on stderr and exits 2. Edits stay under `/workspace`, and the
web is reached through PipesHub's tools, never directly; a git repository only through `ph git clone`. Your task names
the git hosts this run may clone from, or says it cannot; when a clone is declined or refused, say so in your answer.

## For scripts: --json

`ph --json <command> ...` prints exactly one JSON object on stdout, whether the command worked or not, and exits
with the same code as without `--json`:

```json
{"ok": true, "command": "tools.search", "data": {"tools": [{"name": "...", "summary": "..."}]}}
{"ok": false, "command": "tools.call", "error": {"code": "approval_pending", "exit": 75, "message": "..."}}
```

`command` is one of `search`, `fetch`, `tools.list`, `tools.search`, `tools.describe`, `tools.call`, `skill.list`,
`skill.show`, `policy.check`, `version`.

## Exit codes

| Code | Error code (`--json`) | Meaning |
|---|---|---|
| 0 | | It worked; the result is on stdout |
| 1 | `tool_error` | The tool ran and failed, or was refused; the reason is on stderr |
| 2 | `refused` | `ph policy check`: the run's policy does not allow the call; the reason is on stderr |
| 75 | `approval_pending` | The call waits for a person to approve it. Keep working on the rest of the task meanwhile: the decision comes to you as a message, in this turn if you are still working. End your turn only when nothing is left that you can do without this call. Do not call it again until you are told the decision |
| 64 | `usage` | The command or `--json` is not valid |
| 65 | `not_found` | This run has no tool or skill of that name (`ph tools search`, `ph skill list` find one) |
| 69 | `unavailable` | The tool gateway could not be reached |
| 70 | `bad_answer` | The tool gateway's answer could not be read |
| 77 | `not_permitted` | The gateway refused the sandbox's credentials (the run has ended) |
| 78 | `not_in_run` | Not inside a PipesHub engine run |

## What is here

| Path | What it holds |
|---|---|
| `/pipeshub/VERSION` | The contract version of this folder and of `ph` (an integer) |
| `/pipeshub/AGENTS.md` | This page |
| `/pipeshub/tools/INDEX.md` | This run's toolsets, one line each, written at the start of each turn |
| `/pipeshub/tools/<toolset>/<tool>.schema.json` | Each tool's description and input, as JSON Schema 2020-12 |
| `/pipeshub/knowledge/<id>.md` | A knowledge result too long to show whole: the result shows its opening and names this file |
| `/pipeshub/skills/` | The skills this run may use (each folder has a `SKILL.md`) |
| `/pipeshub/inputs/` | The files this task started with (the workspace's `in/`) |
| `/pipeshub/outputs/` | Where deliverables go (the workspace's `out/`) |
| `/pipeshub/workspace/` | This conversation's workspace, the current folder |
| `/pipeshub/bin/ph` | The `ph` command (also on the PATH) |
| `/pipeshub/session/` | What the harness wrote for this turn, including how `ph` reaches the gateway |

Older names still work: `/pipeshub/README.md`, `/pipeshub/tools.txt`, `ph describe` and `ph call`.
