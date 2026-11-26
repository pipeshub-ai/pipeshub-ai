# ADR-OPS-03: Every store takes a credential, and the app port is local unless the operator opens it

- Findings: SEC-D-06 (store authentication is optional or missing) and SEC-D-08 (Compose publishes the app on every
  interface over HTTP while the installer says "local access only").
  R41-OPS, 2026-11-13.
- Builds on: [ADR-KEY-02](ADR-KEY-02-per-component-keys.md) (the sandbox plane's own Redis ACL users),
  [ADR-OPS-02](ADR-OPS-02-container-baseline.md) (the container baseline every new service keeps).

## Context

Each store's authentication was the operator's environment, not a property of the product, and nothing failed closed:

- Redis got `--requirepass` only when `REDIS_PASSWORD` was set, and `env.template` shipped it empty. Redis sits on
  `pipeshub` and `sandbox_kv`, so with an empty password the default user (which ADR-KEY-02 applies the component
  users through) was open to every container on either network.
- etcd listened on plain HTTP with no client authentication, also on `sandbox_kv`.
- Kafka's only listener was `PLAINTEXT`. `KAFKA_PASSWORD` reached the app but matched no SASL listener, and the
  Python client dropped a SASL login unless TLS was on. ZooKeeper, which holds Kafka's metadata, sat on `pipeshub`.
- Qdrant's API key and MongoDB's password defaulted to empty in `docker-compose.yml`, and to published placeholders
  (`password`, `your_qdrant_secret_api_key`) in the legacy and build files and in `env.template`.
- The Node KV config for Kafka, once stored, won over the environment, so even a correct login in `.env` could never
  reach an install whose Kafka config was stored earlier.

Separately, `docker-compose.yml` published `${APP_PORT}:3000` with no host address, which Docker binds on `0.0.0.0` and
`::`, and Docker's NAT rules bypass ufw and firewalld. The installer told an operator who gave no public URL that the
install was "local access only".

## Decision

**D1. A store starts only with its credential, and Compose refuses to render without it.** `docker-compose.yml`
requires `REDIS_PASSWORD`, `QDRANT_API_KEY`, `MONGO_PASSWORD` and `KAFKA_PASSWORD` (`${VAR:?…}` with what to set).
Compose interpolates every profile, so `KAFKA_PASSWORD` is required even with Redis Streams; `install.sh` generates all
of them on every install. The legacy and build files (`docker-compose.prod-legacy.yml`, `docker-compose.build.neo4j.yml`)
lose their published placeholders and require the same values.

- **Redis**: `--requirepass "$REDIS_PASSWORD"` always, read from the container's environment.
  `REDISCLI_AUTH` signs in the healthcheck and an operator's `compose exec redis redis-cli` without a password on any
  command line. The default user takes the password, and ADR-KEY-02's component users are applied over it as before.
- **Kafka**: one `SASL_PLAINTEXT` listener with SCRAM-SHA-512, for clients and the broker itself. A one-shot `kafka-init`
  writes the SCRAM user into ZooKeeper before the broker starts. It runs on every start, so changing `KAFKA_PASSWORD`
  and restarting rotates it. ZooKeeper moves to an internal `broker_zk` network that only Kafka and `kafka-init` join,
  so nothing else can reach it, login or not. Its own SASL digest is not needed while that holds in Compose (Helm:
  R41OPS-FU-3).
- **etcd**: TLS only, with `--client-cert-auth`. A one-shot `etcd-tls` service (the app image,
  `python -m app.utils.internal_tls`) keeps a CA in its own volume and issues etcd's server certificate plus one
  client certificate per component (`pipeshub-ai`, `egress-proxy`, `sandbox-broker`). Each certificate sits in a volume
  only that component mounts. The CN names the component: it is the user name etcd's role-based auth reads once
  R38IDENT-FU-9 enables it, so per-component roles need no new certificates. The service runs on every start, keeps
  what is current, and reissues what is within a year of expiry, issued by another CA, or for other names. It is
  root only to give each consumer its own files (`CHOWN`), and it has no network. The single member's peer port
  listens on loopback.
- **Qdrant and MongoDB**: required as above. MongoDB already ran with a keyfile, so authentication was on.

**D2. The app refuses a store that answers anonymously (defence in depth).** Before any client connects, Node
(`src/libs/config/store-auth.ts`, called first in `index.ts`):

- checks each active store has its credential configured: Qdrant's key; a MongoDB URI with a password or an
  `authMechanism`; the active graph database's password (an ArangoDB started with an empty one lets anyone in as
  root); etcd over `https://` with all three TLS files; Kafka's SASL login;
