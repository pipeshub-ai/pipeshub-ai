# syntax=docker/dockerfile:1
# Slow layers (APT, Rust, Python deps, ML models, runtime stack) live in Dockerfile.base.
# Override with local tags if registry images are missing:
#   docker build --build-arg PYTHON_DEPS_IMAGE=myreg/python-deps --build-arg RUNTIME_BASE_IMAGE=myreg/runtime .
# Slim app image (no pre-baked BGE; ~1.3 GB smaller — model downloads on first use):
#   docker build --build-arg PYTHON_DEPS_IMAGE=pipeshubai/pipeshub-ai-base:python-deps-slim -t pipeshubai/pipeshub-ai:slim .
ARG PYTHON_DEPS_IMAGE=pipeshubai/pipeshub-ai-base:python-deps
ARG RUNTIME_BASE_IMAGE=pipeshubai/pipeshub-ai-base:runtime

FROM ${PYTHON_DEPS_IMAGE} AS python-deps
# The base image bakes in dependencies as of its publish time. Reconcile with the
# current hashed lock (SEC-SC-07) so packages added or moved since the base was
# published end up in the app image at their locked version and file. pip skips
# packages already at their locked version, so this is a fast no-op when the base is
# current and installs only the delta otherwise. The base carries the build toolchain
# (it is built FROM build-base in Dockerfile.base), so a locked source archive can still
# compile here.
WORKDIR /app/python
COPY backend/python/pyproject.toml ./
COPY backend/python/requirements/ ./requirements/
ENV PIP_ROOT_USER_ACTION=ignore
# A base image published before the fork was excluded still carries unclecode-litellm, which installs into the
# same litellm/ directory as the real package. Both leave together so the locked litellm is installed whole.
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    if python3 -m pip show -q unclecode-litellm 2>/dev/null; then \
      python3 -m pip uninstall -y --disable-pip-version-check unclecode-litellm litellm; \
    fi && \
    sh requirements/install.sh runtime --project . --editable && \
    crawl4ai-setup && \
    playwright install chromium


FROM node:24-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS node-runtime

FROM ${RUNTIME_BASE_IMAGE} AS runtime-base

# Presentation previews are converted to PDF at request time. The published
# runtime base historically included Writer and Calc only, leaving the soffice
# wrapper present but unable to load PPT/PPTX files.
# Install CJK fallback fonts until they are available in the published runtime
# base image. LibreOffice uses these when documents reference unavailable fonts.
# git: the worker's repository_fetch (WP-63) clones public repositories outside the sandbox with the distribution's git.
# ripgrep: storage search runs grep stages through it (same: until the base has it).
# Node.js comes from the digest-pinned official image, not a remote setup script piped to a shell (SEC-D-11).
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && \
    node --version
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-impress-nogui fonts-noto-cjk git ripgrep \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# -----------------------------------------------------------------------------
# Stage 1: Node.js Backend Build
# -----------------------------------------------------------------------------
FROM node:24-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS nodejs-backend
WORKDIR /app/backend

COPY backend/nodejs/apps/package*.json ./
COPY backend/nodejs/apps/tsconfig.json ./

# No install scripts on any architecture: the lock ships every native part prebuilt (sharp, msgpackr-extract),
# and what the scripts would add is telemetry (@scarf/scarf), a bun binary nothing runs, or a version notice
# (contracts/supply-chain/install-scripts.json). An allowlisted `npm rebuild` would run here if one were needed.
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm ci --ignore-scripts

COPY backend/nodejs/apps/src ./src
RUN npm run build && \
    # Prune dev dependencies after build
    npm prune --production && \
    # Clean npm cache
    npm cache clean --force

# tsc does not emit .hbs, and emailTemplates.ts reads them from ${process.cwd()}/src
# at runtime, so every template under src is staged here with its path kept.
RUN mkdir -p /app/templates && cd src && find . -name '*.hbs' -exec cp --parents {} /app/templates/ \;

