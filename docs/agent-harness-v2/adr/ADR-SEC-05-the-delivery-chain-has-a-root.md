# ADR-SEC-05: The delivery chain has a root: signed images, digest pins, checked fetches, least-privilege CI

- Findings: SEC-D-04 (first-party images unsigned, run by a mutable tag), SEC-D-11 (what `scripts/supply_chain.py` did not check) and SEC-D-12 (CI workflows).
- Related: ADR-SEC-01 (one trust model for the artefacts PipesHub runs; engine images are checked by the broker against the release root), ADR-OWD-08 (`RELEASE_KEYS`), [SUPPLY-CHAIN.md](../guides/SUPPLY-CHAIN.md), [RELEASE-CHECKLIST.md](../RELEASE-CHECKLIST.md).

## Context

ADR-SEC-01 signs what PipesHub's own code loads: release data, engine images, skills. All of it ships inside the product image, and nobody signed that image. Compose, `install.sh full` and Helm ran it as `:latest`, so whoever could push that tag (a stolen Docker Hub token, the hand-publishing host) replaced every install's code at its next pull, and the trust roots with it. The same gap existed one layer down: image builds ran `curl … | bash` installers, user-run stores and the chart's images floated on moving tags, 50 workflow steps ran actions by tag, and the CI jobs that held secrets were not reviewed against a compromised maintainer or a prompt-injected agent.

The fault is one: the chain of trust had no root at its outermost layer. Each layer pinned what it loaded, but the layer that delivered it to a host or a runner was taken on trust by name.

## Decision

### D1. A release image is signed by the release workflow's identity, and installs check it

- `release-publish.yml` gains a `sign` job between verification and promotion. It signs each candidate by **digest** (app and sandbox, full and slim) with keyless Sigstore cosign: the certificate names `.github/workflows/release-publish.yml@refs/tags/v…` of this repository, and the signature is in Rekor. No signing key exists to steal; the identity is the workflow at a tag, which only a tag push creates. `actions/attest-build-provenance` adds signed SLSA provenance beside the image. The SBOM BuildKit attaches is inside the image index the signature covers.
- `crane copy` promotes by digest, so the release tags carry the candidate's signatures.
- The vault-held key is the alternative (`RELEASE_SIGNING=key`, the private key in the `RELEASE_COSIGN_KEY` secret from the vault, the public key given to installs as `PIPESHUB_IMAGE_PUBKEY`). Keyless is the default recommendation: nothing to rotate, revoke or leak, and the identity check is stronger than "whoever holds the key".
- The job runs only on a tag push, only when the owner sets the repository variable `RELEASE_SIGNING`, and only after a reviewer of the `release-signing` environment approves it. That approval is the owner's go-ahead for each release. While signing is off, promotion behaves as before; once it is on, a tag push promotes only signed images (`promote` needs `sign` to succeed).
- `install.sh` checks the signature of the exact digest it pulled (`cosign verify` with the release identity and issuer; a host `cosign`, else the digest-pinned `ghcr.io/sigstore/cosign` image) and writes the digest to `.env` (`PIPESHUB_APP_PINNED`, `PIPESHUB_SANDBOX_PINNED`). Compose runs those references, so the stack runs the image that was checked, and a tag re-pushed on the registry changes nothing until the next `--upgrade` checks the new one. The pins are cleared before each pull (the pull must fetch the tag) and not written for a local build.
- A distribution that builds and signs its own images the same way names its release workflow: `PIPESHUB_SIGNER_REPOSITORY=owner/repo` and, when it is not `release-publish.yml`, `PIPESHUB_SIGNER_WORKFLOW`. They are names, checked and escaped by the installer, not a pattern: the identity stays one workflow at a `v*` tag under the GitHub Actions issuer, a malformed value fails the check, and unset means this repository's release workflow (SIGNER-FU-1, `installer_test.sh`).
- `PIPESHUB_IMAGE_VERIFY` is `require`, `warn` or `off`; an unknown value is `require`. The default is `warn` until the first signed release is published, because the releases before it have no signature to check; the release checklist flips it to `require` (one line, `IMAGE_VERIFY_DEFAULT`). A refusal says what happened, that the run started nothing and the data is untouched, what to do, and how a self-built or mirrored image is checked instead (`PIPESHUB_IMAGE_PUBKEY`) or skipped.
- A run that did not pull (`--no-pull`, air-gapped) pins what is cached and does not check signatures, because the registry that holds them is not reachable; the air-gapped bundle's own signature is that install's check (R41SUPPLY-FU).

