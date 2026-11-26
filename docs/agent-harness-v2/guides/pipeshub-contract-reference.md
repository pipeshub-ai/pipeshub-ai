# The `/pipeshub` contract and the `ph` CLI: reference

Checklist item C18.06. This is the reference for what every coding-engine sandbox gets at `/pipeshub` and from the
`ph` command. Skills, prompts and scripts may rely on anything here. The decision behind it is
[ADR-OWD-06](../adr/ADR-OWD-06-pipeshub-contract-and-ph.md); the layout rules are what `ImageContract`
(`backend/python/harness_core/engines/tool_folder.py`) checks; the page the engine itself reads is
`backend/python/harness_engines/pipeshub/AGENTS.md`.

`backend/python/tests/cohesion/test_v2_guides.py` checks this page against the code: the versions below against
`backend/python/harness_engines/pipeshub/VERSION` and `PH_VERSION` in `backend/python/harness_engines/pipeshub/ph.py`,
the command table against `ph`'s own argument parser (what `ph --help` prints), and the exit codes against `ph`'s
`ERROR_CODES`. A command added to `ph` without a row here fails that test.

## Versions

| | Current |
|---|---|
| `/pipeshub` contract (`/pipeshub/VERSION`) | 1 |
| `ph` | 2.3.0 |

`ph --version` prints both: `ph 2.3.0 (/pipeshub contract 1)`
(`backend/python/tests/unit/harness_engines/pipeshub/test_ph_cli.py::test_ph1_version_names_ph_and_the_folder`).

**Rules** (versioning):

- The contract version is an integer. Within a version, changes are additive only: a new path, command, `--json`
  field or error code. A rename or removal is a new contract version, and the old name stays as an alias for at
  least one version.
- Before a turn the controller reads the image's `/pipeshub/VERSION`. A missing or unknown version refuses the turn
  with `engine_contract_unsupported` (`ImageContract` in `backend/python/harness_core/engines/tool_folder.py`).
- `ph` has its own semantic version.

### History

| Date | Contract | `ph` | Change | Commit |
|---|---|---|---|---|
| 2026-10-20 | `1.0.0` (pre-contract semver) | 1.0.0 | `ph search` (found tools), `describe`, `call --json`, `--version`; `/pipeshub/README.md`, `VERSION`, the session folder | `ff81df8306` |
| 2026-10-24 | `1.1.0` (pre-contract semver) | 1.1.0 | `ph call` asks the gateway for a tool's whole result past the engine's 24k-character cap (SPENDCONT) | `085be490d0` |
| 2026-10-26 | 1 | 2.0.0 | Contract 1 (WP-02, OWD-6): the integer version and the refusal of an unknown one; `AGENTS.md`, `tools/INDEX.md` and per-tool JSON Schema 2020-12 files; noun-verb commands `ph tools {list,search,describe,call}`; `ph search` now searches knowledge and `ph fetch` reads records; `--json` on every command. `describe` and `call` stay as older names | `ddaf1ed063` |
| 2026-11-01 | 1 | 2.0.0 | Added within contract 1: `ph skill {list,show}` (SK-05) and `ph policy check` (G-25), WP-42 | `72709a0259` |
| 2026-11-01 | 1 | 2.0.0 | Added within contract 1: `ph git {clone,fetch,push}` (WP-63) | `76c0d41471` |
| 2026-11-02 | 1 | 2.1.0 | Added within contract 1, each additive: `ph ask` and `ph publish` (WP-67, C3.12), `ph inputs get` (C5.01), safe output names (SEC-FS-02, R14-PUB); `ph tools search` ranks the run's listed tools beside the gateway's, `describe` and `call` take server-prefixed names, the session's `ph` comes first on `PATH` (WP-70). `ph` 2.1.0 names the three 2026-11-01 additions too, which had not raised its version | round 15 |
| 2026-11-10 | 1 | 2.2.0 | Added within contract 1 (WP98-FU-4): `ph tools call --save` and `--print`. A result past 24,000 characters is saved under the workspace's `notes/tool-results/` (the run's scratch folder) and `ph` prints its path and opening; a redirect to a file, `--print` and the `--json` envelope still get it whole, and `--save` adds `path` and `chars` to `data` | DEC-B |
| 2026-11-10 | 1 | 2.3.0 | Added within contract 1 (WP98-FU-1, ADR-RUN-02): `ph git clone` checks out under `/pipeshub/references` when the sandbox has that mount and leaves a link at the folder asked for (`data.checkout` names the checkout; a link it left before is replaced); `ph git adopt DIR`, which the controller runs at a turn's start, moves a checkout an older `ph` made inside the workspace onto the mount | DEC-B |

