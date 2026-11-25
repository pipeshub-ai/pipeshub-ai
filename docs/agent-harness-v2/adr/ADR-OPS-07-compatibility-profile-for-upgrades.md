# ADR-OPS-07: Upgrades keep upstream's behaviour; one switch adopts the hardened profile

- Finding: R51-COMPAT. The owner's PR-blocking requirement (2026-11-14): "Everything should work as before until the
  new agent harness environment and feature flags are enabled. The existing platform should be fully backward
  compatible and shouldn't break anything."
- Amends: [ADR-OPS-06](ADR-OPS-06-one-harness-switch-off-by-default.md) decision 4 ("platform changes stay on when they
  are security fixes or additive"). Related: SEC-D-05/06/08/09 (ADR-KEY-02, ADR-OPS-03, ADR-OPS-04), SEC-A-04,
  ADR-WEB-01/02, ORGDEL, E7, F3.

## Context

R50-OFF made the harness itself off by default, and listed what the security programme changed for every install
whatever the switch says (R50-OFF inventory, now [HARNESS-OFF.md](../guides/HARNESS-OFF.md) "Security profile"). Several of those changes stop an
existing install or client on upgrade: a plain `docker compose up` with an older `.env` failed at render on the store
credentials; a template `SECRET_KEY` stopped every service; the app port moved to 127.0.0.1; an install whose MongoDB
had no root password was locked out once the keyfile turned authentication on; code execution needed containers and
keys only the installer makes; a scripted client that refreshed with the same body token was signed out; an install
that framed PipesHub got a blank frame; a monitor that parsed `/api/v1/health` lost the service list; an API call that
deleted an org without `confirmName` got a 400; a flag value such as `enabled` stopped a service; Helm's namespace
default-deny cut traffic in a shared namespace. A rollback to the previous release could not read configuration
written after the upgrade.

## Decision

1. **One switch, `PIPESHUB_HARDENED`, decides the security profile.** Unset or `false` is the **compatibility
   profile**: the behaviour of a release without the security programme, with a warning in the log wherever the
   hardened default would have differed. `true` is the **hardened profile**: today's defaults. A value that is not a
   boolean reads compatibility with a warning; the switch never stops a process. Node reads it through
   `hardenedProfile` (`backend/nodejs/apps/src/libs/utils/security-profile.ts`), Python through `hardened_profile`
   (`backend/python/app/utils/security_profile.py`), Compose passes it, Helm renders it from `hardened`.
2. **Fresh installs get the hardened profile; existing installs keep theirs.** `install.sh` writes
   `PIPESHUB_HARDENED=true` on a fresh install and keeps a value an `.env` names. An `.env` this branch's earlier
   installer wrote ran the hardened defaults, so it gets `true`. An `.env` from a release without the switch gets
   `false`, written explicitly so the choice is visible, with the one line that opts in
   (`PIPESHUB_HARDENED=true ./install.sh --upgrade`). The copied `deployment/docker-compose/env.template` is a fresh
   install and says `true`. Helm cannot tell an install from an upgrade under GitOps (`helm template`), so `hardened`
   defaults to `false` and the chart's NOTES and README say to set it on a new release.
3. **The switch flips exactly the profile-driven set** (table in [HARNESS-OFF.md](../guides/HARNESS-OFF.md),
   "Security profile"). An explicit per-item variable (`STORE_AUTH_CHECK`, `SECRET_KEY_KDF`, `APP_BIND_ADDRESS`,
   `CSP_FRAME_ANCESTORS`, `CSP_REPORT_ONLY`, `networkPolicy.*`, …) still wins over the profile.
4. **What the installer does invisibly stays on for everyone**: generating missing store credentials and component
   keys, creating MongoDB's root user when the password was empty (R50OFF-FU-2), the replica set. A plain
   `docker compose up` with an older `.env` never fails at render: each store runs as upstream did when its credential
   is missing, and says so in its log.
5. **Rollback stays possible in compatibility**: new configuration is written under the SHA-256 key the previous
   release reads, a weak `SECRET_KEY` is not rotated, code execution stays on the Docker API proxy path, and MongoDB
   data stays readable by a standalone `mongod`. The hardened profile writes under the HKDF key; rolling back after
   opting in needs the steps in the upgrade notes.
6. **Security fixes for flaws upstream also has stay on when they break nothing** (SEC-A-04's floor on every sandbox
   the proxy creates, refresh-token reuse detection for families that rotated, the strength warning). Where one would
   break an existing install it follows the profile, and the risk is written in the upgrade notes.
7. **The harness switch is independent.** `AGENT_HARNESS_WORKER_ENABLED=true` keeps the behaviours the harness needs
   (the internal listener, session erasure), whatever the profile. The broker follows code execution: an upgrade
   under compatibility keeps `SANDBOX_MODE=docker` and starts no broker even with the switch on by default (the value
   the installer writes on upgrade is not a request), and moves to it when the operator runs
   `PIPESHUB_AGENT_HARNESS=on ./install.sh --upgrade` or opts in to the hardened profile (2026-11-21: since the switch
   defaults on, reading it as a request moved every upgraded install to the broker).

## As built

- The switch: `backend/nodejs/apps/src/libs/utils/security-profile.ts`, `backend/python/app/utils/security_profile.py`;
  the suites run as a hardened install (`tests/helpers/harness-switch-env-global.ts`, `backend/python/tests/conftest.py`)
  and each compatibility behaviour has its own test.
- Node: the key ring (`libs/encryptor/secret-key-ring.ts`, `writerKdf`), store auth (`libs/config/store-auth.ts`), env
  flags (`libs/utils/env-flags.ts`), headers (`libs/middlewares/security-headers.ts`), health
  (`tokens_manager/routes/health.routes.ts`), refresh (`auth/session-rotation.ts` `keep`, `auth/session-cookie.ts`
  `refreshRotationWanted`, header `X-PipesHub-Refresh-Rotation`), org deletion (`user_management/controller/org.controller.ts`),
  conversation, user and service-account erasure (`agent-harness/config.ts` `erasureOnDelete`), the internal listener
  (`transport/internal-listener.ts` `internalListenerWanted`), the conversation body limit.
- Python: the key ring (`utils/encryption/secret_key_ring.py` `writes_sha256`), env flags (`utils/env_flags.py`
  `LEGACY_UNRECOGNISED`), the Docker API proxy (`sandbox/docker_proxy.py`: `anonymous_caller`, `drop_root`), the
  legacy executor (`harness/sandbox/docker_client.py`), Kafka (`services/messaging/kafka/config/kafka_config.py`).
  Shared cases: `contracts/secret-key/profile-cases.json`.
- Compose and installer: `deployment/docker-compose/docker-compose.yml` (no required store credential, Redis/Kafka/Mongo
  fallbacks, bind address, `SANDBOX_MODE` unset reads `docker`, the `sandbox-broker` profile, the proxy's root start
  without `DOCKER_GID`), `install.sh` (`security_profile_choice`, `secure_secret_key`, `ensure_mongo_root_user`,
  `sync_docker_proxy_app_token`, `sandbox_plan`), `env.template`. Tests: `installer_test.sh`, `sandbox_plane_test.sh`,
  `test_c12_sandbox_plane.py`, `test_sec_d_store_auth.py`.
- Helm: `hardened` in `values.yaml` and `_helpers.tpl`; `tests/test_security_profile.py`, `check_chart.sh` variants,
  `aws/install.sh` `release_hardened`.
- Lane: `deployment/e2e/compat_upgrade_check.py` (seed on the previous release, check after the upgrade and the
  rollback).

## Deviations

- The app container is on `sandbox-control` in every install, so that compatibility keeps the previous release's code
  execution. In broker mode the app's way to Docker is closed by the proxy's caller credential (the installer writes
  the app none), no longer by the network.
- The Docker API proxy starts as root when `DOCKER_GID` is unset (an older `.env`) and drops to uid 1000 with the
  socket's group before serving; it holds `SETUID`/`SETGID` for that.
- A fresh Helm release is compatibility unless it sets `hardened=true` (decision 2); the README quick starts,
  `aws/install.sh` and the kind lanes set it.
- The image entrypoint's `env_flag` (`deployment/process_monitor.sh`) still stops the container on a malformed
  `AGENT_HARNESS_WORKER_ENABLED` or `MODEL_GATEWAY_ENABLED`; neither existed in the previous release.

## Owner amendments

None yet. Owner questions: R51COMPAT-FU-1 (fresh Helm releases default to compatibility).

## Status

**Accepted** (2026-11-14, R51-COMPAT; the owner's requirement of the same day).
