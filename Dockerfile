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
# current pyproject.toml so packages added since the base was published (e.g. new
# connector SDKs like opensearch-py) end up in the app image. uv skips
# already-satisfied packages, so this is a fast no-op when the base is current and
# installs only the delta otherwise. The base carries uv + the build toolchain
# (it is built FROM build-base in Dockerfile.base), so native wheels can still
# compile here when a new dependency needs it.
WORKDIR /app/python
COPY backend/python/pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv pip install --system -e . && \
    crawl4ai-setup && \
    playwright install chromium


FROM ${RUNTIME_BASE_IMAGE} AS runtime-base

# Presentation previews are converted to PDF at request time. The published
# runtime base historically included Writer and Calc only, leaving the soffice
# wrapper present but unable to load PPT/PPTX files.
# Install CJK fallback fonts until they are available in the published runtime
# base image. LibreOffice uses these when documents reference unavailable fonts.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-impress-nogui fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# -----------------------------------------------------------------------------
# Stage 1: Node.js Backend Build
# -----------------------------------------------------------------------------
FROM node:20-slim AS nodejs-backend
WORKDIR /app/backend

COPY backend/nodejs/apps/package*.json ./
COPY backend/nodejs/apps/tsconfig.json ./

# Install dependencies with architecture handling (npm ci: lockfile-speed + reproducible)
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    set -e; \
    ARCH=$(uname -m); \
    echo "Building for architecture: $ARCH"; \
    if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then \
        echo "Detected ARM architecture"; \
        npm ci --ignore-scripts && \
        npm uninstall jpeg-recompress-bin mozjpeg imagemin-mozjpeg 2>/dev/null || true && \
        npm install sharp --save || true; \
    else \
        echo "Detected x86 architecture"; \
        npm ci; \
    fi

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
FROM node:20-slim AS frontend-build
WORKDIR /app/frontend

COPY frontend/package*.json ./

RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm config set legacy-peer-deps true && \
    npm ci

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
RUN groupadd --gid 1000 pipeshub && \
    useradd --uid 1000 --gid 1000 --home-dir /root --no-create-home \
        --shell /usr/sbin/nologin pipeshub && \
    install -d -o 1000 -g 1000 /root/.cache /root/.local && \
    chown 1000:1000 /root
ENV HOME=/root
# Pinned so the baked models are still found when HOME is overridden (the
# Helm chart sets HOME=/home/user).
ENV HF_HOME=/root/.cache/huggingface \
    NLTK_DATA=/root/nltk_data \
    PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright

# Point fastembed at the pre-populated cache we copy in below, matching the
# FASTEMBED_CACHE_PATH used at build time in the python-deps stage.
ENV FASTEMBED_CACHE_PATH=/root/.cache/fastembed

# Cap ML thread fan-out (PyTorch/OpenBLAS/MKL). runtime_threads.py propagates
# this to all libraries. Override via OMP_NUM_THREADS in .env if needed.
ENV OMP_NUM_THREADS=2

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
