# ADR-CAP-02: One sandbox ceiling, sized from the daemon that runs the sandboxes

- Amends: [ADR-OWD-10](ADR-OWD-10-runtime-placement-and-stores.md) C2.19 (the broker's install ceiling, `SANDBOX_MAX_TOTAL`, "default 50, 0 is none").
- Related: main #5959 (the code sandbox ceiling sized from the machine, `backend/python/app/agent_loop_lib/sandbox/capacity.py`).

## Context

Two caps read the same variable, `SANDBOX_MAX_TOTAL`, with different meanings:

| | App: code sandbox governor (`capacity.py`) | Sandbox broker (`sandbox_broker_main.py`, before) |
|---|---|---|
| Counts | `run_code` sandboxes of this app container (every query and harness worker shares its slot files) | every live sandbox of its namespace on its daemon: engine sandboxes, claimed warm ones and, with `SANDBOX_MODE=broker`, the native `run_code` ones |
| Empty | sized from the machine: min(25 % of memory / 512 MB, 75 % of CPUs / 0.5, 25 % of the process limit / 256), at least `SANDBOX_MIN_TOTAL` | 50, whatever the host |
| 0 or less | 1024 (the most slot files) | no ceiling |
| Not a number | ignored with a warning (sized) | refuses to start |
| Machine it measures | docker mode: the daemon's `/info` through the proxy | none |

In broker mode the app could not measure the daemon that runs its sandboxes. On Compose the proxy refuses the app `/info` (it holds no proxy credential there), so it sized from its own `/proc` and logged an "older proxy image?" warning; that is the same host only by luck of the topology. On Helm the app pod has no `DOCKER_HOST` at all, so sizing never succeeded and every pod stayed at the minimum of 2. The chart passed `sandbox.capacity.maxTotal` to the app pods only, per pod, and passed the broker neither it nor the sidecar's limits nor the pod split, so the broker ran at 50 whatever its `dind` sidecar held.

What an operator got from `SANDBOX_MAX_TOTAL=10`: on Compose in broker mode, at most 10 `run_code` sandboxes per app container and at most 10 sandboxes of every kind on the daemon (the two happen to agree because there is one app container). On Compose in docker mode with the broker profile also running, 10 `run_code` sandboxes through the proxy plus 10 broker sandboxes, up to 20 on one host, since neither side sees the other's. On Helm in broker mode (`sandbox.capacity.maxTotal=10`), 10 per app pod, so N pods could ask for 10 × N while the broker allowed 50. Left empty, the broker allowed 50 on a 4 GB host and 50 on a 256 GB one.

The fault is one variable with two parsers, two defaults and two machines. Nothing about the two budgets needs them to differ: in broker mode every `run_code` sandbox is one of the broker's sandboxes, so the broker's ceiling is the host's bound and the app's is its share of it.

## Decision

**D1. One meaning.** `SANDBOX_MAX_TOTAL` is the most sandboxes the component that enforces it runs at once on the daemon it starts them on. Empty is sized from that daemon; 0 or less is 1024, with a warning; anything else that is not a whole number is ignored with a warning, so a typo is sized, never unlimited. There is no "no ceiling": a fixed set of slot files cannot offer one, and an unbounded broker is how a host runs out of memory.

**D2. One parser, one sizing.** The broker builds its ceiling with the app's code (`build_daemon_capacity` in `capacity.py`): `CapacitySettings.from_env`, `read_machine_size` on its own daemon's `/info` (or `SANDBOX_HOST_*` when a deployment passes the daemon's limits in), `compute_plan` with the code sandbox's run size, and `slot_file_count`. `SANDBOX_MIN_TOTAL`, `SANDBOX_MEMORY_SHARE`, `SANDBOX_CPU_FACTOR` and `SANDBOX_HOST_*` mean the same on both sides. Until the daemon answers the ceiling is the minimum, and it rises once it does (`SandboxCap` reads it at each admission). Engine sandboxes are larger than a code sandbox; the count does not model that, and an operator who runs many engine sessions sets `SANDBOX_MAX_TOTAL` lower (sizing guide).

**D3. Measured where the daemon is.** The broker reports its ceiling and the machine it sized from on its health answer (`broker_detail`). In broker mode the app sizes from that machine with its own settings, and the broker's ceiling divided by `SANDBOX_CAPACITY_DIVISOR` is one more term (`machine_from_broker`, `read_broker_machine_size`), so an app copy never sizes past its share of what the broker allows. A broker that does not report it (a release before this) leaves the app sizing as before; one not yet sized is waited for, at the minimum.

**D4. Pods sharing a daemon split it.** On the Helm broker paths the broker gets `sandbox.capacity.maxTotal`, the sizing knobs and, with its own `dind` sidecar, that sidecar's limits and memory share; the app pods get the divisor (the most pods that can run) and the same memory share, and no ceiling of their own. Docker-mode paths keep their behaviour: per pod, and split only for an external daemon with `maxTotal` empty.

Two names were the alternative (one for the app's per-container cap, one for the broker's host cap). It was rejected: in broker mode the app's number is only its share of the broker's, so a second name would be a second place to get the same fact wrong, and every existing `.env` that set `SANDBOX_MAX_TOTAL` already meant "this many on this host".

**Migration.** Compose: an operator who set `SANDBOX_MAX_TOTAL` gets the same value on both sides, as before. One who left it empty gets a broker sized from the host instead of 50 (lower on a small host, higher on a large one); one who set 0 gets 1024 instead of no ceiling; a value that is not a number now starts the broker, sized, with a warning. Helm on the broker paths: `sandbox.capacity.maxTotal` now bounds the broker, and app pods take their share of it instead of each getting the whole value. With the harness off and `SANDBOX_MODE=docker` (the Compose default), nothing changes: the app reads the variable as main does.

**Consequences.**

- One setting, one number on the host: in broker mode `SANDBOX_MAX_TOTAL=10` is 10 sandboxes on the daemon, shared by every app copy.
- Helm broker installs size their app pods from the broker's daemon instead of sitting at 2.
- Known limit: docker mode with the broker profile also running (engines on the broker, `run_code` through the proxy) still has two counts on one host, each up to the ceiling. `install.sh` writes broker mode whenever the harness is on, so this is a hand-made mix.

## As built

- D1, D2: `build_daemon_capacity`, `broker_detail`, `machine_from_broker`, `read_broker_machine_size`, `build_sandbox_capacity`, `MachineSize.ceiling` and the `broker` term in `compute_plan` (`backend/python/app/agent_loop_lib/sandbox/capacity.py`); `sandbox_capacity` in `backend/python/app/sandbox_broker_main.py` replaces `max_sandboxes` and its default of 50; `DockerSandboxBroker(capacity=...)` reports it on health (`backend/python/app/harness/sandbox/docker_broker.py`); `SandboxCap` takes a ceiling read at each admission (`backend/python/harness_core/sandbox/capacity.py`).
- D3: broker mode reads the broker (`build_sandbox_capacity`, used by `get_sandbox_capacity`).
- D4: `pipeshub-ai.brokerCapacityEnv` and the broker paths of `pipeshub-ai.sandboxCapacityDivisor` (`deployment/helm/pipeshub-ai/templates/_helpers.tpl`), `deployment/helm/pipeshub-ai/templates/sandbox-broker.yaml`, `deployment/helm/pipeshub-ai/templates/deployment.yaml`; Compose passes the broker the app's variables and defaults (`deployment/docker-compose/docker-compose.yml`).
- Tests: `backend/python/tests/unit/agent_loop_lib/sandbox/test_capacity_broker.py` (app and broker parity for every value, broker mode, a broker of an older release), `backend/python/tests/unit/harness/sandbox_broker/test_broker_process.py::test_c2_19_cap02_the_install_sandbox_ceiling_is_sized_from_the_brokers_daemon`, `backend/python/tests/unit/harness_core/sandbox/test_c2_19_sandbox_cap.py::test_cap02_a_ceiling_sized_later_is_read_at_each_admission`, `deployment/docker-compose/tests/compose_sandbox_env_test.sh` (the broker role), `deployment/helm/tests/check_chart.sh` (`broker-dind-3-pods`, `broker-external-2-pods`, `broker-dind-1-pod`, the broker notes).

## Deviations

None.

## Owner amendments

None.

## Status

**Proposed** (2026-11-25).