- asks Redis (`PING`) and Qdrant (`GET /collections`) without a credential.

An answer, or a missing credential, stops the start with a message that names each store and the variable to set.
An unknown answer (unreachable, or an unexpected reply) is not a refusal: the regular client reports that itself.
`STORE_AUTH_CHECK=warn` logs the same findings instead, for a store protected some other way. That is the one
relaxation, and it must be explicit: any other value enforces. This covers what Compose cannot see, such as an external
Redis, a hand-written deployment, or an old `.env`.

**D3. Clients follow the store's security, from one place per language.**

- Python's `kafka_security_kwargs` gives SASL without TLS as `SASL_PLAINTEXT`, and with TLS as `SASL_SSL`. A username
  without a password is refused either way. The consumer and the Kafka health check now use it too, instead of
  their own copies.
- etcd's client settings come from one parser per language (`app/config/providers/etcd/etcd_env.py`,
  `src/libs/keyValueStore/etcd-tls.ts`). An `https://` URL without the TLS files is refused rather than quietly
  connecting in plaintext, as the old Python parser did.
- Node's stored Kafka config takes the environment's login when it names the same brokers (`reconcileKafkaLogin`):
  an upgraded install, or a rotated password, moves on without anyone editing the store. Brokers an admin pointed
  elsewhere stay as stored.

**D4. The app port binds to loopback unless the operator opens it.** Compose publishes
`${APP_BIND_ADDRESS:-127.0.0.1}:${APP_PORT}:3000`, in every file that publishes the app. `install.sh` decides once and
writes `APP_BIND_ADDRESS` to `.env`:

- an explicit choice (`PIPESHUB_BIND_ADDRESS`, or a value already in `.env`) wins;
- otherwise a public URL whose host is not loopback means `0.0.0.0`, because an HTTPS proxy or load balancer must reach
  the port;
- anything else means `127.0.0.1`.

It then says what that means. On loopback, how to open it. On every interface, that the port speaks plain HTTP, that
ufw and firewalld do not close it, what to set when the HTTPS proxy is on the same machine, and a stronger warning
when the public URL itself is `http://`. ADVANCED_DEPLOYMENT.md "Remote access and HTTPS" gives the Caddy and
Cloudflare Tunnel paths.

**D5. Upgrades heal, they do not break.**

- `install.sh --upgrade`, or a rerun on an existing `.env`, generates any credential that a store reads fresh at
  each start and that is missing: Redis, Qdrant, Kafka, and ADR-KEY-02's component keys and legacy proxy token, which
  the upgrade path also skipped before. It says which (never the value).
- An `.env` with no bind address gets the D4 rule, written down, with a warning when that closes a port that used to
  be open.
- MongoDB's and the graph databases' passwords are never generated over existing data: those stores keep theirs on
  their volume.
- The lane's `run.sh rebuild` heals a stack's file the same way.
- etcd's data volume from the plain-HTTP etcd starts under TLS unchanged (lane-proven).
- Kafka's data and offsets stay; the broker re-registers under the SASL listener.

## As built

- Compose: `deployment/docker-compose/docker-compose.yml` (redis, qdrant, mongodb, kafka-init, kafka-1, zookeeper on
  `broker_zk`, etcd-tls, etcd, the five `etcd_tls_*` volumes, the app port), `docker-compose.prod-legacy.yml`,
  `docker-compose.build.neo4j.yml`, the integration files' app port; `env.template`; `install.sh`
  (`REGENERABLE_CREDENTIALS`, `heal_regenerable_credentials`, `app_bind_address`, `explain_bind_address`);
  `ADVANCED_DEPLOYMENT.md`.
