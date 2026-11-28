# Supply chain: locked dependencies, checked installs, pinned images

Every install of PipesHub's Python and Node dependencies is reproducible and integrity-checked: in a Dockerfile, in
CI, and in the dev setup in [CONTRIBUTING.md](../../../CONTRIBUTING.md). An install takes exactly the files a
committed lock names and checks each one against its hash. Nothing resolves a version at build time, so a release
published after the lock was written cannot get in. That includes a poisoned release like litellm 1.107.0, which
reached a dev venv on 2026-10-23.

One script holds the rules: `scripts/supply_chain.py`. The pull-request check (`main.yml`, job `supply-chain`) runs
`python3 scripts/supply_chain.py check`. The scenario IDs are SEC-SC-07 to SEC-SC-11 in
[test-scenarios.md](../test-scenarios.md). Each rule fails on a seeded bad case in `scripts/test_supply_chain.py`,
and `backend/python/tests/cohesion/test_sec_sc_supply_chain.py` runs the same cases in the pytest gate.

## What is enforced

| Rule | What fails |
|---|---|
| `PY-LOCK-HASH` | A line in a Python lock is not an exact `name==version` pin with sha256 hashes, or a registry file in `integration-tests/uv.lock` has no sha256. |
| `PY-LOCK-INDEX` | A lock carries an option: an index URL, `-e` or `-r`. `install.sh` names the indexes. |
| `PY-LOCK-VCS` | A git requirement is not `git+https` pinned to a full 40-character commit id. |
| `PY-LOCK-STALE` | `pyproject.toml` or a `requirements/*.in` file changed after the locks were written. The lock header records a fingerprint of those inputs. |
| `PY-LOCK-AGREE` | A direct requirement is missing from its lock or locked at a version its specifier refuses, or a `[tool.uv] exclude-dependencies` package is locked. |
| `PY-LOCK-SUBSET` | `runtime.lock` and `dev.lock` disagree on a package's version or files. |
| `PY-INSTALL` | A Dockerfile, workflow, shell script, Makefile, or a code block of CONTRIBUTING.md or one of its translations (`docs/i18n/*/CONTRIBUTING.md`) runs `pip install` or `uv pip install` without `--require-hashes`, runs `uv sync` without `--locked`, or resolves an environment at run time (`uv run --with`, `uvx`, `uv tool install`, `pipx install`). A shell script's quoted text is a message, not a command. |
| `DENYLIST` | A Python lock, `integration-tests/uv.lock` or a `package-lock.json` holds a release listed in `contracts/supply-chain/denylist.json`, or a direct requirement in `pyproject.toml` admits one. |
| `NODE-INTEGRITY` | A `package-lock.json` entry has no sha512 `integrity`, or resolves outside the npm registry (the SheetJS CDN is allowed for `xlsx`). A `file:` entry passes only as a vendored tarball inside the repository whose sha512, computed by the check, is the lock's one integrity value. Its path is plain relative segments (letters, digits, `._@+-`, no segment starting with a dot): npm reads the target as a URL, so `~`, `.`, `..`, `%`, `\`, `?`, `#` and drive letters would make it open a different file. Also fails on a version 1 lock, or on a `package.json` with dependencies and no lock. |
| `NODE-CI` | Any of the files `PY-INSTALL` reads runs `npm install`, `npm i`, `yarn install` or `pnpm install`. These must be `npm ci`. |
| `NODE-NPX` | Any of those files, or a `package.json` script, runs `npx` with a command no committed `package-lock.json` installs, or with a version (`npx x@1`) or a `--package` no lock holds. Such an `npx` fetches the newest release from the registry. |
| `NODE-SCRIPTS` | A Dockerfile's or a CI workflow's `npm ci` lacks `--ignore-scripts` and is not a listed `scriptedInstalls` entry of `contracts/supply-chain/install-scripts.json`; a lock package with an install script is not listed there (or is listed and has none now); an `npm rebuild NAME` names a package not marked `rebuild`. |
| `IMAGE-PIN` | A `FROM` or compose `image:` names an image by tag only and is not in `contracts/supply-chain/image-pins.json`. Also fails on an entry in that file that is now pinned or no longer used, on a listed third-party image that is not a store in the files install.sh runs, and on such a store listed for any other file. |