# -----------------------------------------------------------------------------
# Stage 2: Frontend Build (Next.js in `frontend/`)
# -----------------------------------------------------------------------------
# Static export so the Node.js API can serve files from `backend/dist/public`.
FROM node:24-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS frontend-build
WORKDIR /app/frontend

COPY frontend/package*.json ./

# No install scripts (C644-FU-1): optional packages stay in, so the lock's own platform builds of next's SWC and
# unrs-resolver's napi binding are installed from it and nothing is fetched outside the lock at build time.
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm config set legacy-peer-deps true && \
    npm ci --ignore-scripts

COPY frontend/ ./

# Force static export for container builds by setting the env flag that
# `frontend-new/next.config.mjs` already uses to enable `output: 'export'`.
RUN ELECTRON_STATIC=1 npm run build && \
    mkdir -p /out && \
    cp -a out/. /out/

# -----------------------------------------------------------------------------
# Stage 3: Final Runtime Image
# -----------------------------------------------------------------------------
FROM runtime-base AS runtime
WORKDIR /app

# Unprivileged runtime user (uid/gid 1000, matching the Helm chart's runAsUser).
# Its home stays /root so every on-disk path is unchanged across the upgrade:
# local storage records absolute file:// URLs under ~/.local, and existing
# volumes and custom mounts target /root/.local and /root/.cache/huggingface.
# docker-entrypoint.sh hands root-owned data from earlier releases over to it.
# The account must exist (torch resolves its cache directory through getpass),
# and /home/user, the HOME the Helm chart sets, must be writable by it.
RUN groupadd --gid 1000 pipeshub && \
    useradd --uid 1000 --gid 1000 --home-dir /root --no-create-home \
        --shell /usr/sbin/nologin pipeshub && \
    install -d -o 1000 -g 1000 /root/.cache /root/.local /home/user && \
    chown 1000:1000 /root
ENV HOME=/root
# Repositories pushed by `pipeshub index` are mirrored on the data volume, so they survive the container being replaced.
ENV PIPESHUB_LOCAL_REPO_STORE=/data/pipeshub/local-repos
# Pinned so the baked models are still found when HOME is overridden (the
# Helm chart sets HOME=/home/user).
ENV HF_HOME=/root/.cache/huggingface \
    NLTK_DATA=/root/nltk_data \
    PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

# Point fastembed at the pre-populated cache we copy in below, matching the
# FASTEMBED_CACHE_PATH used at build time in the python-deps stage.
ENV FASTEMBED_CACHE_PATH=/root/.cache/fastembed

# Cap ML thread fan-out (PyTorch/OpenBLAS/MKL). runtime_threads.py applies this
# default to all libraries unless OMP_NUM_THREADS is set (in .env, say), which wins.
ENV PIPESHUB_OMP_DEFAULT=2

# Several third-party deps (talon, pysbd, ...) ship regex string literals with
# unescaped backslashes and hit Python 3.12+'s SyntaxWarning at import time.
# We don't control their source, and new deps can hit this same issue, so
# suppress the class globally rather than patching each import site.
ENV PYTHONWARNINGS="ignore::SyntaxWarning"

# Copy Python site-packages from build stage
COPY --from=python-deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=python-deps /usr/local/bin /usr/local/bin

# Copy ML model data (dense HF embeddings + reranker, sparse fastembed, NLTK)
COPY --chown=1000:1000 --from=python-deps /root/.cache/huggingface /root/.cache/huggingface
COPY --chown=1000:1000 --from=python-deps /root/.cache/fastembed /root/.cache/fastembed
COPY --chown=1000:1000 --from=python-deps /root/nltk_data /root/nltk_data

