# Deployment, Operations and Developer Experience: Onyx vs PipesHub (as of 1 Nov 2026)

Method and evidence base:
- **PipesHub:** the local checkout at `/home/user/pipeshub-ai` (HEAD `a3f7d0b`, 2026-11-01), plus `gh api` against `pipeshub-ai/pipeshub-ai`.
- **Onyx:** a shallow clone of `onyx-dot-app/onyx` (HEAD `3eb6420`, 2026-11-01) at `/tmp/onyx-src-clone/onyx`. I read files only and ran none of its scripts.
- **Onyx GitHub data:** `gh api` was refused for this repository (403, not enabled for the session). Onyx release counts therefore come from git tag dates, and Onyx issue numbers come from WebFetch of GitHub HTML pages.
- **PipesHub docs site:** docs.pipeshub.com did not resolve from this session's network allowlist. PipesHub documentation was judged from in-repo docs only, and the docs site needs a separate check.
- **Onyx docs:** the Mintlify sources live in-repo under `documentation/`, which is what docs.onyx.app publishes.
- **Link convention:** Onyx links point to `github.com/onyx-dot-app/onyx/blob/main/...`. PipesHub links point to `github.com/pipeshub-ai/pipeshub-ai/blob/main/...`, and each matches the local path of the same name.

## 1. Install paths and time-to-first-answer

### Takeaway
Both products have a curl-pipe one-line installer, Docker Compose, Helm and a managed cloud. Onyx offers far more paths:
- a Go CLI (`onyx-cli deploy …`) that installs, upgrades and checks status;
- a PowerShell installer for Windows;
- Terraform modules for AWS, GCP and Azure;
- an AWS ECS Fargate CloudFormation template;
- a Terraform *provider*;
- per-cloud guides for EC2, EKS, RDS, GCP, Azure and DigitalOcean.

PipesHub has a single deep path, a 2,229-line Bash wizard. Its only cloud-specific path is AWS EKS (scripts plus a guide).