## Commands

Every command takes `--json`: it then prints exactly one JSON object on stdout, `{"ok": true, "command": ..., "data":
...}` or `{"ok": false, "command": ..., "error": {"code", "exit", "message"}}`, and exits with the same code as
without it (`test_ph18_c3_13_json_mode_prints_one_stable_envelope_per_command_golden`).

| Command | `--json` name | What it does | Tests |
|---|---|---|---|
| `ph search <words...>` | `search` | Searches company knowledge (the `knowledgegraph__search` tool) | `test_ph16_ws15_search_and_fetch_read_company_knowledge` |
| `ph fetch <record id...>` | `fetch` | Reads whole records a search showed | `test_ph16_ws15_search_and_fetch_read_company_knowledge` |
| `ph tools list` | `tools.list` | Every tool of the run, one line each, from the turn's index; needs no gateway | `test_ph14_ws15_tools_list_reads_the_turns_index_and_needs_no_gateway` |
| `ph tools search <words...> [--limit N]` | `tools.search` | The best-matching tools (1–20, default 8) | `test_ph11_tools_search_uses_the_gateways_search_tools_or_ranks_the_listed_tools` |
| `ph tools describe <tool>` | `tools.describe` | A tool's description and input schema | `test_ph12_describe_prints_the_schema_and_names_a_missing_tool` |
| `ph tools call <tool> --json '<args>' [--save] [--print]` | `tools.call` | One MCP `tools/call` to the run's tool gateway; `--json -` reads the arguments from stdin. A result past 24,000 characters (or any, with `--save`) is saved under `notes/tool-results/` and its path printed, unless stdout is a file or `--print` is given | `test_ph5_a_call_is_one_mcp_tools_call_with_the_placeholder`, `test_wp98_fu4_tool_results.py` |
| `ph describe <tool>` | `tools.describe` | Older name of `ph tools describe` | `test_ph15_ws15_the_older_names_run_the_canonical_commands` |
| `ph call <tool> --json '<args>'` | `tools.call` | Older name of `ph tools call` | `test_ph15_ws15_the_older_names_run_the_canonical_commands` |
| `ph skill list` | `skill.list` | The run's skills, one line each | `test_sk05_ph_skill_and_policy.py` |
| `ph skill show <name>` | `skill.show` | A skill's `SKILL.md`, then the paths of its other files | `test_sk05_ph_skill_and_policy.py` |
| `ph policy check <tool> [--path P]... [--hook]` | `policy.check` | Asks the tool gateway whether the run's policy allows one of the engine's own tool calls; `--hook` reads a PreToolUse hook's input and refuses when no answer comes | `test_g25_policy_check_asks_the_gateways_check_and_never_decides_itself` |
| `ph git clone <https url> [DIR] [--ref R] [--full]` | `git.clone` | Clones a public repository PipesHub fetched outside the sandbox after the person allowed it; with the references mount, the checkout is there and DIR is a link to it | `test_gitf11_ph_git_clone_and_fetch_call_the_tool_and_check_the_copy_out` |
| `ph git fetch [DIR]` | `git.fetch` | Fetches the latest of a repository `ph git clone` made (asks again) | `test_gitf05_ph_git_fetch_brings_the_new_commit_into_origin` |
| `ph git push` | `git.push` | Pushes the run's connected repository to its session branch, asking the person each time | `test_gitf12_ph_git_push_calls_repository_push` |
| `ph git adopt DIR` | `git.adopt` | Moves a checkout `ph git clone` made inside the workspace onto the references mount and leaves a link (the controller runs it at a turn's start; anything else is left alone) | `test_wp98_fu1_ph_3_adopt_moves_an_old_in_workspace_checkout_and_leaves_everything_else` |
| `ph ask <question...> [--option ANSWER]...` | `ask` | Asks the person (the run's `ask_user`); their answer is the engine's next message. A run with nobody to ask exits 69 and asks nothing | `test_ph20_ask_is_one_ask_user_call_with_the_options` |
| `ph publish <file> [--as NAME]` | `publish` | Copies a regular file into `outputs/` under a plain relative name and publishes it now as a draft (final at the turn's end); it still runs once the turn is told to stop gathering and write | `test_ph25_publish_copies_into_the_outputs_folder_then_asks_the_gateway` |
| `ph inputs get <name>` | `inputs.get` | Places a large input (over 8 MiB) under `in/` on its first read | `test_li6_ph_inputs_get_calls_the_controllers_tool` |
| `ph --version` | `version` | `ph`'s version and the contract's | `test_ph1_version_names_ph_and_the_folder` |

The tests named here are in `backend/python/tests/unit/harness_engines/pipeshub/test_ph_cli.py`,
`backend/python/tests/unit/harness_engines/pipeshub/test_sk05_ph_skill_and_policy.py`,
`backend/python/tests/unit/harness/test_gitf_repository_fetch.py` and
`backend/python/tests/unit/harness_engines/pipeshub/test_wp98_fu1_ph_references.py`.

### How a call travels

`ph` speaks MCP (streamable HTTP) to the run's own tool gateway with the attempt's placeholder token; the egress
proxy swaps the placeholder for the run's short-lived token on the way. No token is in the image, the folder or the
sandbox. A `ph tools call` gets the same grant, argument checks, approvals, flow guard and journal entry as the
engine's own MCP call: `backend/python/tests/unit/harness/gateway/test_ph_contract.py::test_pc2_a_ph_call_and_the_mcp_call_get_the_same_decision_answer_and_journal`.
A token bound to another run is refused the same way:
`backend/python/tests/unit/harness/gateway/test_ph_contract.py::test_pc3_a_token_for_another_binding_is_refused_alike`.

## Exit codes

| Exit | `--json` error code | Meaning |
|---|---|---|
| 0 | | It worked; the result is on stdout |
| 1 | `tool_error` | The tool ran and failed, or was refused |
| 2 | `refused` | `ph policy check`: the run's policy does not allow the call |
| 64 | `usage` | The command or its `--json` is not valid |
| 65 | `not_found` | The run has no tool or skill of that name |
| 69 | `unavailable` | The tool gateway could not be reached |
| 70 | `bad_answer` | The gateway's answer could not be read |
| 75 | `approval_pending` | The call waits for a person; end the turn |
| 77 | `not_permitted` | The gateway refused the sandbox's credentials (the run has ended) |
| 78 | `not_in_run` | Not inside a PipesHub engine run |

`test_ph19_c3_13_every_error_code_is_named_and_documented` checks that every exit code has one error code;
`backend/python/tests/cohesion/test_v2_guides.py` checks this table and the one in the engine's `AGENTS.md` against
them.

## The folder

| Path | What it holds |
|---|---|
| `/pipeshub/VERSION` | The contract version |
| `/pipeshub/AGENTS.md` | The engine's guide to `ph` and this folder (`README.md` is its older name) |
| `/pipeshub/tools/INDEX.md` | The turn's toolsets, one line each |
| `/pipeshub/tools/<toolset>/<tool>.schema.json` | Each granted tool's input, JSON Schema 2020-12 |
| `/pipeshub/knowledge/<id>.md` | A knowledge result too long to show whole |
| `/pipeshub/skills/` | The run's skills, each with a `SKILL.md` |
| `/pipeshub/inputs/`, `/pipeshub/outputs/` | The task's input files and its deliverables |
| `/pipeshub/workspace/` | The conversation's workspace |
| `/pipeshub/bin/ph` | The image's `ph` command, behind the session's on `PATH` |
| `/pipeshub/session/` | What the harness wrote for this turn, including how `ph` reaches the gateway |
| `/pipeshub/session/bin/ph` | This worker's `ph`, written each turn (read-only) and first on the engine's `PATH`, a login shell's included, so a `ph` fix reaches engines without a new image (WP-70) |

Everything is read-only and holds no secret. The image's half is laid out by
`backend/python/harness_engines/pipeshub/install.sh`; the controller writes the session half each turn.
