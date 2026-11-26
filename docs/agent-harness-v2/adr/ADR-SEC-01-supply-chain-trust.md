# ADR-SEC-01: Supply-chain trust: one model for every artefact PipesHub runs or mounts

- Related: ADR-OWD-08 (signed release data, `RELEASE_KEYS`), ADR-OWD-07 (MCP manifest pinning, D-MCP-1), ADR-OWD-09 (policy and approvals), [SUPPLY-CHAIN.md](../guides/SUPPLY-CHAIN.md) (locked dependencies and pinned images at build time).
- Rows: C3.57, C6.34, C11.05 and C6.31 in the project tracker, built by **WP-85 SUPPLY-CHAIN**; D2 and D5 as extension points by WP-129.

## Context

PipesHub runs or mounts five kinds of artefact that it did not write in the same commit as the code that uses them:

| Kind | What it is | Pinned by, before this ADR | Checked at use, before this ADR |
|---|---|---|---|
| `release_data` | `vendor_rules.json`, `vendor_hosts.json`, `acp_agents.json` | exact bytes | Ed25519 signature against `RELEASE_KEYS` (OWD-8, `verify_release_document`) |
| `engine_image` | the coding engines' run images | binary sha256 inside the image; optional image digest (C2.20) | binary digest and candidate label (EH1, EH2); no signature |
| `custom_image` | an organisation's own image built on a PipesHub base | nothing | nothing (no such catalog) |
| `skill` | a skill bundle mounted into a sandbox or loaded by the loop | bundle digest (SKL-G6) | digest only: an imported package was active and mountable at once |
| `mcp_server` | a server's tool manifest | per-tool descriptor digest | admin manifest approval, drift guard, description scanner (MX-03..05) |

Each kind grew its own partial answer. Release data had signatures but nothing else did; MCP had admin approval but no signature path; skills had neither; images had a digest but no signer. A supply-chain attack needs only the weakest kind: a poisoned skill import ("prerequisite: `curl … | sh`", SK-06) was mountable on the next run.

## Decision

One trust model, applied per kind. Code: `backend/python/harness_core/supply_chain/`.

### D1. An artefact is trusted by exactly one of three routes, decided at use

`decide_trust` (`harness_core/supply_chain/trust.py`) is the one decision:

1. **First party.** Content PipesHub's own release ships (built-in skills, the release's MCP templates) or that a signed-in member wrote inside PipesHub (a skill typed in the editor, a learned candidate an admin promoted). Its provenance is recorded by PipesHub, never read from the artefact.
2. **Signature.** An Ed25519 attestation over the canonical statement `{schema: "pipeshub.attestation/1", kind, subject, digest, claims}` by a key the kind's trust roots list. Binding the kind into the signed bytes stops a key trusted for skills from vouching for an image.
3. **Admin approval** of that exact digest, the alternative to a signature for content PipesHub stores and hashes itself (skills, MCP manifests). An approval names one digest; any change is a new digest and needs a new decision.

Everything else is refused with a reason code (`artefact_pending_review`, `artefact_denied`, `artefact_unapproved`, `artefact_signature_invalid`, `artefact_unknown_key`), and the person reads a plain, translated reason.

### D2. Trust roots are per kind, and the release root cannot be widened

`TrustRoots` maps each kind to `keyId -> Ed25519 public key`:

- `release_data` and `engine_image` trust only the release root, `RELEASE_KEYS` in `harness_engines/vendor_rules.py`, the same key and ceremony OWD-8 uses (`release_trust_roots`, `harness_engines/trust_roots.py`). Roots any other source adds for either kind must be refused: a deployment cannot add a key that vouches for release artefacts.
- The roots in force come from one extension point, `TrustRootsProvider` (`harness_core/supply_chain/ports.py`), read through `harness_edition()` by the query service and the sandbox broker alike (`trust_roots`, `app/harness/supply_chain/roots.py`). This build's default, `ReleaseTrustRoots`, holds the release's keys alone, so `skill` has no signature root: a skill is trusted first party or by an admin's approval of its digest. An edition's provider may add roots for `skill` and `custom_image` (the `catalog.trustRoots` dimension of the control plane).
- `mcp_server` has no signature route yet. Its entries come from the release's templates (first party) or an admin's instance, and its tools by admin manifest approval. A signed registry is C3.51.

The signature format and check are OWD-8's: `{alg: "Ed25519", keyId, sig}` over exact bytes. `verify_release_document` now delegates to `harness_core/supply_chain/signatures.py`, so there is one verifier.

### D3. Verification happens where the artefact is used, not only where it enters

| Kind | Enters | Verified at use |
|---|---|---|
| `skill` | import goes to review with a content scan (`scan_skill`); a signed bundle is trusted at import | admission leaves an untrusted skill out of the run (`LegacyCapabilityReader._skills`), and `PinnedSkills.files` (the one reader for the loop and every engine) refuses it with `SkillUntrusted`, so an approval revoked after admission still stops the mount |
| `mcp_server` | admin manifest approval (unchanged, MX-03) | every listing and call (`admit_tools`, unchanged) |
| `engine_image` | release allowlist | the sandbox broker before acquire (`_admit_image` → `ImageTrust.verify`), on the image's own digests |
| `custom_image` | an edition's image check (D5) | the broker before acquire, through the same `ImageTrust.verify` |
| `release_data` | the release | at load (unchanged) |

