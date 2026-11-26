# ADR-SKL-01: A run is offered its skills at admission, pinned in its spec, on the loop and every engine

- Related: ADR-OWD-07 (tool identity and pinning), ADR-SEC-01 (supply-chain trust), FB-11 (a pinned skill reads its admitted content), SK1 (skill packs), SKILLPACKS (pack libraries in the run images), CPS SK-1..SK-5 (who sees a custom skill).

## Context

A manual test (2026-11-09) asked for a summary report of a sports tournament as a PDF. The default chat (the system assistant, `agentIdPlaceholder`) handed the work to a Claude Code child (run `03344798…`). The worker logged `engine.assembly … engine=claude_code granted=59 skills=0`, so the engine built the PDF without the built-in `pdf` pack. Its run spec held `x.pipeshub.skills: []`, and so did its parent's (`589b32ed…`). The parent loop run had the skill tools (`run.catalog … from_skills=5`, `promptSections=[…,preloaded_skills]`): the loop read the builtins live from the graph.

The design fault was that **two executors had two answers to "which skills does this run have"**:

1. The loop built its catalog at claim time from the graph, using the person's visibility (builtins and the person's own skills) narrowed to the agent's assignment. Nothing pinned it.
2. An engine mounts exactly the run spec's pins (`durable._pins`). The spec takes its skills from the run definition, and the definition listed only the agent's explicit assignment. The assistant has none, and neither has an agent without an assignment, so a default chat's engine got nothing.

A child inherits its loop parent's definition (`child-pinning.ts`, `loopParent`), so the empty list carried on into every `code_task` child.

## Decision

**D1. One rule says what a run is offered** (`app/agents/agent_loop/skills/offer.py`):
- the org's built-in packs, always;
- plus the skills the run's agent assigns;
- or, when the agent assigns none (the assistant, or an agent without an assignment), the person's own skills instead.

A skill the loop would not advertise is left out: disabled, deprecated, or not for the model to load (`is_advertised`; a command-only pack such as `/summarize` belongs to the person). When skills are off for the deployment (`PIPESHUB_ENABLE_SKILLS`) or for the org (Labs `ENABLE_SKILLS`), the run is offered nothing, as the loop then has nothing.

**D2. The run definition grants the offer, and admission pins it.** `GET /agents/{key}/run-definition` (the assistant's and every saved agent's) adds the offer to the agent's own skills (`with_offered_skills`), seeding the org's builtins first if this process has not yet done so, and pins each skill by the digest of its content (`pin_skill_content`). Node already maps the definition's skills one for one into `x.pipeshub.skills` (name, `bundleDigest`), so Node needs no change. The spec is the single answer: every attempt, resume and replay of the run mounts the same bundles, and a `code_task` child inherits them with its parent's definition.

**D3. Each executor reads the pins its own way.**
- An engine mounts them where it looks for skills, as SK1 and SKILLPACKS set up: Claude Code's `skills` folder, Codex's extra skill root, the ACP agents' configured skills link, and `ph skill` for an engine without a skills tool.
- The loop reads them through `PinnedSkillManager`, narrowed by `ScopedSkillManager` to the pinned names.

An assignment narrows the person's own skills away, never the builtins: `ScopedSkillManager` now lets builtins through, so today's non-durable loop offers what D1 says too.

**D4. A pin holds while the skill is still offered** (`GraphSkillStore.in_force`). A pinned skill holds when it exists and is not disabled, and one of these is true: it is a builtin, the run's agent assigns it, or it belongs to the run's person (`PinnedSkills(viewers=…)`) while the agent assigns none.
- Before this, the check required an agent→skill edge for any run with an agent, so the assistant's pins could never hold.
- Deleting or disabling a skill after admission still narrows runs in flight, as before (FB-11). An edit still does not.

**D5. The engine is told its skills in one line each.** After its task instructions, the engine gets the name and description of each mounted skill, taken from the pinned `SKILL.md` and cut at 240 characters, and is told to read a skill's `SKILL.md` when the task matches it (`mounted_skills_instructions`). Skill bodies never go into the prompt. The engine loads one when it needs it (progressive disclosure).

## As built

- Python:
  - the rule: `skills/offer.py` (`offered`, `offered_skills`, `skill_viewers`);
  - the definition: `run_definition.with_offered_skills`; the route `_assistant_run_definition` and `get_agent_run_definition` with `_skills_on`;
  - pins holding: `graph_store.in_force(…, viewers)`, `bundle_source.PinnedSkills(viewers=)`, `items/run.SkillReader(viewers=)`, `tool_sources.SkillsSource`;
  - the loop: `scoped_manager.ScopedSkillManager` (builtins pass);
  - the engine: `pinned_labels.MountedSkills.descriptions` and `mounted_skills_instructions`, appended in `engines/durable.py`.
- Tests:
  - H `tests/unit/agents/agent_loop/skills/test_fixskills_offer.py`: the offer, a disabled pack, skills off, an agent's skills, the engine's mount and listing, the pins holding, the loop, the assistant route;
  - the route test in `tests/unit/agents/test_run_definition_skills.py`;
  - `tests/unit/agents/adapter/test_scoped_skill_manager.py`;
  - N `tests/modules/agent-harness/skill-bundle.test.ts`: the assistant's packs pinned in the spec;
  - Docker `test_fixskills_claude_code_sees_the_builtin_pdf_pack_a_default_chat_run_pins` in `tests/unit/harness/engines/test_real_claude_code_controller.py`: real Claude Code, scripted model.

## Deviations

- A run admitted before this change keeps its spec: an engine run of it still mounts nothing it did not pin, and its loop reads the builtins live, as before.
- The person's own skills are offered without a cap. A person with hundreds of skills gets a long spec and listing; the loop's prompt cap (`PIPESHUB_SKILLS_CATALOG_RENDER_LIMIT`) is not applied to the pins.
- For a migrated org whose effective set is the authority (CPD-10 R3), a saved agent's definition is narrowed to the installations the set holds ready (`onlyGranted`). The capability migration installs a skill only from an agent's skill edges, so such an org's builtins reach a saved agent's run only once the set holds them. The assistant is not reviewed, so a default chat is not affected. Follow-up FIXSKILLS-FU-1: install the release's builtin packs for every migrated org.
- When an agent's last assigned skill is unassigned mid-run and that skill is the run's person's own, it still holds: the agent now assigns none, so a fresh run would be offered it too (D1).

## Owner amendments

None.

## Status

**Accepted** (2026-11-10), decided by FIX-SKILLS from first principles, long term, UX-first, on the report that the built-in pdf skill was not loaded. Not yet seen on a human-test stack.
