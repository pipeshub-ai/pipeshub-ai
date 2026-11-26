# ADR-REPO-01: Public code repositories are on by default where web access is on

- Amends: WP-63 part A (`repository_fetch`, `app/harness/repositories.py`): its default, not
  its safety design.
- Related: ADR-RUN-03 D5 (a job says what it could not do), ADR-TEST-01 (the feature registry), WP-70 (the engine is
  told `ph git clone`), WP83-FU-3 (a preset's host reads public).
- Found by: a manual test, 2026-11-12: a job asked to check out code never did, and no automated test caught it.

## Context

A tester asked the default chat, in Agent mode with Codex pinned, for deep research comparing PipesHub with another
open-source product, from its docs, GitHub code repository, website and community. The parent handed a `code_task` to a Codex job. The job used
only web search, `fetch_url` and a calculator and ran 14 shell commands. It never tried git, and its report never said
it had not read either code base.

`repository_fetch` (the engine's `ph git clone`) is the only way a job gets a repository: git hosts are never reachable
from a sandbox. It was off by default. An org got it only when its admin saved a `repositories` section, for example
the `github.com public repos` preset, and the test org had none. The engine brief named `ph git clone` only when a host
was allowed (WP-70), so the job was offered no way to get code and was never told so. Nobody told the person either.

There were four root causes:

| Cause | Fault |
|---|---|
| Product default | Off, while reading a public web page needs no admin step. Reading public code is the same kind of read, and it is safer: the person approves each clone, and the fetch is hardened. |
| Discoverability | The brief and `/pipeshub/AGENTS.md` said nothing about getting code when no host was allowed, and AGENTS.md said nothing at all. |
| Answer contract | Nothing structural made the job or the parent say "I could not read the repository". ADR-RUN-03 covered "not found", not "could not do". |
| Coverage | The journey registry had no repository feature. Its tool-source cohesion check read only `default_sources()`, so the gateway's own `pipeshub@git` tool was invisible to it. |

## Decision

**D1. The release's public presets are on for an org that has not set its git hosts, on a run with web access.**
`WEBSITE_RULES.repositoryPresets` marks `github-public`, `gitlab-public` and `bitbucket-public` as `default: true`.

- The fetch is host-generic: https on 443, smart HTTP, the hardened config, the size cap and the bare shallow clone
  apply to every host the same way. So GitLab and Bitbucket are added on the same terms as GitHub.
- An org whose live layer has no `org` repositories member never saved the section. It folds to those presets
  (`default_repository_policy`, `defaultRepositoryPolicy`), but only for a run with web access (the run's grant has a
  `web@…` tool). Public code rides on the same access as public pages. A run without web access gets nothing unless
  an admin saved hosts.
- A saved section, even an empty one, replaces the default. Turning the switches off on the Git hosts screen saves
  `{hosts: []}`.
- The deployment's cap (`HARNESS_ALLOWED_REPOSITORIES_CAP`) still holds the default.
- An agent's owner may only narrow it. Node's admin check reads the default as the org's list when the org saved none.

**D2. Nothing about each clone changes.** `RepositoryAsks` still asks the person every time, and "Allow for this
conversation" is still per repository. The flow rule still holds a clone after a private read, and a classification
level with `egress: deny` still refuses it. The fetch still runs in the worker, outside the sandbox, with no
credential. `x.pipeshub.repository.fetched` is still journaled.

**D3. The default lives in one place.** The release data holds the `default` flag. Python and Node derive the same
policy from it, and `test_r32repo_public_repositories.py` holds the worker to it. The Git
hosts screen reads it too: an unset section shows the default presets on, with the line "On by default".

**D4 (R41-CLONE, 2026-11-13). A repository the task names is handed over as its clone command, and one never
cloned is said so.** In that test the task named the other product's repository and
github.com/pipeshub-ai/pipeshub-ai and asked to "check … the GitHub code repo". The run allowed github.com (D1). The
only guidance was the generic line in the engine's instructions: "this task may clone public repositories from …;
run `ph git clone https://github.com/<owner>/<repo>`". The task text itself named no clone and no host. The Codex job
read 62 pages and ran 51 searches, never cloned either repository, and the answer did not say so (the report's own
caveats did, as "not a full local clone"). Neither the router nor the hand-off dropped the request: the job got the
person's words verbatim, and the chat agent's notes said "GitHub repositories/code and activity". The fault was that
the harness knew the facts and left them for the model to put together. Now:

- **What the task names.** As the engine run is assembled, `named_repositories` reads the run's task text
  (`claim.input_text`) for repositories on a known forge or an allowed host. Each must parse as
  `parse_repository_url` does, and a forge's own pages (`github.com/features/…`) are not repositories.
- **In the task, not the instructions.** `named_repositories_line` puts each clone command at the first task's tail
  (`AssembledEngineRun.task_facts`, said once, like the answer language): "Code repositories this task names: …; run
  `ph git clone https://github.com/example/repo`; …", the person is asked first, and the engine keeps working
  while it waits (ADR-RUN-06). A repository on a host the run may not clone from is named as such, with the
  instruction to say so. The instructions SR-17 froze are unchanged.
- **Never cloned, said so.** `uncloned` reads the run's journal: a repository fetched (`x.pipeshub.repository.fetched`,
  or a repository source of a result's provenance) or already declined, refused or failed is not told again. One on a
  host not allowed is `repository.not_allowed`. Any other is `repository.not_cloned`: "I couldn't read
  github.com/example/repo: it was never cloned, so its code was not checked." It travels as the other limits do:
  the job's hand-back, its step's typed items and the parent's answer, and the engine run's own answer. A Node that does
  not name `repository_not_cloned` on its claims gets the words only, never a kind its schema would refuse.

Rejected alternatives:

- **Keep it off and only tell the job.** The tester's request still would not have worked. Telling the job is D5 of
  ADR-RUN-03, and it applies when an admin turns cloning off.
- **On for every run, web access or not.** An agent built without web access would reach outside code that its owner
  never chose.

### Consequences

- That test's shape now works. The job is told it may clone github.com, gitlab.com and bitbucket.org repositories and how to
  do it. A clone shows the approval card, and on allow the job reads the files.
- An org that wants no outside code turns the presets off once. The section history keeps the change.
- The live layer's schema is unchanged. Only its description now says what an absent member means.

## As built

R32-REPO on `harness/v2-r32repo`:

- **Data:** `contracts/harness/core/policy/website-rules.json` and its schema (`default`, two new presets),
  regenerated in every target.
- **Fold:** `app/harness/repositories.py` (`default_repository_policy`, `capped_hosts`,
  `live_repository_hosts(…, web=)`) and `RunPolicy.allowed_repository_hosts(web=)` in the pinned, stored and unpinned
  policies. `app/harness/engines/durable.py` sets `run_repository_hosts` from `env.web_search_provider`.
- **Node:** `defaultRepositoryPolicy` and `effectiveRepositories(…, orgDefault)` in
  `control-plane/domain/repositories.ts`, `releaseRepositoryDefault` in `website-settings.ts`, and
  `PolicyAdminDeps.repositories.orgDefault` for an agent's narrowing.
- **UI:** `git-hosts-form.ts` (`DEFAULT_HOSTS`, `hostsOf(null)`, change kind `defaults`) and the "On by default"
  line, in all 10 locales. The chat's "Clone not available" card saves an explicit list when the section was unset.
- **Tests:**
  - `tests/unit/harness/engines/test_r32repo_public_repositories.py`: the default, the brief through the production
    assembler, and the hand-back.
  - `tests/modules/control-plane/r32repo-default-repositories.http.test.ts`.
  - The fold vectors.
  - `git-hosts-page.test.tsx` R32-REPO-UI-1..3.
  - C-38 `gatewayTools`.
  - Journey features `repository.fetch`, `repository.denied` and `repository.off`, and must-run MR-8.

- **R41-CLONE (D4):** `app/harness/repositories.py` (`named_repositories`, `named_repositories_line`),
  `app/harness/engines/capability_limits.py` (`uncloned`, `repository.not_cloned`), the engine assembly
  (`app/harness/engines/durable.py`), the child hand-back and the engine run's answer (`child_result.py`,
  `presenter.py`), the kernel's first-task tail (`task_facts`), the contract kind in every target, Node's
  `repository_not_cloned` feature, and the chat's `chat.couldNotDo.notCloned` in all 10 locales. Tests:
  `test_r41clone_named_repositories.py` (R41CLONE-1..8, the last through the production assembler),
  `test_r41clone_research_clones.py` (the research lane: two named repositories, two clone commands, two clones; none
  cloned, both told back), journey features `repository.named` and `repository.not_cloned`, and must-run MR-12.

## Deviations

None.

## Owner amendments

None yet.

## Status

**Accepted** 2026-11-12: decided from first principles, long term, best UX, answering the manual-test report.