Images are verified by signature only. The broker is the only component that sees an image's bytes, so it trusts no other process's claim that an image was approved.

### D4. A pinned artefact that changes is reviewed as a diff

When an artefact's digest moves, the reviewer sees what changed against the last trusted digest, in plain labels, and approves or denies that exact digest:

- skills: a per-file line diff (`files_diff`) plus the scanner's findings, in the skills page's review panel;
- MCP: a field diff per changed tool (`manifest_changes`: description, each input added, removed or changed, behaviour hints) in the manifest the approval request returns, beside the existing side-by-side view;
- images: the attestation's field diff (`registration_diff` over `fields_diff`: the digest and every claim) logged by the broker when a checked image's digest no longer matches.

### D5. Images beyond the release's are an extension point

The broker's check comes from `ImageTrustProvider` (`harness_core/supply_chain/ports.py`), read through `harness_edition()` by `image_trust_from_env` (`app/harness/supply_chain/images.py`). This build's default, `ReleaseImageTrust`, checks the release's engine images once engine signatures are required (D6) and nothing else.

`ImageTrust` (`harness_core/supply_chain/images.py`) is open to a subclass an edition's provider returns: `kind_of` names the further images it checks, `added_images` admits them past the release allow list, `needs_bases` has the broker read the PipesHub base images' layers (so a check can tell FROM from `RootFS.Layers`, never a label), and `admit` checks a verified image further, refusing with `ArtefactUntrusted`. Such an image still needs an attestation over one of its own digests by a root of its kind (D1, D2); there is no approval route (D3).

### D6. Release signing is the owner's step

Verification is built and tested against generated keys. Signing the shipped images and re-signing the release data with the new release key is OB-1 (credential rotation). Until then engine image signatures are checked only when `HARNESS_IMAGE_SIGNATURES=required`; the default flips with the first signed release.

### D7. The ACP profiles are signed at release build, verified on every stack, and their trust is its own decision (R32-TRUST, 2026-11-12)

Context: at the production floor (`E2E_SANDBOX_RUNTIME=runsc`) every ACP engine read "This coding engine is not installed on this deployment" though its image was there: `acp_profiles` read the unsigned `acp_agents.json` only under `PIPESHUB_ENGINES_WITHOUT_GVISOR`, so one switch decided two unrelated trust questions (may engines run below gVisor; may profiles nobody signed run), and a runsc stack could only lose five engines or bypass verification. 55 journey lines were red on the R31-SECKEY runsc stack.

1. **Never signed in the tree.** Profiles change often, so a signature in the tree would go stale, and no build host keeps the release key's private half. `sign_vendor_rules.py --key … --key-id … --acp-out` is the release-build step: it takes the release key from the secret store and writes `acp_agents.json.sig` next to the document in the build checkout before the image build; git ignores that path (`.gitignore`), and the ceremony that signs the vendor rules and hosts no longer touches the profiles. RELEASE-CHECKLIST.md carries the step; it is tested with a throwaway key.
2. **Test stacks take the real verification path.** `sign_vendor_rules.py --test-root DIR` makes an Ed25519 key in memory, writes its public half (`trust-root.json`, schema `pipeshub.test-trust-root/1`, key id `test-…`) and its signature of the tree's profiles to `DIR`, and never writes the private half. `run.sh` does it per stack at build (and again at `rebuild`) into the run's state folder, never the repo or an image, for `E2E_SANDBOX_RUNTIME=runsc` always and for any stack with `E2E_ACP_PROFILES=signed`; Compose mounts the folder read-only into the worker and the broker and names it in `PIPESHUB_TEST_TRUST_ROOT`. The verifier (`harness_engines.acp.registry.packaged_acp_agents`) adds that key to the release keys for the ACP profiles only.
3. **Where the extra root may come from.** Only a service's process environment and a read-only mount name it: no org setting, API, ConfigurationService key or sandbox reaches either (operator config, like `SANDBOX_WITHHOLD_RUNTIMES`). The key vouches for the ACP profiles only, never the vendor rules, their hosts or an image, so D2 holds for everything that decides model access or isolation: `release_trust_roots` is unchanged and `RELEASE_KEYS` ships as it was. A test key's id must start with `test-` and can never be a release key's id. **The guard:** a release build is one whose packaged profiles carry their signature (only the release-build step writes it). Such a build refuses to read its profiles with `PIPESHUB_TEST_TRUST_ROOT` set unless `PIPESHUB_ALLOW_TEST_TRUST_ROOT=true`, and every harness process (broker, worker, model gateway) reads them at start, so it does not start; a test root that is set but unreadable stops it the same way (`ProfileTestRootRefused`, `harness_engines.profile_trust`).
4. **Two switches.** `PIPESHUB_ACP_PROFILES_UNSIGNED` (dev only, exactly `true`) reads unsigned profiles; `PIPESHUB_ENGINES_WITHOUT_GVISOR` only lowers the isolation floor. The e2e lane sets the first wherever it set the second before (auto and runc, `E2E_ACP_PROFILES_UNSIGNED`), so those stacks behave as they did; production Compose ships it `false`.
5. **Plain words.** The broker reports the allow-listed ACP engines whose packaged profile is there but untrusted (`enginesUntrusted`, also in `enginesUnavailable`). Node lists them greyed, not absent, with `ENGINE_UNAVAILABLE` and the finer reason `engine_profile_untrusted` (`CodingEngineView.reason.reasonCode`), and refuses a save with "This engine's profile isn't signed for this deployment"; the admin page and the chat's refusal say the same in all 10 locales. The operator's fix is in GVISOR.md and FLAGS.md: a release image is signed; a source checkout sets `PIPESHUB_ACP_PROFILES_UNSIGNED=true` on a dev install, or signs with a test root.
6. **The human-test stack** (`scripts/human-test-stack.sh`) runs at the production floor by default where the daemon has runsc (`--runtime runsc|auto|runc|default`), so testers see what production installs run, all eight engines included.

