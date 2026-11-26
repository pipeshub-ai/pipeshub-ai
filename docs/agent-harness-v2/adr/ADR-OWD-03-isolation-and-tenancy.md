# ADR-OWD-03 — Isolation boundary and sandbox tenancy

## Context

[OWD-3](../one-way-doors.md) fixes the security boundary, what may share a sandbox, and whether sandboxes are reused. Customers will be told that each session runs in its own isolated sandbox. Weakening that later is a security regression.

## Decision

Adopt OWD-3. A sandbox belongs to one session and one principal, and a pooled sandbox is claimed once and never handed out again. The outer sandbox is the boundary, and the floor for coding engines in production is gVisor; a microVM (Kata) may be required per org. Plain runc runs engines only behind an explicit dev/test setting that shows a warning. The engine's own sandbox is defence in depth.

## As built

Audited at `959c3bda72`. Decision rows: the v2 architecture report D9; the implementation plan D31; the release decision on the gVisor floor.

- The floor is signed release data (`contracts/harness/core/policy/release.json`, `isolationMin: gvisor`), enforced in `backend/python/harness_core/engines/isolation.py`. The controller refuses rather than downgrades: `backend/python/harness_core/engines/controller.py`.
- Helm refuses engines with `ENGINE_SANDBOX_MISSING` until the agent-sandbox chart exists.
- Passing in the gate:
  - `backend/python/tests/unit/harness_core/engines/test_gvisor_floor.py` (12), including `test_gv1_production_without_runsc_refuses_the_engine_with_the_install_guide`
  - `backend/python/tests/unit/harness_core/engines/test_controller.py::test_ct12_isolation_the_broker_lacks_is_refused_never_downgraded`
  - `backend/python/tests/unit/harness/sandbox_broker/test_docker_broker.py::test_gvisor_uses_the_runsc_runtime`
  - `backend/python/tests/unit/harness_core/sandbox/test_fake_warm_pool.py::test_wp3_wp9_a_released_sandbox_is_never_handed_out_again`
  - `backend/python/tests/unit/harness/sandbox_broker/test_docker_snapshots.py::test_dk3_another_session_never_reads_the_snapshot` (docker.xml)

### On gVisor (R31-GVISO, 2026-11-12)

The host has `runsc` (release-20261109.0, systrap, cgroup v2). The escape, exhaustion and internal-reach probes run on
both runtimes (`test_sec_iso_escape_docker`, `test_sec_iso_exhaustion_docker`, `test_egress_security_docker`) and
assert the security property, not one runtime's errno. What the gVisor run changed in the Docker adapter:

- **Process limit.** The host's pids cgroup counts the Sentry's threads and a stub process per guest process (about
  30 at rest, about 2 per guest process), not the guest's tasks: at the spec's 64 a sandbox could start 16 processes
  and then got `ENOMEM`. Under gVisor the spec's `pids` is now `RLIMIT_NPROC`, which the Sentry counts in its own
  kernel, one per sandbox, and the host cgroup is `128 + 3 × pids` (`host_pids_limit`). runc keeps the cgroup: there
  `RLIMIT_NPROC` counts the uid host-wide, which every sandbox shares.
- **Runtime configuration is not a caller's.** A spec label under `dev.gvisor.` or `io.katacontainers.` is refused
  (`RUNTIME_RESERVED_LABEL_PREFIXES`), the hardening policy refuses any OCI annotation in a create request, and a
  daemon whose `runsc` entry carries an argument that weakens it (`--allow-flag-override`, `--network=host`,
  `--net-raw`, `--host-uds`, `--host-fifo`, `--TESTONLY-*`) is not offered as gVisor (`unsafe_runsc_args`).