### D2. Helm takes a digest, and never defaults to a moving tag

- `pipeshub-ai.image` builds `repository[:tag][@digest]`, refuses a digest that is not `sha256:<64 hex>` and an image with neither. `pipeshub-ai.appImage` defaults the tag to the chart's `appVersion` (now `1.0.0`), never `latest`; `pipeshub-ai.sandboxImage` does the same for the sandbox. `image.digest` is the release's digest from `image-digests.txt`, which the release attaches to the GitHub release.
- NOTES warns on every install whose application image has no digest.
- Every third-party image in the chart and its vendored Bitnami charts carries its digest (`docker:27.5.1-dind`, qdrant `v1.15.5-unprivileged`, every Bitnami image). An admission policy (Kyverno `verifyImages` or `sigstore/policy-controller`) with the same identity is the cluster-side check; it is documented, not shipped, because it needs a cluster-wide controller the chart does not own.

### D3. Code a build or a job fetches is pinned and checked, and the checker derives where code enters

`scripts/supply_chain.py check` (main.yml and the security gate's `supply-chain` stage) gains:

| Rule | What fails |
|---|---|
| `PIPE-SHELL` | `curl` or `wget` piped to a shell or interpreter in a Dockerfile, a workflow, a composite action or a shell script (`| python3 -c` reads the response as data and passes) |
| `DOWNLOAD-HASH` | an archive, package, installer or binary downloaded in an image build or CI with no `sha256sum -c` (or `cosign verify-blob`, `gpg --verify`) against a pinned value |
| `ACTION-PIN` | a non-local `uses:` that is not a 40-hex commit, in workflows and composite actions; a `docker://` action without a digest |
| `IMAGE-PIN` (extended) | a user-run store whose tag is not an exact release; an image a Dockerfile copies from (`COPY --from=`); a CI service or container image; a third-party image in any chart values file without its digest; a chart's own image on `latest` or `slim` |

Node.js in images now comes from the digest-pinned official `node:22-slim` image (`COPY --from`), not NodeSource's setup script; Rust from a pinned `rustup-init` checked against its published sha256, installing a pinned toolchain (`1.99.0`). The release builds from base images by digest (`contracts/supply-chain/release-bases.json`), and a Trivy scan (digest-pinned image, CRITICAL with a fix available) gates promotion.

### D4. A CI job holds only what it needs, and an agent that reads other people's text holds nothing

`scripts/workflow_lint.py` (main.yml, and the security gate through `test_sec_d_12_workflows.py`):

| Rule | What fails |
|---|---|
| `WF-PERMS` | a workflow without top-level `permissions:`, or one that grants a write (or `read-all`, `write-all`) at the top: writes are per job |
| `WF-PERSIST` | an `actions/checkout` without `persist-credentials: false`; a job that must push is listed with its reason in `contracts/supply-chain/workflow-exceptions.json`, and an entry that excuses nothing fails |
| `WF-EXPR` | an attacker-writable expression (dispatch or call inputs, PR, issue, comment, review or release text, commit messages, branch names) spliced into a `run:` script or a `github-script` body; numbers, SHAs and ids pass |
| `WF-AGENT` | an LLM agent step with a shell, interpreter, web, MCP or wildcard tool, with no tool list, with a token in its environment, or in a job that can write |
| `WF-TRIGGER` | `pull_request_target` or a comment trigger; a `workflow_run` that follows a workflow a pull request can start |
| `WF-OWNERS` | `.github/CODEOWNERS` missing an owner for `.github/`, the supply-chain contracts, the Python locks, `vendor_rules.py`, `trust_roots.py`, `deployment/` or the two checkers (the last matching line wins, as on GitHub) |

The release-changelog agent now reads the release and every linked pull request from files a fixed script fetched (`.github/scripts/changelog-context.py`); it holds `Read, Write, Edit, Glob, Grep, Skill` only, with reads of `/proc`, `/sys`, `/etc`, `$HOME` and the runner's temp directory denied, no token in its environment and no credentials in `.git/config`. Its output is a patch that the publish job accepts only as regular files under `CHANGELOG.md` and `changelog/`, and refuses if it carries anything shaped like a credential. The docs job runs the docs repository's own script with no token on disk or in its environment.

### Alternatives not taken

- **Default `require` today.** Every current release would be refused, and an operator would learn to set `off`. `warn` pins the digest now (the tag-swap attack needs a fresh pull after the swap, and the pin removes it) and the switch to `require` is one line once signed releases exist.
- **A long-lived cosign key as the default.** It is one more secret on the path a stolen maintainer token reaches; keyless binds the signature to the workflow at a tag.
- **actionlint or zizmor as the lint.** actionlint runs in main.yml already and checks syntax and shell; the privilege rules here are PipesHub's policy (its exceptions list, its owned paths, its agent rule) and need to run in the security gate without a download.

## As built

R41-SUPPLY on `harness/v2-r41supply` (`11d741bfea`, `a721417588`).

- **Release:** `.github/workflows/release-publish.yml` (`scan`, `sign`, `publish-digests`; bases from `contracts/supply-chain/release-bases.json`).
- **Install:** `deployment/docker-compose/install.sh` (`pin_release_images` and its helpers), `docker-compose.yml` (the `*_PINNED` references); `deployment/docker-compose/tests/image_signature_test.sh`.
- **Helm:** `templates/_helpers.tpl` (`pipeshub-ai.image`, `appImage`, `sandboxImage`), `values.yaml` and the vendored charts' digests, `NOTES.txt`.
- **Checkers:** `scripts/supply_chain.py` with `scripts/test_supply_chain.py`; `scripts/workflow_lint.py` with `scripts/test_workflow_lint.py` (in the gate as `test_sec_d_12_workflows.py`); `.github/CODEOWNERS`, `contracts/supply-chain/workflow-exceptions.json`; the SEC-D-04, -11 and -12 proofs in `test_sec_d_ops_supply_chain.py`, now plain tests.
- **Images:** `Dockerfile`, `Dockerfile.base`, `deployment/sandbox/Dockerfile`, `hot-reload/Dockerfile.dev` (Node from `node:22-slim` by digest, pinned `rustup-init`).

## Deviations

- The runner of the changelog agent still holds `ANTHROPIC_API_KEY`; an OIDC-scoped proxy or an egress-blocked runner (harden-runner in block mode, allowlist learned from an audit run) removes it. Needs a CI run.
- `RELEASE_KEYS` (release data, engine images) is still compiled in, with no revocation list or key expiry: SEC-D-04 fix 5. The image layer no longer depends on it.
- Base images are still built by hand; the release now builds from them by digest only.
- Shell scripts operators run (`deployment/helm/aws/install-deps.sh`) are held to `PIPE-SHELL` but not to `DOWNLOAD-HASH`.
- Air-gapped installs: sign the bundle and verify it offline before `docker load`.

## Owner amendments

None. The maintainers' steps (turning signing on, then `require`; the CODEOWNERS team) are in RELEASE-CHECKLIST.md.

## Status

**Accepted** 2026-11-13: decided from first principles, long term; built by R41-SUPPLY. The default stays `warn` until the first signed release.