## As built

Built by WP-85 on `harness/v2-wp85` (from round 19 `8f12fddca4`).

- Model: `backend/python/harness_core/supply_chain/` (`signatures.py`, `trust.py`, `reviews.py`, `diff.py`, `skill_scan.py`, `images.py`, `ports.py`). Release roots: `backend/python/harness_engines/trust_roots.py`. The roots and image trust defaults and the review store: `backend/python/app/harness/supply_chain/`.
- Skills: `app/api/routes/skills.py` (`finalize_import` records `source: imported` and returns the skill's standing), `app/harness/supply_chain/skills.py` (`SkillTrust` over the skill admission policy, `edition_config.skill_admission`; this build's `ScannedSkillAdmission` uses an import at once unless the content scanner flags it), `app/agents/agent_loop/skills/bundle_source.py` (`PinnedSkills`), `app/agents/legacy_capabilities.py`; Node proxy `backend/nodejs/apps/src/modules/skills/`; per-skill state and the scanner's findings in `frontend/app/(main)/workspace/skills/personal/`.
- MCP: `manifest_changes` in `app/api/routes/mcp_hub.py`'s manifest view, rendered by the hub tab.
- Images: `ImageTrust` in `app/harness/sandbox/docker_broker.py` (`_admit_image`), wired from env in `app/sandbox_broker_main.py`.
- Tests: `backend/python/tests/unit/harness_core/supply_chain/` (model), `backend/python/tests/unit/harness/supply_chain_trust/` (skills, MCP, broker, the two extension points), with scenario IDs SK-06, AD-04, AD-05, SEC-SC-01, SEC-SC-04.

## Deviations

1. **Images have no approval route** (D3). An unsigned image cannot be admitted by an admin click; its builder signs it.
2. **MCP entries have no signature route** (D2). A signed private registry is C3.51 (deferred, P2).
3. **No image scan in this build** (D5). A check that wants a scan reads it as a signed claim on the attestation; PipesHub does not ship a vulnerability scanner.
4. **This build has no admin review of imported skills.** An imported skill is used at once unless the content scanner flags it; flagged content is held back, shown on the skills page with its findings, until its owner changes it. A policy that keeps reviews plugs in as `edition_config.skill_admission` and may add its own Inbox source (`AGENT_HARNESS_INBOX_EXTRA_SOURCES`, kind `artefact_review`). Images have no admin review: the broker verifies them by signature (D3) and logs a moved digest (D4).

- R32-TRUST (D7): `harness_engines/profile_trust.py`, `harness_engines/acp/registry.py`, `harness_engines/release.py` (`acp_profiles`, `untrusted_engines`), the broker's `enginesUntrusted` (`harness_core/sandbox/types.py`, `app/harness/sandbox/engine_images.py`, `docker_broker.py`), Node's `coding-engine-service.ts` and `http-engine-availability.ts`, `scripts/engines/sign_vendor_rules.py`, `deployment/e2e/run.sh` (`acp_profiles_env`, `stack_trust_root`, `restack_runtime`), `docker-compose.e2e.yml`, `scripts/human-test-stack.sh`. Tests: `tests/unit/harness_engines/test_r32_profile_trust.py`, `tests/cohesion/test_r32_stack_trust.py`, `test_engine_image_availability.py::test_r32_*` (docker), Node `r32-engine-profile-untrusted.test.ts`, frontend `r32-engine-profile-untrusted.test.ts`.

## Owner amendments

None yet. D7 was decided by R32-TRUST from first principles, long term, best UX.

## Status

**Accepted** (2026-11-05), decided by WP-85 from first principles, long term, UX-first. Built and tested with generated keys. Signing the shipped images and re-signing the release data waits on OB-1, and so does turning engine image signatures on by default. The SK1 third-party pack lane (`SK1_SKILLS_DIR`) needs a person to install the packs and stays open.