What may stay unlocked is listed in `contracts/supply-chain/unlocked-installs.json`, one entry per command with
its rule, the file, the text it matches and the reason: the hashed installer itself, the Python SDK under test in
the SDK job (from its own pyproject, as its users install it), and the demo connector's manual acceptance check.
An entry that no longer excuses anything fails the check, so the list only shrinks.

Two more checks need the network and run weekly in `supply-chain-weekly.yml`, not on every pull request:

- `python3 scripts/supply_chain.py lock --check` resolves again with uv against the cutoff recorded in the locks. It
  fails if that would write a different file.
- `python3 scripts/supply_chain.py verify-hashes` compares every locked hash with the digests the index publishes.
  It uses PyPI's JSON API, or the PyTorch CPU index for torch and torchvision, and does not trust the resolver's
  word. It fails on an unknown hash, a yanked release, or a release with no file for a target platform.

`python3 scripts/supply_chain.py installed <site-packages>` is offline and compares an installed environment with
`runtime.lock`'s Linux x86_64 pins (`--lock dev` for another lock). The browser lane (`deployment/e2e/run.sh`) runs it
on `E2E_SITE_PACKAGES` and refuses a venv that has drifted from the lock.

### The Python locks

All of these live in `backend/python/requirements/`:

- `runtime.lock`: the services' dependencies, from `pyproject.toml`.
- `dev.lock`: runtime plus the `dev` extra.
- `build.lock`: the build backends, from `build.in`. That is setuptools, plus maturin on Linux aarch64, where
  `html-to-markdown` builds from source.
- `tools.lock`: standalone CI and image tools, from `tools.in`: uv, ruff, PyYAML, coverage, requests, pyinstaller
  and watchdog.
- `vcs.lock`: the one git requirement, `etcd3`, pinned to a commit. pip cannot hash a checkout. Its dependencies
  are in the hashed locks.

The locks are resolved for CPython 3.12 on three platforms: Linux x86_64, Linux aarch64 and Apple-silicon macOS.
The list is `[tool.uv] environments` in `pyproject.toml`. On Linux, torch and torchvision are their CPU builds, so no
nvidia-* library reaches an image or a CI runner.

The torch pin used to be a `Dockerfile.base` build argument. It is now in `pyproject.toml` (`torch==2.12.0`,
`torchvision==0.27.0`), so CI tests the torch the images ship. Before, CI installed the CUDA build of torch 2.14.

`backend/python/requirements/install.sh build|runtime|dev|tools [--project DIR [--editable]]` is the only way the
Dockerfiles, CI and the dev setup install them. It runs in four steps:

1. It installs the build backends.
2. It installs the set's lock with `pip install --require-hashes --no-deps --no-build-isolation`. The four packages
   that ship only a source archive (talon, nats-py, uuid, and html-to-markdown on aarch64) build with the locked
   backends. No unhashed backend is fetched.
3. It installs `vcs.lock` with `--no-deps`.
4. It installs the project itself, if `--project` is given.

The script refuses any interpreter other than CPython 3.12.

The legacy code sandbox image installs its own Python packages. Its lock is
`deployment/sandbox/requirements.lock`, from `requirements.in`, for Linux only. The image installs it with
`--require-hashes --only-binary=:all: --no-deps`.

The SDK job (`sdk-tests.yml`) has two locks of its own in `integration-tests/sdk_tests/requirements/`, beside the
scripts that use them, resolved for the same three platforms (C644-FU-3):

- `helpers.lock`, from `helpers.in`: requests and pydantic, what the provisioning helpers
  (`provision_sdk_test_env.py`, `with_oauth_app.py` and the `integration-tests/helper` modules they load) import.
  The job installs it into a venv of its own and runs the helpers with that venv's Python (`SDK_HELPERS_PYTHON`).
- `tests.lock`, from `tests.in`: pytest and the plugins the Python SDK suite runs with. `run_sdk_tests.sh` installs
  it first, then the SDK under test from its own pyproject. The SDK's dependencies resolve as its users' installs do,
  which is what the suite tests; that install is the listed exception.