- Helm: `templates/store-auth.yaml` generates etcd's CA and certificates (`genCA`, `genSignedCert`) and Kafka's
  login once, and keeps them across upgrades (`lookup`). The etcd certificates are all-or-none: a missing one
  reissues the set under a new CA. etcd serves TLS with `--client-cert-auth`, and its probes use its certificate.
  The app and broker pods mount their own client certificate. Kafka's brokers listen `SASL_PLAINTEXT` with SCRAM
  on both listeners, a `kafka-scram-user` init container writes the user, and the app reads the login from
  `<release>-kafka-auth`. Redis and Qdrant already required their credentials in Helm.
- Python: `app/utils/internal_tls.py`, `app/config/providers/etcd/etcd_env.py`,
  `app/services/messaging/kafka/config/kafka_config.py`, `app/health/health.py`.
- Node: `src/libs/config/store-auth.ts`, `src/libs/keyValueStore/etcd-tls.ts`, `src/modules/tokens_manager/services/cm.service.ts`.
- Lane: `deployment/e2e/run.sh` generates `REDIS_PASSWORD` and `KAFKA_PASSWORD`. A stack on its own Redis signs in and
  enforces; on the shared `pxh-redis` it signs in without a password and only warns. `docker-compose.e2e.yml` runs
  `etcd-tls` from the tree. `docker-compose.matrix.yml` runs its KRaft broker with SASL/PLAIN and its etcd under TLS.
- Tests: the proofs in `tests/unit/harness/security/test_sec_d_ops_supply_chain.py` (green);
  `tests/unit/harness/security/test_sec_d_store_auth.py` (Compose, installer and bypass shapes);
  `tests/unit/utils/test_internal_tls.py`; `tests/unit/config/providers/test_etcd_env.py`;
  `tests/unit/services/messaging/test_kafka_security_kwargs.py`; Node `tests/libs/config/store-auth.test.ts`;
  `deployment/helm/tests/test_store_auth.py`; `deployment/docker-compose/tests/installer_test.sh` (bind address and
  healing); the lane probe `deployment/docker-compose/tests/store_auth_test.sh` (anonymous Redis, etcd and Kafka
  refused, every component's login accepted, a plain-HTTP etcd volume upgraded).

## Deviations

- **Internal TLS for Redis, MongoDB, Neo4j, Qdrant and Kafka** (SEC-D-06 fix 5) is not built. On Compose every store
  is on an internal bridge, and ADR-OPS-02 drops the `NET_RAW` a sniffer needs. Helm across nodes needs
  cert-manager or the chart's own CA for each store. Follow-up R41OPS-FU-1.
- **etcd roles per component**: built by R44-IDENT ([ADR-KEY-02](ADR-KEY-02-per-component-keys.md) D9). Each
  component's certificate CN is an etcd user whose role reads only its own document; Node's own CN keeps root.
- **ZooKeeper's own SASL digest** is not built. Compose isolates it on `broker_zk`; Helm relies on the default-deny
  NetworkPolicy R41-SUPPLY owns. Follow-up R41OPS-FU-3.
- **Developer and integration Compose files** (`integration.*`, `hot-reload/*`, `beta/*`) keep their plaintext stores
  and published ports. They are test fixtures CI drives, and changing their stores means changing CI workflows,
  which is outside this package. Follow-up R41OPS-FU-2.
- **Helm etcd certificate renewal** happened only when a certificate was missing (five-year leaves). Closed by
  R43-SUPPLY (R41OPS-FU-4): Helm cannot read a certificate, so each Secret records its end date
  (`pipeshub.ai/tls-not-after`) at issue; `templates/_etcd_tls.tpl` keeps the set only when all three are there with
  more than a year left, and otherwise reissues it under a new CA, as Compose's `etcd-tls` does. A certificate with no
  readable date (every set issued before this change) counts as expiring, so the first upgrade reissues once. etcd,
  the app and the broker carry the date as a pod annotation, so a reissue restarts every holder onto the new set
  together. Tests: `deployment/helm/tests/test_etcd_tls_renewal.py`.

## Owner amendments

None.

## Status

**Accepted** 2026-11-13 under the owner's direction of 2026-11-12 (first principles, long term, best UX).