# Layout model for the pdfplumber PDF parser (tables, figures), so it needs no
# download at runtime. Only its own cache dir is handed over: a chown of the
# whole cache would copy every baked model into this layer.
RUN python -c "from huggingface_hub import hf_hub_download; hf_hub_download('docling-project/docling-layout-heron-onnx', 'model.onnx', revision='40bde044036bb181c130ddf6c51792187268748f')" && \
    chown -R 1000:1000 /root/.cache/huggingface/hub/models--docling-project--docling-layout-heron-onnx

# Copy Playwright browser binaries
COPY --chown=1000:1000 --from=python-deps /root/.cache/ms-playwright /root/.cache/ms-playwright

# Copy Node.js backend (already pruned)
COPY --from=nodejs-backend /app/backend/dist ./backend/dist
COPY --from=nodejs-backend /app/templates ./backend/src
COPY --from=nodejs-backend /app/backend/src/modules/api-docs/pipeshub-openapi.yaml ./backend/src/modules/api-docs/pipeshub-openapi.yaml
COPY --from=nodejs-backend /app/backend/node_modules ./backend/dist/node_modules

# Copy frontend build (normalized to /out by the selected frontend stage)
COPY --from=frontend-build /out ./backend/dist/public

# Copy Python application code
COPY backend/python/app/ /app/python/app/
COPY backend/python/harness_core/ /app/python/harness_core/
COPY backend/python/harness_engines/ /app/python/harness_engines/

# Bytecode for the app's own code, built here: the code is root-owned and the services run as uid 1000, so none can
# write __pycache__, and every process compiled every module it imports on every start. That cost each service its
# start time and kept the compiler's peak in its heap: the idle harness worker held about 45 MiB of it (R25OPS-FU-1).
RUN python -m compileall -q -j 0 /app/python/app /app/python/harness_core /app/python/harness_engines

# The durable worker imports the query container directly, unlike query_main
# which imports edition_config first. Instantiate it during the image build so
# import-order cycles cannot ship as a worker restart loop.
RUN cd /app/python && python -c 'from app.containers.query import QueryAppContainer; QueryAppContainer.init("agent_harness_worker_build_check")'

# Copy the process monitor script
COPY deployment/process_monitor.sh /app/process_monitor.sh

RUN chmod +x /app/process_monitor.sh && \
    install -d -o 1000 -g 1000 /app/python/logs /data/pipeshub && \
    chown 1000:1000 /app /app/backend

# Code stays root-owned (read-only to the app); only the dirs above, which the
# services create log and state files in, belong to the runtime user. The image
# starts as root so the entrypoint can take over data from earlier root-running
# releases; it drops to uid 1000 with no capabilities before any service starts.
COPY --chmod=755 deployment/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# A container logs to stdout and the platform keeps it (Compose's logging
# driver, the kubelet). Node's file logs are opt-in to a declared, writable
# NODE_LOG_DIR, so no uid needs a writable path inside the image to log.
ENV NODE_LOG_FILES=false

# The sandbox plane this image speaks: 2 is the broker behind the Docker API proxy's broker profile (R43-SOCK).
# rollback.sh reads it to warn before a rollback that would lose code execution; raise it with an incompatible change.
LABEL ai.pipeshub.sandbox-plane.version="2"

# What this image can run, for the installer and the chart to choose by (an image without the label predates them all
# and gets the upstream layout): the sandbox broker and egress proxy, SECRET_KEY read from a file, the agent harness.
# release_tooling_test.sh keeps install.sh's list equal to this one.
LABEL ai.pipeshub.capabilities="sandbox-broker,secret-key-file,agent-harness"

# Which build this image is; each service returns it from /health. Declared
# last so a new commit rebuilds only these layers. With no build args the
# variables are empty and the services report null.
ARG APP_VERSION=""
ARG GIT_COMMIT=""
ENV APP_VERSION=${APP_VERSION} \
    GIT_COMMIT=${GIT_COMMIT}
LABEL org.opencontainers.image.version=${APP_VERSION} \
      org.opencontainers.image.revision=${GIT_COMMIT}

EXPOSE 3000 8002 8092 8093

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["/app/process_monitor.sh"]