Both install with `pip install --require-hashes --no-deps --only-binary=:all:`. The sandbox lock and these are the
standalone locks (`STANDALONE_LOCKS` in `scripts/supply_chain.py`): each has its own input fingerprint and its own
cutoff, so `lock --set sdk-tests` regenerates one alone and `lock --check` resolves each against the cutoff it
records. `integration-tests/` keeps its uv project lock. CI installs it
with `uv sync --locked`, which checks uv.lock's hashes and fails if the lock is stale.

### Node

Every install in a Dockerfile or workflow is `npm ci` against a committed `package-lock.json`, and npm checks each
tarball against its sha512 `integrity`.

Three tools that used to be installed with `npm install -g <latest>` each have a locked package next to where they
are used:

- `deployment/sandbox/tsx/` for the sandbox image's tsx;
- `backend/nodejs/apps/docker/pm2/` for the standalone Node image's pm2;
- `.github/tools/spectral/` for CI's Spectral.

`frontend/package-lock.json` had 580 entries with neither `resolved` nor `integrity`. npm drops them, and
`main` has the same gap. Each entry was filled from the registry's metadata for that exact name and version, so no
version changed.

No image build runs a lock's install scripts on any architecture (C6.44, 2026-11-11). Every package with one is
listed in `contracts/supply-chain/install-scripts.json` with why skipping it is safe (the backend's are telemetry,
a bun binary nothing runs, a version notice, and native parts the lock already ships prebuilt), and a script that
must run is marked `rebuild` and run by name with `npm rebuild NAME` after `npm ci --ignore-scripts`. The backend
images used to run every script on x86 only, while the ARM build of the same lock already ran without them. The two
frontend installs (the app image and the hot-reload image) are the listed exceptions (C644-FU-1).

### Images

The product build bases are pinned by digest, as the engine sandbox images already were:
`python:3.12-slim@sha256:f77ac9e…` in `Dockerfile.base`, `deployment/docker-compose/hot-reload/Dockerfile.dev` and
`deployment/sandbox/Dockerfile`. Dependabot's `docker` entries move each digest and its tag together.

Every image still referenced by tag only is listed in `contracts/supply-chain/image-pins.json`, with its category
and, when the build host had it, its index digest. The check keeps it true: an image newly referenced by tag fails,
and so does an entry that has been pinned.