- **An operator may withhold runsc** (`SANDBOX_WITHHOLD_RUNTIMES=runsc`): the floor then applies as without it.
- What gVisor does differently and the probes accept: no Docker seccomp profile on the guest (`Seccomp: 0`; the
  Sentry answers every syscall and is filtered on the host, unless runsc runs with `--oci-seccomp`); a process may
  make user and network namespaces inside the Sentry, which reach nothing outside them; its own cgroup hierarchy named
  by the container id; no tmpfs inode count (inodes are Sentry memory inside the sandbox's limit); and an OOM ends the
  whole sandbox, since the host's OOM killer sees one process.

### PF-09 runtime benchmark: method (pre-registered 2026-11-12, before any sample)

C1.04 / DP7-P0-2. Written and committed before measuring, so the result cannot shape the rule.

- **Runtimes.** `runc` (the broker's hardened runc) and `runsc` (gVisor release-20261109.0, systrap, the daemon's default flags: `directfs=true`, `overlay2=root:self`). Kata waits on the owner installing `containerd-shim-kata-v2`.
- **Sandbox.** The broker's own host config (`DockerSandboxBroker._host_config`): read-only root, `cap_drop ALL`, `no-new-privileges`, `init`, 2 CPUs (the opencode sizing), 4 GiB, pids 1024, `/tmp` tmpfs, `/workspace` a fresh named volume per sample, network `none`. Image `pxh-opencode-tools:1.18.34-ph6` (git, node, python 3.12 as an engine has them).
- **Workloads**, each timed inside the container with a monotonic clock, so start-up is not in them:
  1. `git clone` of a local mirror (read-only bind) of this repository at `274cbef85e`, depth 1: 19 269 files, 304 MB.
  2. `npm ci --offline --ignore-scripts` of `backend/nodejs/apps/package-lock.json` (800 packages) from a read-only npm cache filled once on the host.
  3. `pytest` of `tests/unit/harness_core/{policy,budget,access,journal}` (2 026 tests) from the clone in `/workspace`, with the lane's site-packages read-only; bytecode and the run's files go to the volume, as an engine's would.
     *Amended before the first sample (2026-11-12):* the engine image's Python is musl and the suite's compiled dependencies (pydantic-core) are the lane's glibc wheels, so this workload runs in `pxh-sandbox:pr1` (glibc Python 3.12, the `run_code` image) with the same host config, on the same volume, right after 1 and 2. The lane's site-packages are mounted read-only as that image's own site-packages, so the suite's child Pythons find them too.
  4. **Cold start**: `docker run` to the first command's exit (`true`), the container created and removed each sample.
- **Samples.** One discarded warm-up per runtime, then 7 per workload and runtime, alternating runc and runsc so host drift falls on both. A sample starts only while `host-room` reads CPU < 40 % and RAM < 80 %; the CPU %, RAM % and 1-minute load at each sample's start are recorded with it. Reported: p50, p95 (nearest rank over 7, so the slowest sample) and the runsc / runc ratio of the p50s.
- **What would change the default.** The release floor (gVisor for engines in production) is a security decision; the benchmark decides only whether it stays the default as built or goes back for a new decision:
  - **Keep gVisor as the engine default** if every engine's conformance and controller suite passes on runsc (C1.03), the sum of the three workloads' p50 on runsc is at most **2.0×** runc's, and the cold-start p50 on runsc is at most runc's **+ 1.0 s**.
  - **Keep it, with a follow-up** (the warm pool, operator flags) if the sum is over 2.0× and at most 3.0×, or cold start is over + 1.0 s and at most + 3.0 s.
  - **Back to the owner** with a proposal per deployment target (Compose, Helm) if the sum is over 3.0×, cold start is over + 3.0 s, or an engine fails only on runsc with no root-cause fix.
- **Operator flags.** gVisor's `directfs` and `overlay2` are measured as variants only if an operator can set them; a sandbox spec must not be able to. `allow-flag-override=false` on this daemon means no container annotation can change them, so they are operator-only (`daemon.json` `runtimeArgs`).

### PF-09 result (2026-11-12, measured after the method above was committed)

Samples: [`backend/python/tests/fixtures/pf09/samples-2026-11-12.jsonl`](../../../backend/python/tests/fixtures/pf09/samples-2026-11-12.jsonl) (7 per workload and runtime after one warm-up each; `python3 -I scripts/pf09_runtime_bench.py report <file>` reproduces the table). Host while sampling: CPU p50 18 % (max 28 %), RAM 37 %, 1-minute load p50 7.1 (max 9.6) on 32 cores.

| Workload | runc p50 / p95 (s) | runsc p50 / p95 (s) | runsc / runc (p50) |
| --- | --- | --- | --- |
| `git clone` (19 269 files, 304 MB) | 9.69 / 9.89 | 15.00 / 15.22 | 1.55× |
| `npm ci --offline` (800 packages) | 9.66 / 10.90 | 17.06 / 18.32 | 1.77× |
| `pytest` (2 026 tests) | 46.12 / 48.67 | 56.41 / 61.14 | 1.22× |
| Sum of the three | 65.48 | 88.47 | **1.35×** |
| Cold start (`docker run … true`) | 0.437 / 0.501 | 0.442 / 0.537 | +0.004 s |

Through the product path, the broker's `sandbox.acquired` time and QA-LAT-3's job start (Enter to the job card's Working, three samples per stack) on `run.sh durable`: Codex at a quiet moment, runsc acquire p50 0.79 s against runc 0.73 s, job start p50 1.01 s against 1.07 s. opencode, measured while the host was busy, runsc acquire p50 1.63 s against 0.84 s, job start p50 1.61 s against 0.96 s. Job start stays within its 2 s p50 budget on both runtimes.

**Decision, by the pre-registered rule: keep gVisor as the engine default** (1.35× ≤ 2.0×, cold start + 0.004 s ≤ + 1.0 s, and every engine passes on runsc: C1.03). Nothing changes for Compose. Helm keeps refusing engines until P5.3, and its default is decided there with the same rule. The I/O cost falls on file-heavy steps (clone, install), not on start-up or on CPU-bound test runs.

**Operator flags.** This daemon runs gVisor's defaults (`directfs=true`, `overlay2=root:self`) with `allow-flag-override=false`, so a sandbox spec or annotation cannot change them. Only an operator can, through `daemon.json` `runtimeArgs`, and that needs root on the host, which this lane does not have. The variants are not measured: R31GVENG-FU-5.

**Kata** is registered but cannot start (`containerd-shim-kata-v2` missing): R31GVENG-FU-3.

### gVisor's own syscall filter, out-of-memory ends and the engines' own sandboxes (R32-SECCOMP, 2026-11-12)

gVisor with its own syscall filter is the production default (2026-11-12). The choices below favour the long-term
solution and the best experience for admins.

- **D-SC1. The installer registers `runsc` with `--oci-seccomp`.** Docker's default seccomp profile then filters each
  sandbox's syscalls inside the Sentry as well as on the host, as runc does: defence in depth behind the boundary. An
  installed `runsc` without it is re-registered only on an explicit yes (`--install-gvisor`, or the prompt), keeping
  its other arguments, and Docker is reloaded, never restarted (a reload re-reads `runtimes`; verified on a Docker
  29.9 daemon: the new arguments apply to new containers, running ones keep running). The other runsc flags stay at
  gVisor's defaults, which are already the strict values (`--network=sandbox`, `--host-uds=none`, `--host-fifo=none`,
  `--net-raw=false`, `--allow-flag-override=false`); `--directfs` is a measured trade-off, not a hardening switch.
- **D-SC2. Warn, do not refuse.** The broker reads the flag from the daemon's `runtimeArgs` as Go parses it
  (`runsc_oci_seccomp`: a bare flag or `=true|1|t` is on, the last occurrence wins) and reports
  `gvisorSyscallFilter=on|off` while gVisor is offered, with a `broker.gvisor_syscall_filter_off` warning; Node
  carries it as the coding-engine view's `gvisorSyscallFilter` and admins see an amber notice linking the guide's
  "Syscall filter" section. Refusing would take every engine away from each install registered by `runsc install`
  on upgrade, while the boundary itself, gVisor, is intact: the filter narrows the Sentry's surface,
  it does not create the isolation. The flag is read from `runtimeArgs` only; one set some other way is not seen
  and reads as off, which errs towards the warning.
- **D-SC3. The probes assert the state they find.** SEC-ISO-01 reads the broker's report: with the filter (and on
  runc) `Seccomp: 2`, `keyctl` gets the profile's `EPERM` and every namespace probe is refused with `EPERM`; without
  it `Seccomp: 0` and a namespace may be made inside the Sentry and must reach nothing. The test daemon had no
  filter and root is needed to add it, so the "on" half ran against a second daemon: a privileged `pxh-r32sc-dind`
  container running Docker 29.9 with the same runsc (release-20261109.0) registered twice, `runsc` with
  `--oci-seccomp` and `runsc-plain` without. There: `Seccomp: 2`, `unshare` `EPERM`, `keyctl` `EPERM` (plain:
  `Seccomp: 0`, `unshare` allowed, `keyctl` `ENOSYS`); SEC-ISO-01 [runc]+[gvisor] pass on both daemons; the exhaustion
  suite's gVisor cases pass there except the four that read the host's cgroup files, which a nested daemon does not
  have. The operator's runbook is GVISOR.md "Turning it on for an existing install".
