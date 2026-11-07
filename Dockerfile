# syntax=docker/dockerfile:1
# App image. Slow layers live in Dockerfile.base and are pulled, not rebuilt:
#   PYTHON_DEPS_IMAGE   pipeshubai/pipeshub-ai-base:python-deps-<hash>
#   RUNTIME_BASE_IMAGE  pipeshubai/pipeshub-ai-base:runtime-<hash>
#   MODELS_IMAGE        pipeshubai/pipeshub-ai-base:models-bge-<hash>
#                       (only the full target; default builds that stage here)
#
# Targets:
#   runtime-slim   no embedding/reranker weights, Chromium downloaded on first use.
#                  About 1.5 GB compressed once the dependency layers settle.
#   runtime        slim plus baked Chromium and the BGE embedding + reranker models.
#                  Works without Hugging Face or the Playwright CDN.
#
# Local full build (downloads the model layer on first build):
#   docker build -t pipeshub-ai:latest .
# Slim:
#   docker build --target runtime-slim -t pipeshub-ai:slim .
ARG PYTHON_DEPS_IMAGE=pipeshubai/pipeshub-ai-base:python-deps
ARG RUNTIME_BASE_IMAGE=pipeshubai/pipeshub-ai-base:runtime
ARG MODELS_IMAGE=models-download
ARG NODE_IMAGE=node:22-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.22@sha256:9874eb7afe5ca16c363fe80b294fe700e460df29a55532bbfea234a0f12eddb1

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_DEPS_IMAGE} AS python-deps
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
# Reconcile the base with the lock checked out in this build. When the base was
# published from this lock the install changes nothing, so the site-packages
# layer copied below stays byte-identical across releases. REQUIRE_CURRENT_BASE=1
# (the release build) fails instead of silently installing a delta.
# Git sources have no hash, so they install separately; see Dockerfile.base.
WORKDIR /app/python
COPY --from=uv /uv /uvx /bin/
COPY backend/python/pyproject.toml backend/python/uv.lock ./
ARG REQUIRE_CURRENT_BASE=0
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    set -eux; \
    snap() { find /usr/local/lib/python3.12/site-packages -type f -printf '%P %s\n' | sort | sha256sum | awk '{print $1}'; }; \
    before="$(snap)"; \
    uv export --frozen --no-dev --no-editable --no-emit-project --no-header --quiet -o /tmp/requirements.txt; \
    grep -E '^[A-Za-z0-9_.-]+ @ git\+' /tmp/requirements.txt > /tmp/requirements-git.txt || true; \
    grep -vE '^[A-Za-z0-9_.-]+ @ git\+' /tmp/requirements.txt > /tmp/requirements-hashed.txt; \
    uv pip install --system --require-hashes -r /tmp/requirements-hashed.txt; \
    if [ -s /tmp/requirements-git.txt ]; then uv pip install --system --no-deps -r /tmp/requirements-git.txt; fi; \
    after="$(snap)"; \
    if [ "$REQUIRE_CURRENT_BASE" = "1" ] && [ "$before" != "$after" ]; then \
        echo "python-deps base does not match uv.lock; rebuild it from Dockerfile.base"; \
        exit 1; \
    fi

# Used only when MODELS_IMAGE is left at its default. Release builds pass the
# published models image and BuildKit skips this stage.
# Same weights as the models stage of Dockerfile.base.
FROM python-deps AS models-download
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-large-en-v1.5', device='cpu')" && \
    python -c "from sentence_transformers import CrossEncoder; CrossEncoder(model_name='BAAI/bge-reranker-base')" && \
    python -c "from huggingface_hub import hf_hub_download; hf_hub_download('docling-project/docling-layout-heron-onnx', 'model.onnx', revision='40bde044036bb181c130ddf6c51792187268748f')"

FROM ${MODELS_IMAGE} AS models

FROM ${NODE_IMAGE} AS node-bin

# Chromium for the full target. A base built from this Dockerfile.base already
# has the matching revision and both installs are no-ops; an older base gets it
# downloaded here. The drivers' node is a symlink to /usr/bin/node in a current
# base, so that binary is supplied for this stage only.
FROM python-deps AS browsers
COPY --from=node-bin /usr/local/bin/node /usr/bin/node
ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright \
    PATCHRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright
RUN python -m playwright install chromium && \
    python -m patchright install chromium

# -----------------------------------------------------------------------------
# Node.js backend. Dependencies and the TypeScript build are separate stages
# so a source change does not rebuild node_modules.
# -----------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS nodejs-backend
WORKDIR /app/backend
COPY backend/nodejs/apps/package.json backend/nodejs/apps/package-lock.json backend/nodejs/apps/tsconfig.json ./
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm ci
COPY backend/nodejs/apps/src ./src
RUN npm run build
# tsc does not emit .hbs, and emailTemplates.ts reads them from ${process.cwd()}/src
# at runtime, so every template under src is staged here with its path kept.
WORKDIR /app/backend/src
RUN mkdir -p /app/templates && find . -name '*.hbs' -exec cp --parents {} /app/templates/ \;

