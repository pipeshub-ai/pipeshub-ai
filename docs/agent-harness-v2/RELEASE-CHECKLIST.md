# Agent harness: before a release

Work through this list before the harness ships to production. Each item says how to check it. Tick an item only once
its check passes on the release commit.

## Release keys and signed data

- [ ] **The release signing key lives in the release secret store, and nowhere else.**
  - Make the release key inside the secret store, or on an offline machine, and import it.
  - Its public half is in `RELEASE_KEYS` in `backend/python/harness_engines/vendor_rules.py`; any key it replaces is
    removed from there.
  - Re-sign the vendor rules and hosts with `scripts/engines/sign_vendor_rules.py --key … --key-id …`. The script
    also rewrites the Node mirror. The ACP profiles are signed by the release build instead (below).
  - No build or test host keeps a copy of the private half.
  - Check: `git grep pipeshub-release-` lists only the current key id.
- [ ] **Every signed file verifies against the shipped key.**
  - Check: the vendor-rules tests pass (`pytest tests -k "vendor_rules or vendor_hosts"` in `backend/python`).
  - Check: `python -c "from harness_engines.vendor_rules import release_vendor_rules; release_vendor_rules()"` raises
    nothing.
- [ ] **The release build signs `acp_agents.json`; the tree never holds its signature** (ADR-SEC-01 D7).
  - Signing it switches on the long-tail ACP engines (ACP-driver.md, "Signing"). Leaving it unsigned keeps them on
    dev installs that set `PIPESHUB_ACP_PROFILES_UNSIGNED=true`; admins elsewhere read "This engine's profile isn't
    signed for this deployment". Record the decision in the release notes.
  - In the release build, before the image build, with the key taken from the release secret store:
    `scripts/engines/sign_vendor_rules.py --key … --key-id … --acp-out`. It writes
    `backend/python/harness_engines/acp_agents.json.sig` in the build checkout, which git ignores; never commit it.
  - Check: `python -c "from harness_engines.acp.registry import packaged_acp_agents; print(len(packaged_acp_agents(unsigned_ok=False)))"`
    in the built image prints the number of ACP agents (5 today), and `git status` in the build checkout is clean.
  - Check: no shipped install sets `PIPESHUB_TEST_TRUST_ROOT` or `PIPESHUB_ALLOW_TEST_TRUST_ROOT` (a release build
    with the first and without the second refuses to start).
- [ ] **Review the vendor rules.**
  - Every `pending_vendor` row in `vendor_rules.json` still matches the vendor's current written position.
  - Bump `version` if any row changed, and re-sign.

## Credentials and supply chain

- [ ] **No credential is in the tree, or in any image layer.**
  - Check: the secret scan in CI passes on the release commit.
  - Check: `scripts/edition_guard.py --base origin/main` is ok.
- [ ] **Every credential a build or test host held is rotated and revoked.** This covers model provider keys, cloud
  keys, GitHub tokens, and engine sign-ins. Check: the old values fail against their services.
- [ ] **Every `SECRET_KEY` a build or test host knew is rotated on the installs that keep running** (any shared
  staging stack). Follow guides/runbooks.md "Rotating `SECRET_KEY`".
  - Check: `rotate-secret-key-cli.js retire` exits 0 on each install, `SECRET_KEY_PREVIOUS` is then removed, and
    `verify` exits 0 with the old key gone.
- [ ] **Dependencies come from the hashed locks.** The locks are current, and pinned packages such as LiteLLM are at
  the reviewed version (guides/SUPPLY-CHAIN.md).

- [ ] **Release images are signed, and installs refuse unsigned ones** (ADR-SEC-05).
  1. Create the `release-signing` environment with a maintainer as required reviewer, then set the repository variable
     `RELEASE_SIGNING=keyless` (or `key`, with `RELEASE_COSIGN_KEY`/`RELEASE_COSIGN_PASSWORD` from the vault).
  2. Push the release tag and approve the `sign` jobs. Check: `cosign verify pipeshubai/pipeshub-ai:<version>
     --certificate-identity-regexp '^https://github\.com/pipeshub-ai/pipeshub-ai/\.github/workflows/release-publish\.yml@refs/tags/v[0-9]'
     --certificate-oidc-issuer https://token.actions.githubusercontent.com` succeeds, and `image-digests.txt` is on
     the GitHub release.
  3. After the first signed release: set `IMAGE_VERIFY_DEFAULT="require"` in `deployment/docker-compose/install.sh`
     and bump the chart's `appVersion`.
  4. Approve the `offline-bundle` jobs too. Check: each architecture's `pipeshub-offline-<version>-<arch>.manifest`,
     its `.sigstore.json`, its parts and `sigstore-trusted-root.json` are on the GitHub release, and the job's "Check
     the bundle as install.sh will" step passed (ADR-SEC-06 D2).
  5. When Sigstore rotates its trusted root (a `cosign verify-blob` of a fresh signature fails offline): replace
     `deployment/docker-compose/sigstore-trusted-root.json` with the current one (`cosign initialize` on a connected
     host writes it under `~/.sigstore/root/`) and its SHA-256 in install.sh's `PIPESHUB_SIGSTORE_ROOT_SHA256`.
- [ ] **CODEOWNERS takes effect.** The `@pipeshub-ai/maintainers` team exists with the owners in it, and branch
  protection on main requires review from code owners.

## Isolation

- [ ] **Coding engines run under gVisor in production.**
  - `PIPESHUB_ENGINES_WITHOUT_GVISOR` is unset in every production config.
  - Check: admins see no "Coding engines run without gVisor" warning.
- [ ] **Kata and the Helm agent sandbox are either shipped or listed in the release notes as not offered.**

## Quality

- [ ] **The full gate is green on the release commit.** That is `scripts/verify.sh` plus the Docker, frontend and e2e
  lanes, with the coverage floors unchanged.
- [ ] **The security gate passes on the release commit** (C6.42). The release workflow's `Security` job runs it and
  promotes nothing without it. Check: `python3 scripts/security_gate.py run all` exits 0 with every stage passed
  (deployment/docker-compose/RELEASE.md, "The security gate").
- [ ] **The final QA pass is complete, and every gap it found is an automated test.** It covers function, performance,
  UX, latency and accuracy.
- [ ] **No release-blocking issue is open for this release.**