- **D-SC4. An out-of-memory end is told apart and not retried (R31GVISO-FU-3).** Docker sets the container's
  `State.OOMKilled` on both runtimes (probed: runc, the command killed and the container still running; runsc, the
  container exited 137). When an engine's turn is lost (the engine gone, the sandbox gone, or a turn the engine never
  ended), the controller asks the broker before it releases the sandbox (`memory_exhausted`, the broker's
  `GET /v1/sandboxes/{id}/memory`; a broker without it, or one that cannot tell, reads as no), and an OOM fails the
  run once as `out_of_memory` with `run.failed.memoryLimitMb`. A lost turn is otherwise released and driven again; an
  OOM would only meet the same limit in a new sandbox of the same size. Node and the web app word it with the limit
  ("ran out of memory (limit 4 GB)"), next action `change_request`. `OOMKilled` stays set for the container's life,
  so on runc a later engine death in a sandbox that had an earlier OOM reads as one too: rare, and memory-related
  more often than not. On runc an OOM usually kills the command, not the engine, which then reads the command's
  exit 137 and goes on: that is a tool result, not a run end.
  *Amended by R33-WORDS (2026-11-13), the same words wherever it shows:* a job's end carries the limit on
  `child.completed.memoryLimitMb`, its failure result and its `sub_agent` part, so the job card and the job's step
  read "ran out of memory (limit 4 GB)" (R32SECCOMP-FU-4). A loop run's own `run_code` asks the same question when its
  program exits 137 (runc) or its sandbox goes (gVisor): an OOM is then a failed run, never a tool failure or a run
  end, whose stderr, error analysis (`memory`) and step summary name the limit (`CodeOutOfMemory`,
  `CodeResult.out_of_memory_mb`; R32SECCOMP-FU-1). The model reads it as any failed run and can change its code; a
  kill the limit did not cause stays exit 137 as before. The legacy Docker-socket backend is not changed (it has no
  `memory_exhausted`).
