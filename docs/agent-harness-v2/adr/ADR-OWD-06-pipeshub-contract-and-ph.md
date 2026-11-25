# ADR-OWD-06 — The `/pipeshub` filesystem contract and the `ph` CLI

## Context

[OWD-6](../one-way-doors.md) fixes the mount point, layout, file formats and CLI verbs inside sandboxes. Skills, AGENTS.md, user prompts and agent-written scripts hard-code them, so renaming breaks every saved skill.

## Decision

Adopt OWD-6 as written, now, while nothing outside the harness depends on the built names:

- `/pipeshub` with an integer contract `VERSION` file; the controller refuses to mount an unknown version.
- The README §6 layout: read-only `AGENTS.md`, `tools/INDEX.md`, `tools/<toolset>/<tool>.schema.json` (JSON Schema 2020-12), `skills/`, `sdk/`, `inputs/`, `outputs/`, `workspace/`, and `bin/ph`.
- `ph` with noun-verb commands (`ph tools {list,search,describe,call}`, `ph fetch`, `ph search`) and `--json` on every verb.
- Additive only within a contract version. Today's names stay as aliases.
- Engine instruction files stay empty and read-only (SEC1); PipesHub's guidance is the read-only `/pipeshub/AGENTS.md`.

This is also the **C3.01 decision** (the v1 layout): the spec layout, with the built paths kept as aliases.

The contract was defined by WP-02; its reference is [pipeshub-contract-reference.md](../guides/pipeshub-contract-reference.md). It fixes:
- `VERSION` is the integer `1`.
- The controller refuses an unknown version with `engine_contract_unsupported`.
- The old `ph` verbs (`ph search|describe|call`) stay as aliases.

## As built

Contract 1 as WP-02 defined it: the integer `VERSION` 1, refused when unknown (`ImageContract` in `backend/python/harness_core/engines/tool_folder.py`, WS-12); the README §6 layout with the older names (`README.md`, `tools.txt`) kept as links; `ph` 2.1.0 noun-verb (`ph tools {list,search,describe,call}`, `ph skill`, `ph policy`, `ph git`, `ph inputs`; `ph search`, `ph fetch`, `ph ask`, `ph publish`) with `--json` on every verb.

WP-92 (re-audit at `0eda1d7461`): the additive-only rule is a test. WS-20 (`backend/python/tests/unit/harness_engines/pipeshub/test_ws20_contract_additive.py`) reads the built surface with `backend/python/harness_engines/pipeshub/surface.py` and compares it with the pre-registered golden `tests/unit/harness_engines/pipeshub/contract_v1_surface.json` (every path with its kind, link target and mode, the session folder's names, every `ph` command's arguments and flags, the exit codes, the `<tool>.schema.json` shape): an addition passes; a removal, a rename, a changed link, mode or flag, or a new required argument fails.

Passing: `test_contract_v1.py`, `test_ph_cli.py` (PH-14..PH-19, the PH-18 `--json` golden), `test_ws20_contract_additive.py` (17), `tests/unit/harness/gateway/test_ph_contract.py`, and D `test_recs_audit_real_engine::test_r3_*` (three engines).

## Deviations

The audit at `959c3bda72` found five deviations. WP-02 closed the first four and WP-92 re-audited them against their tests: (1) the README §6 layout (`test_ws01_*`), (2) noun-verb verbs with `--json` on every verb (`test_ph14..ph19`), (3) the integer `VERSION` and (4) the refusal of an unknown version (`test_ws12_*`).

5. **The contract exists only in engine sandboxes** (UX-first, WP-92: kept as the contract's scope). A loop run's model calls its tools itself, its code sandbox has no network (in-sandbox code runs unasked because of it) and deep, multi-step work goes to the engine, so a skill that uses `ph` is engine work and there is one tool path. An engine-written skill loaded on a loop run names a `ph` that is not there; CONTRACT-V1.md's Versioning says so.

Spec items amended here (UX-first, 2026-10-26): **C3.03** — engine instruction files (`CLAUDE.md`, Codex project docs) stay empty and read-only, per SEC1, and PipesHub's guidance is the read-only `/pipeshub/AGENTS.md`, not a `CLAUDE.md` that imports it. **C3.10** — a cloned repository's `AGENTS.md`/`CLAUDE.md` stays ignored rather than merged, which is stricter than the spec. *Superseded (WP-42, 2026-11-01):* they are read as lower-trust content, as the spec asks, without loading them as configuration. The engines' native loaders stay off (SEC1), because they would give whatever a repository commits the trust of the engine's system prompt, follow `@imports` anywhere in the sandbox and let an override file replace the rest. The controller reads the two files at the workspace root and the clone's root after the turn's tree is in place (regular files only, 16 KiB each, 32 KiB in all) and adds them after the person's request in the session's first message, fenced and labelled `trust="repository"` with path, sha256 and size, under a preamble that ranks them below PipesHub's instructions and the request and says they cannot change instructions, tools, permissions or approvals. They never reach the system prompt or `/pipeshub/AGENTS.md`; the fence cannot be closed from inside; what was shown is logged with its provenance (`harness_core/engines/repository_instructions.py`, `test_ws08_repository_instructions.py`). The contract gains `ph skill {list,show}` (SK-05), `ph policy check` (G-25) and `session/ph.py`, additively within contract 1.

## Owner amendments

None.

## Status

**Accepted** (2026-10-26). Built (WP-02, WP-92): the spec layout, verbs and version refusal in WP-02; the additive-only test (WS-20) and the re-audit in WP-92.
