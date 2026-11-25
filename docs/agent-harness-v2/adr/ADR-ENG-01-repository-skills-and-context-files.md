# ADR-ENG-01: An engine offers no repository skill; a repository's context files are lower-trust content

- Related: CF-02 and CF-04 (`harness_core/testing/engines/contract.py`), R27REST-FU-3 (the cases on every real
  binary), C3.10 (`harness_core/engines/repository_instructions.py`: the controller's fenced repository instructions),
  [ADR-OWD-06](ADR-OWD-06-pipeshub-contract-and-ph.md) (repository config is never loaded as configuration),
  [ADR-OWD-11](ADR-OWD-11-adapter-contract.md) (the adapter contract).
- Found by: R30-ENG (R30ENG-FU-2). Decided in round 31.

## Context

A job's workspace is a repository the person or a job brought in. CF-02 plants every engine's own project files in it
before the engine starts (settings, MCP servers, hooks, agents, a model, instruction files and skills) and checks that
none of them changes the run; CF-04 checks that the run's own instructions and skills reach the model and the
repository's do not. On the real binaries, R30-ENG fixed what changed the run's configuration (dsh's instruction
candidates, Gemini CLI's and Qwen Code's settings layer, Codex's project trust). Two things were left on Codex
0.160.0, Gemini CLI 0.63.0, Qwen Code 0.26.5 and Goose 1.57.0:

- **Repository skills.** Each lists the repository's skills (`.agents/skills`, and its own `.codex/`, `.gemini/`,
  `.qwen/` or `.goose/skills`) beside the run's, so a repository can name a skill "use for everything" and have the
  engine load its text as a skill: instructions at the trust of the run's own skills.
- **Project context files.** Gemini CLI, Qwen Code and Goose read the repository's `AGENTS.md`, `GEMINI.md` or
  `.goosehints` into the model's context, as each does on a person's own machine.

None of them ran a repository hook or MCP server, changed its tool list or its model.

Three ways were weighed: (a) delete or rename the repository's skill and context paths when the session opens,
(b) send the run's instructions as prompt text and point each engine's context loader at nothing, (c) accept and
document. (a) makes the launch depend on the workspace's contents (and the files are the person's work, which the
job may need to read or edit); (b) changes what CF-04's session instructions mean for those engines and still leaves
the skill listing. A skill and a context file are not the same risk: a skill is offered to the model as a capability
the run grants, a context file is text the model reads, as it would read the file itself with a read tool.

## Decision

- **D1. No repository skill is offered.** Each engine's skills are the run's (the skills mount, ADR-SKL-01) and the
  engine's own bundled ones where its profile allows them; a repository's skill folders are switched off through the
  engine's own supported setting or flag, in the config PipesHub renders at launch. Never by deleting, renaming or
  masking paths in the sandbox: the launch does not depend on what the workspace holds. Where a pinned version has no
  such switch, its repository skills are recorded as lower-trust content in its profile and an upstream follow-up is
  opened; CF-02 and CF-04 then name the exception.
- **D2. A repository's project context files are lower-trust content.** An engine's own loader may read the
  repository's `AGENTS.md`, `GEMINI.md`, `QWEN.md` or `.goosehints`, as the model could read the file with its read
  tool. They carry no authority: every action they prompt goes through the tool gateway's grant and approvals as any
  other, and the run's instructions keep their own place (the engine's global instruction file or system prompt). Each
  profile names the files its engine reads; the contract asserts exactly those are read as content and that nothing
  else of the repository reaches the model, no hook or MCP server of it runs, and the tool list and model are the
  run's.
- **D3. Configuration stays sealed.** A repository's settings, MCP servers, hooks, agents and model never change the
  run (CF-02, unchanged): D2 covers text the model reads, never configuration an engine obeys.

## As built

- Contract: `repository_mentions` attributes each mention to the file it came from (`REPO_MARKER@<source>`);
  `EngineAdapterContract.repository_context_files` is the D2 set a lane declares; CF-02 fails on any other mention, on
  a change to the set read, on a repository skill, a hook, a tool-list change or a model change; CF-04 fails on a
  repository skill offered.
- Per engine: see the engine's profile comment and the table below.

| Engine (pinned) | D1: repository skills | D2: context files read |
| --- | --- | --- |
| Claude Code 2.1.294 | none offered (its settings sources, CF-02) | none |
| opencode 1.18.34 (native and ACP) | none offered | none |
| dsh 0.2.2-rc.1 | none offered | none |
| Qwen Code 0.26.5 | off: `skills.disabledLevels: [project]` in the run's user settings (UNION-merged across scopes, so a workspace cannot take it back; the run's skills are the user level) | `AGENTS.md` |
| Codex 0.160.0 | exception: `.agents/skills` and `.codex/skills` listed (scope repo); its config disables a skill only by name or path | none (`project_doc_max_bytes = 0`) |
| Gemini CLI 0.63.0 | exception: `.agents/skills` listed in the trusted workspace it needs for MCP servers; `skills.disabled` takes names only | `AGENTS.md`, `GEMINI.md` |
| Goose 1.57.0 | exception: `.agents/skills` listed; no skill setting | `.goosehints`, `AGENTS.md` (its Project Hints) |

The exceptions are R31ENG-FU-1 (upstream: a scope switch for repository skills in each engine). Checked on the
binaries: Qwen's settings schema (`disabledLevels`: project, user, extension, bundled, UNION-merged), Gemini's skill
manager (`disabledSkills` by name), Goose's strings (no skill setting; `.agents/skills (in current project)`), and
Codex per R30-ENG.

## Deviations

- Codex, Gemini CLI and Goose list a repository's skills (D1's exception above), until upstream has a switch.

## Owner amendments

None.

## Status

**Accepted** 2026-11-12: the decision on R30ENG-FU-2 (round 31), built in R31-ENG.