### Cited Findings
- **Onyx installer:** `install.sh` is now only a bootstrap. It installs the Go `onyx-cli` and calls `onyx-cli deploy install`, which supports `--lite`, `--include-craft`, `--tag`, `--no-prompt`, `--dry-run`, `--dir`, `--offline` and `--no-wait`. — [onyx deployment/docker_compose/install.sh](https://github.com/onyx-dot-app/onyx/blob/main/deployment/docker_compose/install.sh)
- **Onyx one-liner:** `curl -fsSL https://onyx.app/install_onyx.sh | bash`. — [onyx README.md](https://github.com/onyx-dot-app/onyx/blob/main/README.md)
- **Onyx on Windows:** a native 1,121-line PowerShell installer ships next to the 393-line bash bootstrap. — [onyx deployment/docker_compose/install.ps1](https://github.com/onyx-dot-app/onyx/blob/main/deployment/docker_compose/install.ps1)
- **Onyx day-2 commands:** the CLI also provides `deploy status`, `deploy logs`, `deploy stop`, `deploy upgrade` and uninstall, plus migration from older install.sh or manual compose setups. — [onyx documentation/deployment/local/onyx_cli.mdx](https://github.com/onyx-dot-app/onyx/blob/main/documentation/deployment/local/onyx_cli.mdx)
- **Onyx Terraform modules:** under `deployment/terraform/modules/{aws,azure,gcp}`. The AWS set has `eks`, `onyx`, `opensearch`, `postgres` (RDS), `redis`, `s3`, `vpc` and `waf` submodules, and a `size` input (small/medium/large). — [onyx deployment/terraform/modules](https://github.com/onyx-dot-app/onyx/tree/main/deployment/terraform/modules); [onyx SIZING.md](https://github.com/onyx-dot-app/onyx/blob/main/deployment/helm/charts/onyx/SIZING.md)
- **Onyx CloudFormation:** `deployment/aws_ecs_fargate/cloudformation` has cluster, EFS and ACM templates plus `deploy.sh` and `uninstall.sh`. — [onyx aws_ecs_fargate/cloudformation](https://github.com/onyx-dot-app/onyx/tree/main/deployment/aws_ecs_fargate/cloudformation)
- **Onyx Terraform provider:** `terraform-provider-onyx/`, released by its own `release-terraform-provider.yml` workflow. — [onyx terraform-provider-onyx](https://github.com/onyx-dot-app/onyx/tree/main/terraform-provider-onyx)
- **Onyx deployment doc pages:**
  - `cloud/aws/ec2`, `cloud/aws/eks`, `cloud/aws/rds`, `cloud/gcp`, `cloud/azure`, `cloud/digital_ocean`;
  - `local/docker`, `local/kubernetes`, `local/terraform`, `local/onyx_cli`, `local/desktop_app`;
  - `getting_started/quickstart`, described as "Recommended way to deploy Onyx in minutes".
  - Source: [onyx documentation/deployment/](https://github.com/onyx-dot-app/onyx/tree/main/documentation/deployment)
- **Onyx Cloud:** a managed SaaS at cloud.onyx.app, with free sign-up linked from the README. — [onyx README.md](https://github.com/onyx-dot-app/onyx/blob/main/README.md). Third-party sources put the price at about $20/user/month billed annually; Onyx's own pricing page was not checked. — [gosearch.ai](https://www.gosearch.ai/faqs/onyx-ai-pricing-the-real-open-source-tco-in-2026/); [aitoolsatlas.ai](https://aitoolsatlas.ai/tools/onyx/pricing)
- **PipesHub one-liner:** `curl -fsSL https://get.pipeshub.com/install | bash`. The root `install.sh` (115 lines) downloads `docker-compose.yml` and the real installer for the latest release tag, then runs it. — [pipeshub install.sh](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/install.sh)
- **PipesHub installer flags:** the 2,229-line wizard supports `--yes`, `--version`, `--build`, `--no-pull` (air-gapped), `--print-env-only`, `--reconfigure`, `--upgrade`, `--rotate-signing-secrets`, `--stop` and `--uninstall`. It checks Docker, RAM and disk prerequisites. — [pipeshub deployment/docker-compose/install.sh](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/docker-compose/install.sh); [pipeshub README.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/README.md)
- **PipesHub has no Windows-native installer and no CLI binary.** Open issue #3074 asks for "pipeshub-cli for install, upgrade, and uninstall" (opened 2026-08-26). — [issue #3074](https://github.com/pipeshub-ai/pipeshub-ai/issues/3074)
- **PipesHub on Kubernetes:**
  - Helm chart `deployment/helm/pipeshub-ai` (chart 0.2.1, appVersion 0.10.0, which lags the v1.0.0 app release);
  - values files `values-eks.yaml`, `values-eks-single-az.yaml`, `values-cloud.yaml` and `values-local.yaml`;
  - AWS helper scripts `deployment/helm/aws/{install.sh,deploy.sh,cluster.yaml,...}`;
  - a guide, `docs/deployment/aws-eks.md`, that includes a one-command install, backups/restore, upgrades and troubleshooting.
  - Sources: [pipeshub Chart.yaml](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/helm/pipeshub-ai/Chart.yaml); [pipeshub docs/deployment/aws-eks.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/docs/deployment/aws-eks.md)
- **PipesHub has no Terraform, CloudFormation, GCP or Azure guides** in the repo. The only cloud guide is `docs/deployment/aws-eks.md`, and `deployment/` holds only `docker-compose/`, `helm/` and `sandbox/`. — [pipeshub deployment/](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/deployment)
- **PipesHub Cloud:** a managed offering at app.pipeshub.com, "the same connectors, permission-aware search, and APIs, with nothing to install". — [pipeshub README.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/README.md)
- **PipesHub HTTPS caveat:** the README warns that an HTTP-only cloud deployment shows a "white screen" because browsers block requests, so users must put their own TLS terminator (Cloudflare, Nginx or Traefik) in front. — [pipeshub README.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/README.md)
- **Onyx bundles TLS:** nginx plus Let's Encrypt (`docker-compose.prod.yml`, `init-letsencrypt.sh`). — [onyx deployment/docker_compose](https://github.com/onyx-dot-app/onyx/tree/main/deployment/docker_compose)

### Inferences
- **Onyx leads on breadth of IaC and cloud coverage:** Terraform for 3 clouds, CloudFormation, a Terraform provider, and per-cloud docs.
- **Onyx leads on day-2 tooling:** the CLI has status, logs and upgrade with manifest-based drift detection.
- **Onyx leads on TLS:** it bundles TLS in compose, while PipesHub tells users to bring their own TLS terminator.
- **PipesHub's wizard is richer in ops flags than a typical install.sh:** signing-secret rotation, RAM-aware memory sizing, and a paired `rollback.sh`. It is still Bash-only and Unix-only.
- **Neither side publishes a time-to-first-answer figure** beyond Onyx's "in minutes".

### Gaps
- No measured time-to-first-answer for either product.
- AWS, GCP or Azure Marketplace listings and Railway or Render templates were not found in either repo. Marketplace presence was not verified on the web.
- PipesHub Cloud pricing was not found.
- docs.pipeshub.com could not be fetched, so any cloud guides that exist only on the docs site are unverified.

## 2. Infra footprint

### Takeaway
PipesHub ships as one large all-in-one app container plus about 4 datastores by default:
- **App:** Node, 8 Python services and the UI, all under a shell process monitor.
- **Datastores:** MongoDB, Redis, Qdrant and Neo4j.
- **Sizing:** it wants a ~16 GB host (8 GB "eval" mode).

Onyx's standard stack is about 11 containers, with separate API, web, background, model servers, Postgres, OpenSearch, Redis, nginx, MCP and code interpreter:
- **Standard:** minimum 4 vCPU / 10 GB RAM.
- **Lite:** a genuinely small chat-only mode at 2 vCPU / 2 GB minimum and under 1 GB at baseline, with only Postgres as the datastore.

### Cited Findings
- **PipesHub default compose services:**
  - **Always on:** `pipeshub-ai`, `mongodb`, `redis` and `qdrant`.
  - **Behind profiles:** `docker-socket-proxy` and `sandbox-image` (profile `sandbox`); `searxng` and `crawl4ai` (search and fetch); `arango` (graph-arango); `neo4j` (graph-neo4j); `etcd` (kv-etcd); `zookeeper` and `kafka-1` (broker-kafka).
  - **Memory limits:** app `${APP_MEMORY_LIMIT:-10G}`, MongoDB 2G, Qdrant 6G (4 CPUs), Neo4j 3G (4 CPUs).
  - Source: [pipeshub docker-compose.yml](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/docker-compose/docker-compose.yml)
- **PipesHub monolith:** one container starts Node (`dist/index.js`), the Slack bot, and Python `embedding_main`, `docling_main`, `indexing_main`, `connectors_main`, `query_main`, `parsing_main` and `extraction_main` as background processes. — [pipeshub deployment/process_monitor.sh](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/process_monitor.sh)
- **PipesHub deployment types:**

  | Type | Image | Datastores / broker | RAM class |
  |---|---|---|---|
  | Slim | `pipeshub-ai:slim`, embedding model downloaded on first use | Neo4j, Redis Streams, Redis KV | 16 GB host / 8 GB Docker VM |
  | Full | `pipeshub-ai:latest`, bge-large-en-v1.5 bundled (+~1.3 GB), "Production, air-gapped" | ArangoDB, Kafka, etcd | 16 GB+ |
  | Eval | slim image, no sandbox, no Slack bot | Neo4j kept | 8 GB-class laptop |

  The doc also says: "Do not drop Neo4j to save RAM". — [pipeshub ADVANCED_DEPLOYMENT.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/docker-compose/ADVANCED_DEPLOYMENT.md)
- **PipesHub installer RAM thresholds:** it warns below 7,500 MB (eval), 10,240 MB (Docker Desktop VM) or 15,000 MB (16 GB-class native host). It auto-sizes the app memory limit with a 6 GB floor, a 32 GB cap and a 4 GB reserve for sibling services ("Neo4j dominates at 1.5-2 GB"). — [pipeshub install.sh lines ~215-320, ~790-830](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/docker-compose/install.sh)
- **Onyx standard compose services:**
  - **Default:** `api_server`, `background`, `web_server`, `mcp_server`, `inference_model_server`, `indexing_model_server`, `relational_db` (Postgres), `opensearch`, `nginx`, `cache` (Redis) and `code-interpreter`.
  - **Behind profiles:** `webhook_server`, plus `object-store`, `minio` and `object-store-copy` (s3-filestore).
  - Source: [onyx docker-compose.yml](https://github.com/onyx-dot-app/onyx/blob/main/deployment/docker_compose/docker-compose.yml)
- **Onyx background workers:** the `background` container runs 13 supervisord programs: celery workers primary, light, heavy, docprocessing, user_file_processing, scheduled_tasks, docfetching and monitoring, plus celery_beat, a beat watchdog, slack_bot, discord_bot and a log handler. — [onyx backend/supervisord.conf](https://github.com/onyx-dot-app/onyx/blob/main/backend/supervisord.conf)
- **Onyx resource requirements:**
  - **Standard:** minimum 4 vCPU / 10 GB RAM / 32 GB + ~2.5x indexed data; preferred 8+ vCPU / 16+ GB / 500 GB for organizations under 5,000 users.
  - **Lite:** minimum 2 vCPU / 2 GB / 10 GB; preferred 4 vCPU / 4 GB / 50 GB.
  - **Recommended instances:** standard `m7g.xlarge`, `e2-standard-4/8` or `D4s_v3`; Lite `t3.medium`, `e2-medium` or `B2s`.
  - **Kubernetes per-container:** totals at least ~12 CPU / ~24 GB.
  - Source: [onyx snippets/resourcing-overview.mdx](https://github.com/onyx-dot-app/onyx/blob/main/documentation/snippets/resourcing-overview.mdx); [onyx getting_started/resourcing.mdx](https://github.com/onyx-dot-app/onyx/blob/main/documentation/deployment/getting_started/resourcing.mdx)
- **Onyx Lite:**
  - It is a compose overlay that disables the vector DB, Redis, the model servers and the background workers. Postgres takes over cache, auth and file storage, and Lite starts six services.
  - Connectors and RAG are disabled, while chat, tools, file uploads, Projects and the code interpreter still work. Lite "uses under 1GB of memory at baseline".
  - There is also a Helm `values-lite.yaml`.
  - Sources: [onyx docker-compose.onyx-lite.yml](https://github.com/onyx-dot-app/onyx/blob/main/deployment/docker_compose/docker-compose.onyx-lite.yml); [onyx README.md](https://github.com/onyx-dot-app/onyx/blob/main/README.md); [onyx values-lite.yaml](https://github.com/onyx-dot-app/onyx/blob/main/deployment/helm/charts/onyx/values-lite.yaml)
- **Onyx Helm sizing tiers:**
  - small, up to ~200 users and under ~500k docs: 1x 8 vCPU / 32 GiB;
  - medium, ~200–1,000 users and 0.5–2M docs: m7i.4xlarge x1–5;
  - large, 1,000+ users: m7i.4xlarge x2–8 plus a dedicated index node.
  - Source: [onyx SIZING.md](https://github.com/onyx-dot-app/onyx/blob/main/deployment/helm/charts/onyx/SIZING.md)
- **Air-gapped install, Onyx:**
  - `onyx-cli deploy install --offline` and `deploy upgrade --offline` work from images staged with `docker save`/`docker load`, and the CLI checks that every needed image is present before starting.
  - The `ONYX_AIRGAPPED=true` env flag supports this.
  - CI covers it with `docker-compose.airgap-test.yml` (`networks.default.internal: true`) and `backend/tests/airgap`.
  - Sources: [onyx onyx_cli.mdx "Offline install"](https://github.com/onyx-dot-app/onyx/blob/main/documentation/deployment/local/onyx_cli.mdx); [onyx configuration.mdx](https://github.com/onyx-dot-app/onyx/blob/main/documentation/deployment/configuration/configuration.mdx); [onyx docker-compose.airgap-test.yml](https://github.com/onyx-dot-app/onyx/blob/main/deployment/docker_compose/docker-compose.airgap-test.yml)
- **Air-gapped install, PipesHub:** `--no-pull` starts from cached images ("air-gapped / keep current"), and the Full image is labelled "Production, air-gapped" because it bundles the embedding model. — [pipeshub install.sh](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/docker-compose/install.sh); [pipeshub ADVANCED_DEPLOYMENT.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/docker-compose/ADVANCED_DEPLOYMENT.md)

### Inferences
- **Container count:** PipesHub runs fewer containers (about 5 by default), which makes simple installs easier.
- **Memory floor:** PipesHub's floor (~16 GB, 8 GB eval) is higher than Onyx Standard's 10 GB minimum. PipesHub has nothing comparable to Onyx Lite's 2 GB chat-only mode.
- **Isolation and scaling:** PipesHub's single container puts every service into one memory and cgroup budget, so the services cannot be isolated or scaled independently in Compose.
- **Air-gap support:** Onyx's is more complete and is tested in CI. PipesHub's is a cached-image flag plus a bundled model, and I found no documented `docker save`/`load` workflow or CI air-gap test.

### Gaps
- GPU guidance was not found in either repo's deployment docs. The quick grep found nothing in the PipesHub README or ADVANCED_DEPLOYMENT, nor in Onyx's getting_started docs.
- I found no PipesHub disk-minimum figure in docs. The installer checks disk, but the threshold was not extracted.

## 3. Upgrades, migrations and release cadence

### Takeaway
Onyx is at major v5 and ships far more often:
- 25 minor/major releases and 252 stable semver tags in the 12 months to 1 Nov 2026;
- patch backports on parallel release branches;
- 499 Alembic migrations, plus 12 tenant-schema migrations.

PipesHub reached v1.0.0 on 2026-10-27 with 17 GitHub releases in the same window. It has a strong release gate (install and upgrade-from-previous on amd64 and arm64, promotion by digest) and a rollback script. Its data migrations are 16 ad-hoc Python graph migration modules plus Node config/KV migrations, not a schema-versioning framework like Alembic.

### Cited Findings
- **Onyx tag counts:** git tags dated 2025-11-01 to 2026-11-01 include 252 stable `vX.Y.Z` tags (556 tags in total, including beta, cloud and nightly). Of these, 25 are `.0` minor/major releases from v2.2.0 (2025-11-04) to v5.0.0 (2026-10-23):
  - v3.0.0 on 2026-03-09;
  - v4.0.0 on 2026-05-26;
  - v5.0.0 on 2026-10-23.
  - Source: tag dates from the shallow fetch of [onyx tags](https://github.com/onyx-dot-app/onyx/tags). Tag dates are commit dates; GitHub Releases objects could not be counted because the API was denied.
- **Onyx parallel patches:** patch lines run in parallel, for example v4.8.10 (2026-10-29), v4.9.6 (2026-10-29) and v5.0.3 (2026-10-30). Automated CVE issues are filed per release branch (`release/v5.0`, `release/v5.1`), for example #17028–#17033 on 2026-10-31. — [onyx tags](https://github.com/onyx-dot-app/onyx/tags); [onyx issues search](https://github.com/onyx-dot-app/onyx/issues?q=is%3Aissue+OOM+OR+memory+OR+helm+OR+upgrade+created%3A%3E2026-01-01)
- **Onyx release docs:**
  - Versioning is SemVer, with stable and beta channels.
  - Nightly images are built from `main`.
  - The docs warn that deploying without a version gives you the nightly builds.
  - `cloud.onyx.app/api/versions` returns the latest stable and beta versions.
  - Source: [onyx release_process.mdx](https://github.com/onyx-dot-app/onyx/blob/main/documentation/deployment/miscellaneous/release_process.mdx)
- **Onyx upgrade tooling:**
  - `onyx-cli deploy upgrade [--tag]` rewrites only `IMAGE_TAG` in `.env`.
  - It detects hand-edited managed files from a checksum manifest, backs them up and asks before overwriting.
  - A downgrade needs `--allow-downgrade`.
  - Services keep serving while the new images download.
  - Source: [onyx onyx_cli.mdx "Upgrade"](https://github.com/onyx-dot-app/onyx/blob/main/documentation/deployment/local/onyx_cli.mdx)
- **Onyx Helm breaking changes:** documented per chart version in `MIGRATION.md`. The 0.8.x→0.9.x move from MinIO to SeaweedFS uses dual-write, read-fallback and a copy Job, with "no downtime". Chart version is 0.9.30. — [onyx helm/MIGRATION.md](https://github.com/onyx-dot-app/onyx/blob/main/deployment/helm/MIGRATION.md); [onyx Chart.yaml](https://github.com/onyx-dot-app/onyx/blob/main/deployment/helm/charts/onyx/Chart.yaml)
- **Onyx migrations:** `backend/alembic/versions` has 499 files and `backend/alembic_tenants/versions` has 12. — [onyx backend/alembic](https://github.com/onyx-dot-app/onyx/tree/main/backend/alembic)
- **PipesHub releases:** 17 non-prerelease GitHub releases from 2025-11-01 to 2026-11-01, starting at 0.1.0 (2025-12-23):
  - v0.2.0 on 2026-02-11;
  - v0.3.0 on 2026-03-20;
  - v0.4.0–v0.4.5 in May;
  - v0.5.0 on 2026-07-01;
  - v0.6.0 on 2026-08-10;
  - v0.7.0 on 2026-08-26;
  - v0.8.0 on 2026-09-14;
  - v0.9.0/v0.9.1 on 2026-09-28;
  - v0.10.0 on 2026-10-12;
  - v1.0.0 on 2026-10-27.
  - Cadence moved from roughly monthly to roughly every two weeks from August.
  - Source: `gh api repos/pipeshub-ai/pipeshub-ai/releases` ([releases](https://github.com/pipeshub-ai/pipeshub-ai/releases))
- **PipesHub release gate:**
  - Pushing a tag builds both variants for amd64 and arm64.
  - Verification covers a first-run install and an upgrade from the previous release.
  - The candidate is promoted by digest, so `:latest` "only ever points at an image that passed".
  - The doc notes: "Before this existed … releases were monthly while merges were daily."
  - Source: [pipeshub deployment/docker-compose/RELEASE.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/docker-compose/RELEASE.md)
- **PipesHub rollback:**
  - `rollback.sh` moves `IMAGE_TAG` back and re-runs the upgrade path, with `--list` and `--dry-run`.
  - It explicitly "does NOT … restore data. A rollback undoes code, not migrations."
  - Source: [pipeshub rollback.sh](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/docker-compose/rollback.sh)
- **PipesHub migrations:**
  - 16 Python migration modules in `backend/python/app/migrations/`, for example `kb_apps_migration.py` and `record_link_migration.py`.
  - Node config migrations in `configuration_manager/services/migrations` and `libs/keyValueStore/migration`.
  - A `db-migrations.yml` CI workflow.
  - Sources: [pipeshub app/migrations](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/backend/python/app/migrations); [pipeshub .github/workflows/db-migrations.yml](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/.github/workflows/db-migrations.yml)
- **PipesHub 1.0 breaking change:** the release removed SAML SSO and generic OAuth/OIDC sign-in. It came with a dedicated upgrade note and an automatic policy rewrite that adds password sign-in so nobody is locked out. — [pipeshub docs/deployment/upgrading-to-1.0-sign-in.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/docs/deployment/upgrading-to-1.0-sign-in.md)
- **PipesHub version lag:** the Helm chart appVersion is "0.10.0" while the app is at v1.0.0. — [pipeshub Chart.yaml](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/helm/pipeshub-ai/Chart.yaml)
- **PipesHub release-quality issues:**
  - #3030, "Published :slim image crashes the entire Python backend on a default install … fixed on main but unreleased" (2026-08-21);
  - #3046, an empty-string env var crash on slim.
  - Sources: [issue #3030](https://github.com/pipeshub-ai/pipeshub-ai/issues/3030); [issue #3046](https://github.com/pipeshub-ai/pipeshub-ai/issues/3046)

### Inferences
- **Onyx is ahead on upgrade maturity:**
  - a supported-branch backport model (several minors patched at once);
  - an ordered schema-migration framework with about 500 revisions;
  - a chart migration guide;
  - CLI upgrades that protect hand edits.
- **PipesHub is ahead on one point, verifying the published artifact:** the digest-promoted, upgrade-tested release gate. No equivalent Onyx gate was examined.
- **PipesHub has no patch-branch model,** so issue #3030 shows a fix sitting "unreleased" on main.
- **PipesHub's graph-data migrations are ad-hoc modules** with no down-migrations, which matches rollback.sh's warning.

### Gaps
- Onyx GitHub Release objects (as opposed to tags) were not counted because the API was denied, and the Onyx changelog.mdx was not counted.
- Backward-compatibility guarantees, such as a supported version-skip range, were not found for either product.

## 4. Scaling, workers, multi-tenancy, observability and backups

### Takeaway
Onyx's architecture scales by component:
- 8 Celery worker types, each with its own HPA and KEDA ScaledObject templates;
- api-server and webserver HPAs;
- ServiceMonitors and Grafana dashboards;
- a Prometheus `/metrics` endpoint on each service;
- schema-per-tenant multi-tenancy (alembic_tenants, `docker-compose.multitenant.yml`).

PipesHub's Helm chart scales the whole monolith pod: `replicaCount: 2` plus an HPA. It has HA datastore defaults (Mongo 2+arbiter, ZooKeeper 3, Kafka 2, Redis 2), a PDB and a NetworkPolicy. I found no Prometheus endpoint or Sentry integration in core code, only a product-telemetry pusher. Its indexing-throughput tuning is documented in depth.

### Cited Findings
- **Onyx autoscaling templates:** 89 Helm templates, among them HPA *and* KEDA ScaledObject files for the api, webserver and each celery worker (docfetching, docprocessing, heavy, light, monitoring, primary, scheduled-tasks, user-file-processing). There are also ServiceMonitors for the api, MCP, webhook and celery workers, plus `pdb.yaml`. — [onyx helm templates](https://github.com/onyx-dot-app/onyx/tree/main/deployment/helm/charts/onyx/templates)
- **Onyx Grafana dashboards:** shipped with the chart: `indexing-pipeline.json`, `indexing-pruning.json`, `opensearch-search-latency.json` and `redis-queues.json`. — [onyx helm dashboards](https://github.com/onyx-dot-app/onyx/tree/main/deployment/helm/charts/onyx/dashboards)
- **Onyx metrics:** Prometheus metrics come from the API server (`/metrics` on 8080, bearer `METRICS_AUTH_TOKEN`), the MCP server (8090) and each background worker port. `monitoring.serviceMonitors.enabled` turns on scraping. — [onyx production/monitoring.mdx](https://github.com/onyx-dot-app/onyx/blob/main/documentation/deployment/production/monitoring.mdx)
- **Onyx instrumentation in code:** `prometheus_client` appears in 25 files and `sentry_sdk` in 8 files under `backend/onyx`, with opentelemetry in 1. — grep of [onyx backend/onyx](https://github.com/onyx-dot-app/onyx/tree/main/backend/onyx)
- **Onyx HA doc:**
  - stateless components can run several replicas, while `celery_beat` must stay at 1;
  - the chart creates an HPA by default, with `minReplicas: 2` shown in the example;
  - it also covers probes and graceful shutdown.
  - Source: [onyx production/high_availability.mdx](https://github.com/onyx-dot-app/onyx/blob/main/documentation/deployment/production/high_availability.mdx)
- **Onyx sizing principles:**
  - "Scale the api-server out, not up";
  - OpenSearch memory pressure is off-heap;
  - a pegged indexing model server can trip the indexing stall watchdog.
  - Source: [onyx SIZING.md](https://github.com/onyx-dot-app/onyx/blob/main/deployment/helm/charts/onyx/SIZING.md)
- **Onyx multi-tenancy:** `backend/alembic_tenants` has 12 migrations and there is a `docker-compose.multitenant.yml`. — [onyx backend/alembic_tenants](https://github.com/onyx-dot-app/onyx/tree/main/backend/alembic_tenants); [onyx docker-compose.multitenant.yml](https://github.com/onyx-dot-app/onyx/blob/main/deployment/docker_compose/docker-compose.multitenant.yml)
- **Onyx external services and backups:** the external_services page (RDS and others) and the Terraform and CLI docs mention backups. — [onyx production/external_services.mdx](https://github.com/onyx-dot-app/onyx/blob/main/documentation/deployment/production/external_services.mdx)
- **PipesHub Helm values:**
  - top-level `replicaCount: 2`, `terminationGracePeriodSeconds: 300`, `autoscaling` ("Metrics Server is REQUIRED"), `podDisruptionBudget`, `networkPolicy` and `spreadAcrossNodes`;
  - bundled StatefulSets with HA counts: MongoDB 2 data nodes plus an arbiter, ZooKeeper 3, Kafka 2 (RF=2), Redis 2;
  - templates `hpa.yaml`, `pdb.yaml`, `networkpolicy.yaml` and `externalsecret.yaml`.
  - Sources: [pipeshub values.yaml](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/helm/pipeshub-ai/values.yaml); [pipeshub templates](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/deployment/helm/pipeshub-ai/templates)
- **PipesHub single deployment:** there is one `deployment.yaml` for the app, with no per-service Deployments for indexing, query or connectors, because every service runs inside the monolith image. — [pipeshub templates](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/deployment/helm/pipeshub-ai/templates); [pipeshub process_monitor.sh](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/process_monitor.sh)
- **PipesHub observability:**
  - `prometheus_client` or `prom-client` appears in only 2 files: `app/telemetry/backend.py` and `libs/services/telemetry/metrics-backend.ts`;
  - "sentry" appears only in an MCP server integration (`agents/mcp/servers/sentry.py`), not as error reporting;
  - opentelemetry appears in 0 files.
  - Helm `telemetry.collection.enabled` controls *product* telemetry, which is on by default.
  - Sources: grep of [pipeshub backend](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/backend); [pipeshub values.yaml](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/helm/pipeshub-ai/values.yaml)
- **PipesHub telemetry consent:** open issue #3625 says "Self-hosted installation enables data collection by default without clear notice during setup" (2026-09-27). — [issue #3625](https://github.com/pipeshub-ai/pipeshub-ai/issues/3625)
- **PipesHub indexing internals:**
  - `docs/indexing-service.md` covers the consumer loop, heavy/light tiers, the resource governor and the "known throughput-collapse root cause";
  - ADVANCED_DEPLOYMENT tells users to read `resource_governor.mem_pressure` when indexing is slow and the CPU is idle;
  - there are also `docs/deployment/vector-db-sizing.md` and `qdrant-search-tuning.md`.
  - Sources: [pipeshub AGENTS.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/AGENTS.md); [pipeshub ADVANCED_DEPLOYMENT.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/docker-compose/ADVANCED_DEPLOYMENT.md); [pipeshub docs/deployment](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/docs/deployment)
- **PipesHub performance CI:** `perf-indexing.yml`, `perf-query.yml`, `perf-load.yml` and `perf-scale.yml`, plus a `loadtest/` directory. — [pipeshub .github/workflows](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/.github/workflows)
- **PipesHub backups:** documented for EKS only ("10. Backups and restore"). Backup is not covered in the compose docs beyond rollback's warning. — [pipeshub docs/deployment/aws-eks.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/docs/deployment/aws-eks.md)
- **PipesHub pluggable stores:**
  - graph: Neo4j or Arango;
  - vector: Qdrant, OpenSearch or Redis;
  - KV: Redis, etcd or Postgres;
  - broker: Kafka, Redis Streams or Postgres.
  - Each choice goes through a factory. — [pipeshub AGENTS.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/AGENTS.md)

### Inferences
- **Scaling and observability is Onyx's largest operational lead.** Its scaling is per-workload, with indexing workers scaled separately from the API, and its first-party metrics come with dashboards.
- **PipesHub's horizontal scaling means more copies of everything.** That wastes memory, and it cannot target the indexing tier alone.
- **PipesHub's backend flexibility is a counter-advantage, at an ops cost.** Its pluggable stores across graph, vector, KV and broker are something Onyx does not offer. Onyx is fixed on Postgres, OpenSearch, Redis and S3.
- **More choice also means more test matrices.** PipesHub's `storage-backends.yml` and `pg-stores.yml` workflows cover them.

### Gaps
- No public indexing-throughput claim (docs per hour) was found for either product.
- PipesHub multi-tenant architecture was not found in docs. The README, docs and env.template grep found no "multi-tenant"; PipesHub Cloud presumably runs some tenancy model, which is unverified.
- Onyx's Sentry configuration docs were not read.

## 5. Deployment security posture

### Takeaway
Both Helm charts support existingSecret/ExternalSecrets, NetworkPolicy and pod security contexts. On authentication, PipesHub 1.0 is now *behind*:
- PipesHub 1.0 removed SAML and generic OIDC, leaving password, email OTP, Google and Microsoft sign-in.
- Onyx documents SAML, OIDC, OAuth, SCIM and JWT.

On encryption at rest, Onyx's MIT edition does *not* encrypt stored credentials; that is an EE-only feature. PipesHub has an encryption service keyed by `SECRET_KEY` in the open-source build.

### Cited Findings
- **Onyx auth docs:** `authentication/{saml,oidc,oauth,sso_providers,scim,basic,jwt}.mdx`. — [onyx documentation/deployment/authentication](https://github.com/onyx-dot-app/onyx/tree/main/documentation/deployment/authentication)
- **Onyx Kubernetes security doc:**
  - image pinning, security contexts, service accounts and network policies;
  - every `auth` section accepts `existingSecret`;
  - one `ExternalSecret` is supported through External Secrets Operator.
  - Templates `network-policy-sandbox-egress.yaml` and `network-policy-sandbox-push.yaml`.
  - Sources: [onyx production/security.mdx](https://github.com/onyx-dot-app/onyx/blob/main/documentation/deployment/production/security.mdx); [onyx helm templates](https://github.com/onyx-dot-app/onyx/tree/main/deployment/helm/charts/onyx/templates)
- **Onyx MIT encryption:** `_encrypt_string` logs "MIT version of Onyx does not support encryption of secrets" and returns plaintext bytes. Real encryption lives in `backend/ee/onyx/utils/encryption.py`. — [onyx backend/onyx/utils/encryption.py](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/utils/encryption.py)
- **Onyx edition gating:** EE adds SSO, RBAC and white-labeling according to third-party summaries, and the price is quote-based. — [wz-it.com](https://wz-it.com/en/blog/onyx-self-hosted-installation/); [docs.onyx.app enterprise edition](https://docs.onyx.app/admins/getting_started/enterprise_edition)
- **PipesHub sign-in after 1.0:** SAML and generic OAuth/OIDC are removed. Password, email OTP, Google and Microsoft remain, and Azure AD is folded into Microsoft. — [pipeshub upgrading-to-1.0-sign-in.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/docs/deployment/upgrading-to-1.0-sign-in.md)
- **PipesHub encryption:** `libs/services/encryption.service.ts` exists, and `backend/env.template` requires `SECRET_KEY` (placeholder `my-secret`). The installer generates secrets and supports `--rotate-signing-secrets`. — [pipeshub encryption.service.ts](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/nodejs/apps/src/libs/services/encryption.service.ts); [pipeshub env.template](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/env.template); [pipeshub install.sh](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/docker-compose/install.sh)
- **PipesHub Helm security:**
  - `secretManagement.externalSecrets`, `podSecurityContext`, `securityContext` and `networkPolicy`;
  - `maxAuthRequestsPerMinute: 10`;
  - `trustProxy` guidance that says "Never 'true'".
  - In compose, the sandbox profile isolates Docker-socket access to `docker-socket-proxy` ("the only container with the Docker socket").
  - Sources: [pipeshub values.yaml](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/helm/pipeshub-ai/values.yaml); [pipeshub ADVANCED_DEPLOYMENT.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/deployment/docker-compose/ADVANCED_DEPLOYMENT.md)
- **Supply-chain CI:**
  - **Onyx:** `cve-alerts.yml`, `cve-alerts-nightly.yml`, `cve-tracking.yml` and `audit.yml`, which file per-branch vulnerable-dependency issues automatically. — [onyx .github/workflows](https://github.com/onyx-dot-app/onyx/tree/main/.github/workflows)
  - **PipesHub:** `codeql.yml` and `dependency-review.yml`. — [pipeshub .github/workflows](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/.github/workflows)

### Inferences
- **Enterprise SSO is a clear gap for PipesHub after 1.0.** An enterprise that needs Okta SAML or generic OIDC is better served by Onyx, although on Onyx it may require the paid EE.
- **At-rest credential encryption in the free build is a point in PipesHub's favour** against Onyx Community Edition.

### Gaps
- Which exact Onyx auth methods are EE-only was not verified against official docs.
- Data-store encryption at rest (disk/KMS) guidance was not found for PipesHub. Onyx's Terraform modules probably use encrypted RDS and S3, but this is unverified.

## 6. Docs quality

### Takeaway
Onyx has a large, structured Mintlify docs site in the repo:
- about 410 `.mdx` pages, with a docs-check CI workflow and a docs-publish CI workflow;
- separate deployment, admin, security, developer/API reference and agent-wiki trees;
- an API reference split into 13 domains.

PipesHub's in-repo docs are deep engineering documents (indexing, context engineering, text fragments) plus 4 deployment docs. Its user-facing docs site could not be inspected.

### Cited Findings
- **Onyx docs tree:**
  - `documentation/` has `docs.json`, `admins/`, `deployment/`, `developers/`, `security/`, `agent-wiki/`, `changelog.mdx` and about 410 `.mdx` files.
  - Deployment alone has more than 35 pages across getting_started, local, cloud, production, authentication, configuration and miscellaneous.
  - Source: [onyx documentation](https://github.com/onyx-dot-app/onyx/tree/main/documentation)
- **Onyx API reference:** `developers/api_reference/` has 13 sections: actions, agents, analytics, chat, files_connectors, ingestion, miscellaneous, projects, query_history, search, usage, user_groups and user_management. Guides include `create_connector`, `index_files_ingestion_api`, `search_api_guide`, `chat_new_guide` and `llm_gateway`. — [onyx developers](https://github.com/onyx-dot-app/onyx/tree/main/documentation/developers)
- **Onyx docs CI:** `docs-check.yml` and `docs-publish.yml`. — [onyx .github/workflows](https://github.com/onyx-dot-app/onyx/tree/main/.github/workflows)
- **PipesHub in-repo docs:**
  - `docs/` holds 22 top-level documents, for example `indexing-service.md`, `context-engineering.md`, `ai-models.md` and `text-fragments.md`;
  - `docs/deployment/` holds 4: `aws-eks.md`, `qdrant-search-tuning.md`, `upgrading-to-1.0-sign-in.md` and `vector-db-sizing.md`;
  - deployment directory docs: `ADVANCED_DEPLOYMENT.md` (626 lines), `BOOTSTRAP.md` and `RELEASE.md`.
  - Sources: [pipeshub docs](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/docs); [pipeshub deployment/docker-compose](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/deployment/docker-compose)
- **PipesHub README troubleshooting:** a short troubleshooting list covering HTTPS and `docker compose logs`. — [pipeshub README.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/README.md)
- **PipesHub docs drift:**
  - #3779, "Locale READMEs still start production with docker-compose.prod.yml";
  - #3259, the Spanish README documents an obsolete compose file.
  - Sources: [issue #3779](https://github.com/pipeshub-ai/pipeshub-ai/issues/3779); [issue #3259](https://github.com/pipeshub-ai/pipeshub-ai/issues/3259)
- **PipesHub agent docs:** AGENTS.md points to `https://docs.pipeshub.com/for-agents.md`, so a docs site exists. — [pipeshub AGENTS.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/AGENTS.md)

### Inferences
- **Onyx's docs are broader on operator topics:** per-cloud, HA, monitoring, security, auth and resourcing are all in one versioned tree that CI checks.
- **PipesHub's docs are stronger on internal architecture.** For operators they rely on long README and ADVANCED_DEPLOYMENT files, and those files have shown drift in the 12 locale READMEs.

### Gaps
- docs.pipeshub.com was blocked by the network allowlist, so its page count, search, API reference and troubleshooting coverage are unverified. Allow that host and recheck.

## 7. Contributor experience

### Takeaway
Both have about 525–550-line CONTRIBUTING guides and heavy CI. PipesHub runs 35 workflows and Onyx 54. Onyx adds:
- a devcontainer;
- pre-commit hooks (ruff, oxfmt, oxlint, compose-sync);
- a VSCode debugger setup;
- a Go `ods` devtools CLI;
- engineering best-practice sections.

Onyx also requires an IP-assignment agreement for EE contributions. PipesHub has:
- a one-shot `scripts/verify.sh`;
- far more open good-first-issues (40 open vs 1);
- fast PR turnaround (median about 5 hours for its last 44 merged PRs).

Most of those PipesHub PRs come from core team accounts.

### Cited Findings
- **Onyx CONTRIBUTING.md (525 lines):**
  - contribution process: get the feature approved, then the design approved, then "IP attribution for EE contributions";
  - pre-commit with ruff, oxfmt and oxlint;
  - a VSCode debugger setup, local Kubernetes, macOS notes and a long "Engineering Best Practices" section;
  - support on Discord.
  - Source: [onyx CONTRIBUTING.md](https://github.com/onyx-dot-app/onyx/blob/main/CONTRIBUTING.md)
- **Onyx tooling:** `.devcontainer/` (Dockerfile, devcontainer.json, firewall init) and `.pre-commit-config.yaml`. `contributor_ip_assignment/EE_Contributor_IP_Assignment_Agreement.{md,pdf}`. — [onyx .devcontainer](https://github.com/onyx-dot-app/onyx/tree/main/.devcontainer); [onyx contributor_ip_assignment](https://github.com/onyx-dot-app/onyx/tree/main/contributor_ip_assignment)
- **Onyx tests and CI:**
  - about 1,820 `test_*.py` files under `backend/tests`, split into unit, integration, external_dependency_unit, regression, daily, evals and airgap;
  - about 354 web `*.test.ts*` files and about 100 Playwright specs;
  - 54 workflows, including pr-python-tests, pr-playwright-tests, pr-helm-chart-testing, pr-database-tests, pr-terraform-provider-tests, merge-group and nightly-close-stale-issues.
  - Sources: [onyx backend/tests](https://github.com/onyx-dot-app/onyx/tree/main/backend/tests); [onyx .github/workflows](https://github.com/onyx-dot-app/onyx/tree/main/.github/workflows)
- **Onyx good-first-issue label:** 14 issues in total, 1 open ("Support for Google Chat", from 2023). — [onyx good first issue](https://github.com/onyx-dot-app/onyx/issues?q=is%3Aissue+label%3A%22good+first+issue%22)
- **PipesHub CONTRIBUTING.md (549 lines):**
  - finding work, docs, new connectors (linked to CONNECTOR.md and CONNECTOR_CHECKLIST.md), and per-OS system packages;
  - default and optional stores, per-service run instructions and a health check;
  - an architecture section;
  - testing with coverage thresholds of 90% lines and 80% branches for Node;
  - "Checking everything at once" with `scripts/verify.sh`.
  - Source: [pipeshub CONTRIBUTING.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/CONTRIBUTING.md)
- **PipesHub tests and CI:**
  - about 2,543 Python `test_*.py`, about 526 Node spec/test files, about 431 frontend test files and about 126 integration-test files;
  - 35 workflows, including backend-matrix, storage-backends, pg-stores, e2e-smoke, arm64-smoke, helm-chart, openapi-validator, perf-*, weekly-coverage, sdk-tests and answer-quality-evals.
  - Sources: [pipeshub backend/python/tests](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/backend/python/tests); [pipeshub .github/workflows](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/.github/workflows)
- **PipesHub dev tooling:** hot-reload compose files (`deployment/docker-compose/hot-reload/{Dockerfile.dev,docker-compose.dev.neo4j.yml,...arango.yml}`). There is no `.devcontainer` or `.pre-commit-config.yaml` at the repo root, and ruff is configured in `backend/python/pyproject.toml`. — [pipeshub hot-reload](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/deployment/docker-compose/hot-reload)
- **PipesHub issues:** 52 `good first issue` issues, 40 open, and 107 open issues in total. — `gh api search/issues` on [pipeshub issues](https://github.com/pipeshub-ai/pipeshub-ai/issues?q=is%3Aissue+label%3A%22good+first+issue%22)
- **PipesHub PR throughput and turnaround:**
  - 1,839 merged PRs created since 2026-08-01, 1,711 of them excluding dependabot;
  - the last 44 merged non-bot PRs had a median of about 4.9 hours from open to merge;
  - authors were mostly core team members (for example abhishekgml1991 with 18 and abhishek-pipeshub with 8).
  - Source: `gh api repos/pipeshub-ai/pipeshub-ai/pulls` ([pulls](https://github.com/pipeshub-ai/pipeshub-ai/pulls))
- **Licences:** PipesHub is Apache-2.0 with 3,899 stars and 631 forks, created 2025-03-06. — `gh api repos/pipeshub-ai/pipeshub-ai`. Onyx Community Edition is MIT, with an `ee/` directory under a separate licence. — [onyx LICENSE](https://github.com/onyx-dot-app/onyx/blob/main/LICENSE); [onyx backend/ee](https://github.com/onyx-dot-app/onyx/tree/main/backend/ee)

### Inferences
- **PipesHub is friendlier to newcomers:** it has many open good-first-issues, an Apache licence, no CLA or IP assignment, and fast merges.
- **Onyx has more mature dev ergonomics:** devcontainer, pre-commit, debugger configs and generated compose files with drift checks. Its EE split adds contributor friction.

### Gaps
- Onyx PR turnaround, star count and contributor counts were not measured because the API was denied.
- The share of external contributors to PipesHub was not computed beyond the sample of 44 PRs.

## 8. API and extensibility

### Takeaway
- **PipesHub** maintains a single large OpenAPI spec (73,335 lines) that CI validates. It has an SDK test workflow and a documented `ConnectorFactory` framework with a connector checklist.
- **Onyx** publishes an API reference in 13 domains with ingestion and search guides. It also ships an MCP server, a webhook server, OpenAPI-based custom tools ("plugins"), a Terraform provider and a CLI.

### Cited Findings
- **PipesHub API and connector framework:**
  - `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml` is 73,335 lines, and AGENTS.md says a mismatch with new routes is "blocking";
  - `openapi-validator.yml`, `sdk-tests.yml` and `sdk-break-approval.yml` run in CI;
  - connectors extend `ConnectorFactory`, following CONNECTOR.md and CONNECTOR_CHECKLIST.md;
  - MCP is served at `/mcp`.
  - Sources: [pipeshub openapi yaml](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml); [pipeshub AGENTS.md](https://github.com/pipeshub-ai/pipeshub-ai/blob/main/AGENTS.md); [pipeshub .github/workflows](https://github.com/pipeshub-ai/pipeshub-ai/tree/main/.github/workflows)
- **Onyx API and extension points:**
  - API reference sections and developer guides, including `create_connector.mdx` and `index_files_ingestion_api.mdx`;
  - `admins/plugins/openapi.mdx`, which imports OpenAPI specs as agent tools;
  - `mcp_server` and `webhook_server` compose services;
  - a `terraform-provider-onyx` with docs and examples.
  - Sources: [onyx developers](https://github.com/onyx-dot-app/onyx/tree/main/documentation/developers); [onyx admins/plugins/openapi.mdx](https://github.com/onyx-dot-app/onyx/blob/main/documentation/admins/plugins/openapi.mdx); [onyx docker-compose.yml](https://github.com/onyx-dot-app/onyx/blob/main/deployment/docker_compose/docker-compose.yml); [onyx terraform-provider-onyx](https://github.com/onyx-dot-app/onyx/tree/main/terraform-provider-onyx)

### Inferences
- **Onyx leads on configuration as code:** its Terraform provider manages Onyx resources declaratively, and its webhook server receives inbound events. PipesHub has nothing equivalent to the provider.
- **PipesHub's machine-readable API contract looks more formally enforced in CI,** with spec validation and an SDK break-approval gate.

### Gaps
- Whether Onyx publishes a machine-readable OpenAPI file or official SDKs was not verified.
- What the PipesHub SDK covers was not examined. Language and package names are unknown, and the only hint found is `integrations/omnigent`.
