# Engine sandbox image for DeepSeek Harness's ACP server (`dsh --profile acp`, ACP-driver, WP-105, C2.43): DeepSeek's
# unmodified npm release (@deepseek-ai/dsh, MIT) with the exact dependency tree of engine/package-lock.json (some 650
# packages, every one integrity-pinned in the lock; npm ci without install scripts), its entry point lib/bin.js pinned
# by digest, run by the sandbox broker (P5.1) as uid 1000 with a read-only root, no capabilities and no network but the
# egress proxy. Build with build.sh (engine-build.sh), which verifies the registry tarball and its bin.js against
# backend/python/harness_engines/allowlist.json and the lock against the entry's lockSha256, and passes the lock in as
# engine-lock/ and the entry point's digest in.
#
# Debian rather than Alpine: the tree's native addons (node-addon-require-builtin, sharp) are the glibc builds npm
# picks on the builder. One run target, tools (allowlist `variants`), as the native engines' run images (E0b): CPython
# 3.12 with opencode's hash-pinned data and office packages (tools/ is opencode's) beside Node, so the programs dsh
# writes run in its own shell, as Claude Code's Debian image does.
ARG DEBIAN=debian@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
# CPython 3.12.15 on Debian 12.15 (bookworm-slim), the base above's release.
ARG PYTHON_DEBIAN=python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG ALPINE=alpine@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
ARG VARIANT=engine
# Node 24 (dsh needs 24.9 or later): the official glibc build, the binary only; and npm for the builders. npm itself
# never reaches an image.
ARG NODE_DEBIAN=node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c

# The broker's connect(port) relay without python3 (P5.1-f), from the opencode image's source.
FROM ${ALPINE} AS relay
RUN apk add --no-cache gcc musl-dev
COPY relay/pxh-relay.c /src/pxh-relay.c
RUN gcc -static -Os -s -Wall -Wextra -Werror -o /pxh-relay /src/pxh-relay.c

# dsh and the lock's exact tree: integrity-checked by npm ci, no install scripts (the native addons are prebuilt).
FROM ${NODE_DEBIAN} AS dsh-tree
ARG ENGINE_SHA256
RUN --mount=type=bind,source=engine-lock,target=/build \
    mkdir -p /usr/local/lib/dsh && cp /build/package.json /build/package-lock.json /usr/local/lib/dsh/ \
    && cd /usr/local/lib/dsh \
    && npm ci --omit=dev --ignore-scripts --no-audit --no-fund --no-update-notifier \
    && echo "${ENGINE_SHA256}  /usr/local/lib/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js" | sha256sum -c - \
    && chown -R 0:0 /usr/local/lib/dsh \
    && chmod -R a-w,a+rX /usr/local/lib/dsh \
    && chmod 0555 /usr/local/lib/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js

FROM ${DEBIAN} AS base-engine
FROM ${PYTHON_DEBIAN} AS base-tools

FROM base-${VARIANT} AS engine
ARG ENGINE_VERSION
ARG ENGINE_SHA256
# bash: dsh's bash tool; git and rg: a repository workspace and the search tools. `rm` is coreutils': WorkspaceSync
# removes paths with `rm -rf --` (P5.5).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -d /home/engine -s /bin/bash engine \
    && mkdir -p /workspace /state /skills \
    && chown 1000:1000 /workspace /state
COPY --from=dsh-tree /usr/local/bin/node /usr/local/bin/node
COPY --from=dsh-tree /usr/local/LICENSE /usr/share/licenses/nodejs/LICENSE
# The driver launches bin.js (its shebang finds /usr/local/bin/node); the tree is root's and read-only (its modes set
# in the builder, so the copy is one layer).
COPY --from=dsh-tree /usr/local/lib/dsh/ /usr/local/lib/dsh/
RUN echo "${ENGINE_SHA256}  /usr/local/lib/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js" | sha256sum -c - \
    && test "$(stat -c %u:%a /usr/local/lib/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js)" = 0:555 \
    && node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 24 ? 0 : 1)'
COPY --from=relay --chmod=0555 /pxh-relay /usr/local/bin/pxh-relay
COPY licenses/ /usr/share/licenses/pxh-dsh/
LABEL io.pipeshub.engine.kind="acp-dsh" \
      io.pipeshub.engine.version="${ENGINE_VERSION}" \
      io.pipeshub.engine.binary-digest="sha256:${ENGINE_SHA256}"
ENV HOME=/tmp
USER 1000:1000
WORKDIR /workspace
CMD ["/bin/sh", "-c", "while :; do sleep 3600; done"]

# The office packages' lock: integrity-checked by npm ci, no install scripts.
FROM ${NODE_DEBIAN} AS node-packages
RUN --mount=type=bind,source=tools/node,target=/build \
    mkdir /out && cp /build/package.json /build/package-lock.json /out/ && cd /out \
    && npm ci --omit=dev --ignore-scripts --no-audit --no-fund --no-update-notifier \
    && rm package.json package-lock.json

# Wheels only, every one hash-pinned, no resolver: opencode's tools stages on Debian, as Claude Code's image (the
# manylinux libraries are not stripped: strip on auditwheel's patchelf-rewritten libraries misaligns their segments).
FROM base-tools AS wheels
RUN --mount=type=bind,source=tools,target=/build \
    python3 -m pip install --no-cache-dir --root-user-action=ignore --disable-pip-version-check \
        --require-hashes --only-binary=:all: --no-deps --no-compile --target /site -r /build/requirements.lock \
    && find /site -depth -type d -name tests -path "/site/*/*" -exec rm -rf {} + \
    && rm -f /site/reportlab/fonts/DarkGarden* /site/reportlab/fonts/*.pfb /site/reportlab/fonts/*.afm \
        /site/reportlab/fonts/hb-test.ttf \
    && rm -rf /site/bin

FROM engine AS tools
USER 0:0
COPY --from=wheels /site/ /usr/local/lib/python3.12/site-packages/
# An ES module finds a bare specifier only in a node_modules folder above it (NODE_PATH is for require), so
# /node_modules points at the global tree: `import ExcelJS from "exceljs"` works from /workspace, /tmp and /skills.
COPY --from=node-packages /out/node_modules/ /usr/local/lib/node_modules/
RUN ln -s /usr/local/lib/node_modules /node_modules
RUN --mount=type=bind,source=tools,target=/build \
    python3 -c 'import sys; assert sys.version_info[:2] == (3, 12), "build the tools target with --build-arg VARIANT=tools"' \
    && python3 -m pip uninstall -y -q --root-user-action=ignore pip \
    && python3 -m compileall -q -j 0 /usr/local/lib/python3.12/site-packages \
    && mkdir -p /usr/share/pxh-engine \
    && python3 /build/runtime_note.py /build/requirements.lock /build/node/package-lock.json > /usr/share/pxh-engine/RUNTIME.md \
    && python3 /build/runtime_note.py --check /build/requirements.lock /build/node/package-lock.json \
    && rm -rf /tmp/* /tmp/.[!.]*
COPY tools/licenses/ /usr/share/licenses/pxh-dsh-tools/
# R3 / OWD-6: `ph` and the static /pipeshub contract, installed by the package's own script.
RUN --mount=type=bind,source=pipeshub,target=/build-pipeshub \
    sh /build-pipeshub/install.sh \
    && /usr/local/bin/ph --version
LABEL io.pipeshub.engine.variant="tools"
ENV MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    PYTHONDONTWRITEBYTECODE=1
USER 1000:1000