- **D-SC5. Engines run without their own sandbox on gVisor too (R31GVISO-FU-6).** Measured on a runsc host and the
  nested daemon with a throwaway Codex image that has bubblewrap: Codex's `workspaceWrite` sandbox starts under gVisor
  without the filter and refuses a write outside the workspace; on runc and under gVisor with `--oci-seccomp`
  bubblewrap gets no namespace. The release images have no bubblewrap, so today nothing starts on either runtime. The
  two layers exclude each other: an engine's own sandbox needs user namespaces, which the profile refuses. The filter
  is kept, because it narrows the kernel surface for every process in the sandbox (the engine itself and every
  command), while an inner sandbox confines only the commands the engine routes through it, and would require
  shipping bubblewrap (and socat for Claude Code) in signed images and a looser profile. The outer sandbox stays the
  boundary; its in-guest filter is the defence in depth.
- **PF-09 variant.** `scripts/pf09_runtime_bench.py run --runtime runc --runtime runsc --runtime-label
  runsc=runsc-oci-seccomp` samples the flag's cost on a daemon where it is set, and `report` compares it with runc by
  the pre-registered rule and with plain gVisor on its own (`runsc-oci-seccomp/runsc`).

## Deviations

- The gVisor lanes of the Docker tests run wherever the daemon offers `runsc`. The engines' real lanes run on the strongest runtime the daemon offers (`HARNESS_TEST_SANDBOX_RUNTIME` pins one), and every engine passes on runsc and runc (R31-GVENG). CI's runners still have no runsc. One switch (R31GVENG-FU-4, 2026-11-12): the pytest lanes' `HARNESS_TEST_SANDBOX_RUNTIME` takes run.sh's `E2E_SANDBOX_RUNTIME` words (`auto`, `runsc`, `runc`; `gvisor` stays an alias of `runsc`), and a stack's runc side is `E2E_SANDBOX_RUNTIME=runc` (the broker withholds runsc), so R31-GVENG's lane-local override (a `docker` shim adding a broker that dropped runsc) is deleted; `test_r31gveng_lane_runtime.py` holds the two vocabularies equal.
- Kata and the Helm agent-sandbox chart (P5.3) are not built; Kata's shim is not installed on the test hosts.

These are deferred, not divergences from the decision.

## Owner amendments

- **2026-10-20** (owner): gVisor is required for engines in production; dev/test may opt out with a visible warning; native `run_code` is unaffected.
- **2026-10-26** (owner): gVisor installation, the gVisor lanes and the PF-09 benchmark are deferred to the end, together with Kata, P5.3 and S17.

## Status

**Accepted** (2026-10-26). The floor and the no-reuse rule are built and tested, on runc and, since R31-GVISO (2026-11-12), on gVisor. PF-09 keeps gVisor as the engine default. Since R32-SECCOMP gVisor's own syscall filter is the installer's default and reported to admins when off. ⏸ Kata is waiting on its shim (R31GVENG-FU-3).
