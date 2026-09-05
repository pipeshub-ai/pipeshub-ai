# Releasing PipesHub

Cutting a release should be pushing a tag and reading a status check. This
describes how that works, what each gate proves, and what to do when one fails.

## Before this existed

Images were built and pushed by hand. Nothing ran against the published
artifact, so whether a release worked was established by someone installing it
and looking. That is why releases were monthly while merges were daily: the
bottleneck was never the code, it was the verification.

## Cutting a release

```bash
git tag v0.7.1
git push origin v0.7.1
```

That is the whole procedure. The `Release — build, publish, verify` workflow
then does four things in order, and stops at the first failure.

| Stage | What happens | What it proves |
|-------|--------------|----------------|
| Resolve | The tag is parsed as semver | A typo cannot publish `pipeshubai/pipeshub-ai:oops` |
| Build | Both variants built for amd64 and arm64, pushed under a **candidate** tag | The image builds reproducibly from the tag |
| Verify | First-run install, then upgrade from the previous release — both variants, on amd64 and arm64 | A user can install it, and an existing user can move to it, on either architecture |
| Promote | Candidate copied by digest onto the release tags | `:latest` only ever points at an image that passed |

Because promotion copies by digest, the image a user pulls is byte-identical to
the one the tests passed against — not a rebuild that happens to come from the
same commit.

A prerelease tag (`v0.8.0-beta.1`) publishes `0.8.0-beta.1` and
`0.8.0-beta.1-slim` but deliberately leaves `:latest` and `:slim` alone.

### Rehearsing without publishing

Run the workflow manually with **promote** unchecked. It builds and verifies,
and moves no published tag. Useful before a risky release, and for checking the
pipeline itself after changing it.

## What the gates actually check

Verification runs on both architectures the build publishes. Docker resolves a
multi-arch tag to whatever the runner is, so testing only on x64 would promote an
arm64 image that had never been started; each job asserts its own architecture
first, so a runner change cannot silently turn this back into four x64 runs.

**First-run smoke** (`tests/published_hub_smoke.sh`) installs from the published
image with `install.sh --yes`, waits for the app to report healthy, confirms the
running container is the expected image and has not crash-looped, and checks the
host can reach the health endpoint.

**Upgrade smoke** (`tests/upgrade_smoke.sh`) installs the previous release,
creates an organisation, an admin and two knowledge bases through the API,
upgrades in place, then checks the stack returns healthy, the admin can still
sign in, and the knowledge bases are still there. It also asserts that
`SECRET_KEY`, `MONGO_PASSWORD`, `REDIS_PASSWORD` and `QDRANT_API_KEY` are
unchanged — an upgrade that regenerates them leaves every encrypted value in the
database unreadable, which looks like data loss and passes a health check.

Seeding goes through the API rather than writing to the databases, so the test
exercises the same path a user does and does not break every time a migration
renames a collection.

## When a gate fails

The release is not published. `:latest` still points at the previous image, and
users are unaffected. Diagnostics — container logs and the health payload — are
attached to the run as artifacts.

Fix forward and tag again. There is nothing to clean up: candidate tags are
removed on success and are harmless if left behind, since nothing references
them.

## Rolling back

Daily releases are safe because a bad one is cheap to undo, not because every
one is perfect.

```bash
cd ~/pipeshub
./rollback.sh --list      # what this deployment has run
./rollback.sh             # back to the previous tag
./rollback.sh 0.6.0       # back to a specific version
./rollback.sh --dry-run   # show what would happen
```

Rollback verifies the target image exists before touching anything, keeps a copy
of `.env`, and restores it if the downgrade fails.

**A rollback undoes code, not data.** If the release you are undoing migrated
data, the older image may not understand it and the correct recovery is
restoring a backup. The script says so before proceeding.

## Watching a deployment

`Post-release probe` runs after each release and every thirty minutes against
the instance in `vars.PIPESHUB_PROBE_URL`, opening a single issue when the
deployment degrades and closing it when the probe recovers. It is deliberately
shallow — is it up, are its services healthy — because its job is detection
time, not depth.

Probe by hand with:

```bash
PROBE_URL=https://pipeshub.example.com bash deployment/docker-compose/tests/health_probe.sh
```

## One-time setup

| Setting | Where | Why |
|---------|-------|-----|
| `DOCKERHUB_USERNAME` | Secrets | Pushing images |
| `DOCKERHUB_TOKEN` | Secrets | Pushing images |
| `PIPESHUB_PROBE_URL` | Variables | The deployment to probe; the probe skips if unset |
| `PIPESHUB_PROBE_TOKEN` | Secrets | Only if health needs auth |

The base images (`pipeshubai/pipeshub-ai-base:python-deps`, `:python-deps-slim`,
`:runtime`) are still built deliberately rather than on every release — see the
header of `Dockerfile.base`. The release workflow pulls them and fails clearly if
they are missing, rather than silently rebuilding a forty-minute layer.

## Testing the tooling itself

These scripts take destructive actions against real deployments, so the logic
that decides *whether* to act is tested on its own — no Docker, no network:

```bash
bash deployment/docker-compose/tests/release_tooling_test.sh
bash deployment/docker-compose/tests/installer_test.sh
```

Both run on any pull request touching `deployment/docker-compose/`.