What stays by tag is a decision per category (recorded in the file's `$comment`):

- **Build bases and every test, CI or contributor stack**: pinned as `tag@sha256:<index digest>`, so a build or
  a test run is the same on every host. Dependabot's `docker` and `docker-compose` entries move the digests.
- **The stores in the files install.sh runs** (`userRunFiles`: `docker-compose.yml`, `docker-compose.prod-legacy.yml`):
  kept at their exact version tags. An air-gapped host loads or tags those images by name (the air-gapped guide),
  and a digest reference only matches an image pulled from a registry. The tag also takes the publisher's rebuilds
  for OS fixes.
- **Images PipesHub builds**: signed by the release workflow (keyless cosign, ADR-SEC-05) and pinned by digest
  where they run: `install.sh` checks the signature of the digest it pulled and writes it to `.env`
  (`PIPESHUB_APP_PINNED`, `PIPESHUB_SANDBOX_PINNED`); Helm takes `image.digest` and defaults its tag to the chart's
  `appVersion`, never `latest`. The release builds from bases by digest (`contracts/supply-chain/release-bases.json`).
- **Helm values**: every third-party image (the chart's and the vendored Bitnami charts') carries its digest.

### Fetched code and CI (SEC-D-11, SEC-D-12)

- `PIPE-SHELL`: no `curl … | bash` (or any shell or interpreter) in a Dockerfile, workflow, composite action or
  shell script. Node.js comes from the digest-pinned `node:22-slim` image (`COPY --from`); Rust from a pinned
  `rustup-init` checked with `sha256sum -c`.
- `DOWNLOAD-HASH`: an archive, package or binary an image build or CI job downloads is checked against a pinned
  checksum (`sha256sum -c`, `cosign verify-blob`). In a shell script an operator or contributor runs
  (`deployment/helm/aws/install-deps.sh`, the kind setup, install.sh's gVisor), a download the script then
  installs, unpacks, marks executable or runs is checked by `sha256sum -c` (or a function of the script that runs
  it) against a checksum the script carries; a sum read from the download's own server (`$(curl …sha256)`, a
  fetched `.sha256`/`.sha512`/checksums file) is refused, because it proves the transfer, not the bytes
  (R41SUPPLY-FU-10). To move a pinned tool, take the new release's sums and change the version and its lines
  together; `deployment/helm/tests/install_deps_test.sh` checks every platform has one.
- `ACTION-PIN`: every non-local action is a 40-hex commit, the tag kept as a comment; Dependabot moves both.
- `scripts/workflow_lint.py`: per-job write permissions, `persist-credentials: false`, inputs through `env:`, an LLM
  agent with file tools only, no `pull_request_target`, and CODEOWNERS on what CI and installs trust. Excused jobs
  are in `contracts/supply-chain/workflow-exceptions.json`.

## Updating a dependency safely

### Python

1. Edit `backend/python/pyproject.toml`, or `requirements/build.in` or `tools.in`. A direct dependency keeps an
   exact pin or a deliberate range.
2. Get uv from the hashed tools lock in a venv of its own:
   `python3.12 -m venv /tmp/locktools && PYTHON=/tmp/locktools/bin/python backend/python/requirements/install.sh tools`.
3. Regenerate the locks: `UV=/tmp/locktools/bin/uv python3 scripts/supply_chain.py lock`.
   - Every package you did not change keeps its locked version, because uv reuses the existing locks as
     preferences.
   - To move a transitive package, add `--upgrade-package NAME`.
   - New picks come only from releases at least 7 days old. The cutoff is recorded in each lock, so a release
     pulled within days of upload, as litellm 1.107.0 was, is never picked up.
4. Run `python3 scripts/supply_chain.py verify-hashes` and then `python3 scripts/supply_chain.py check`.
5. Review the lock diff. A new package, a new git source or a new source-only build deserves a second look.
6. Commit `pyproject.toml` together with the locks.

Dependabot's pip pull requests change `pyproject.toml` only. Their `PY-LOCK-STALE` failure is expected: check out
the branch, run step 3 and push the locks.

A release found to be malicious goes into `contracts/supply-chain/denylist.json`, with its reason, source and
`status`: `confirmed` (PipesHub saw it, or its publisher confirmed it) or `verify` (a public report nobody here has
checked yet, such as litellm 1.82.7 and 1.82.8). Both are denied the same way; the check refuses an entry without
one of the two. Turn `verify` into `confirmed` once checked, and never remove an entry. If it is a direct dependency,
bound it out in `pyproject.toml`; the check insists on this. Then regenerate the locks.

### Node

1. Change the dependency in the package's directory, for example `npm install some-package@1.2.3`. This is a
   workstation step; CI and images only run `npm ci`.
2. Commit `package.json` and `package-lock.json` together.
3. Run `python3 scripts/supply_chain.py check`, which confirms every new entry has a sha512 `integrity`.

For a locked tool package such as `deployment/sandbox/tsx/`, change its `package.json` and run
`npm install --package-lock-only --ignore-scripts --before <a date 7 days ago>`.

### Images

To pin an image:

1. Get its index digest: `docker buildx imagetools inspect <image:tag>`.
2. Write the reference as `image:tag@sha256:<digest>`.
3. Drop its entry from `contracts/supply-chain/image-pins.json`.
4. Run `python3 scripts/supply_chain.py images` to see what remains.

## Not covered yet

- **Installs at build or run time that bypass the package managers.** Examples: `crawl4ai-setup` and
  `playwright install chromium` in the app image.
- **Release signatures.** No signature is checked on Python or npm releases (PEP 740 attestations,
  `npm audit signatures`). What PipesHub runs or mounts at run time (engine and custom images, skills, MCP
  manifests, release data) follows one trust model, [ADR-SEC-01](../adr/ADR-SEC-01-supply-chain-trust.md): the
  broker verifies an engine image's signature before acquire (on with `HARNESS_IMAGE_SIGNATURES=required`, the
  default once the shipped images are signed with the new release key, OB-1), and custom images, imported skills
  and MCP tools are admitted only signed by a trusted key or approved at their exact digest.