FROM ${NODE_IMAGE} AS nodejs-prod-deps
WORKDIR /app/backend
COPY backend/nodejs/apps/package.json backend/nodejs/apps/package-lock.json ./
# Type declarations, source maps, docs and tests are not loaded at runtime.
# License files stay. bun is a dependency of @pipeshub-ai/mcp that this server
# never executes (it imports the package's ESM); the platform-variant binaries
# are most of node_modules, so they are removed here until that package stops
# declaring bun as a runtime dependency.
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm ci --omit=dev && \
    find node_modules -type d \( -name test -o -name tests -o -name __tests__ -o -name __mocks__ \) -prune -exec rm -rf {} + && \
    find node_modules -type f \( -name '*.d.ts' -o -name '*.d.mts' -o -name '*.d.cts' -o -name '*.map' -o -name '*.md' \) ! -iname 'LICENSE*' -delete && \
    rm -rf node_modules/bun node_modules/@oven node_modules/@img/sharp-wasm32

# -----------------------------------------------------------------------------
# Frontend. Static export so the Node.js API can serve files from backend/dist/public.
# -----------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
# The web export never runs Electron; its postinstall downloads a ~100MB binary
# from GitHub, which stalls and fails the whole build on slow/emulated networks.
ENV ELECTRON_SKIP_BINARY_DOWNLOAD=1
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm config set legacy-peer-deps true && \
    npm ci
COPY frontend/ ./
RUN --mount=type=cache,target=/app/frontend/.next/cache,sharing=locked \
    ELECTRON_STATIC=1 npm run build && \
    mkdir -p /out && \
    cp -a out/. /out/

# -----------------------------------------------------------------------------
# Slim runtime. Chromium is not in the image; ensure_chromium() downloads it
# into /data/pipeshub on first use. Embedding and reranker weights are not
# baked either.
# -----------------------------------------------------------------------------
FROM ${RUNTIME_BASE_IMAGE} AS runtime-slim
WORKDIR /app

# uid/gid 1000 matches the Helm chart's runAsUser. Home stays /root so local
# storage file:// URLs and existing volume mounts keep working.
# docker-entrypoint.sh hands root-owned data from earlier releases over to it.
RUN groupadd --gid 1000 pipeshub && \
    useradd --uid 1000 --gid 1000 --home-dir /root --no-create-home \
        --shell /usr/sbin/nologin pipeshub && \
    install -d -o 1000 -g 1000 /root/.cache /root/.local /app/python/logs /data/pipeshub /data/pipeshub/ms-playwright && \
    chown 1000:1000 /root
ENV HOME=/root \
    HF_HOME=/root/.cache/huggingface \
    NLTK_DATA=/root/nltk_data \
    PLAYWRIGHT_BROWSERS_PATH=/data/pipeshub/ms-playwright \
    PATCHRIGHT_BROWSERS_PATH=/data/pipeshub/ms-playwright \
    FASTEMBED_CACHE_PATH=/root/.cache/fastembed \
    OMP_NUM_THREADS=2 \
    PYTHONWARNINGS="ignore::SyntaxWarning"

COPY --link --from=python-deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --link --from=python-deps /usr/local/bin /usr/local/bin
COPY --link --chown=1000:1000 --from=python-deps /root/.cache/fastembed /root/.cache/fastembed
COPY --link --chown=1000:1000 --from=python-deps /root/nltk_data /root/nltk_data

COPY --link --from=nodejs-backend /app/backend/dist ./backend/dist
COPY --link --from=nodejs-backend /app/templates ./backend/src
COPY --link --from=nodejs-backend /app/backend/src/modules/api-docs/pipeshub-openapi.yaml ./backend/src/modules/api-docs/pipeshub-openapi.yaml
COPY --link --from=nodejs-prod-deps /app/backend/node_modules ./backend/dist/node_modules
COPY --link --from=frontend-build /out ./backend/dist/public
COPY --link backend/python/app/ /app/python/app/
COPY --link --chmod=755 deployment/process_monitor.sh /app/process_monitor.sh
COPY --link --chmod=755 deployment/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# process_monitor.sh writes /app/process_monitor.log and the Node server writes
# under /app/backend. Each --link copy recreates its parent directories as root,
# so ownership is set after the copies, without recursing into them.
RUN chown 1000:1000 /app /app/backend

# Which build this image is; each service returns it from /health. Declared
# last so a new commit rebuilds only these layers. With no build args the
# variables are empty and the services report null.
ARG APP_VERSION=""
ARG GIT_COMMIT=""
ARG BASE_HASH=""
ARG NPM_LOCK_HASH=""
ENV APP_VERSION=${APP_VERSION} \
    GIT_COMMIT=${GIT_COMMIT}
LABEL org.opencontainers.image.version=${APP_VERSION} \
      org.opencontainers.image.revision=${GIT_COMMIT} \
      org.pipeshub.base-hash=${BASE_HASH} \
      org.pipeshub.npm-lock-hash=${NPM_LOCK_HASH}

EXPOSE 3000 8002 8092 8093
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["/app/process_monitor.sh"]

# -----------------------------------------------------------------------------
# Full runtime: slim plus baked Chromium and the embedding/reranker models.
# -----------------------------------------------------------------------------
FROM runtime-slim AS runtime
ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright \
    PATCHRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright
COPY --link --chown=1000:1000 --from=browsers /root/.cache/ms-playwright /root/.cache/ms-playwright
COPY --link --chown=1000:1000 --from=models /root/.cache/huggingface /root/.cache/huggingface
