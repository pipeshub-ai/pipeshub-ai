# Engine sandbox image for Claude Code, built by the customer's admin from their own copy of the binary.
#
# Claude Code is proprietary (Anthropic Commercial Terms): PipesHub never ships it, in this repository, an
# image or a package (D29). This Dockerfile holds only PipesHub's layers; build.sh (engine-build.sh with
# --binary) verifies the admin's binary against the digest pinned in
# backend/python/harness_engines/allowlist.json and passes it in as `engine-binary`. The binary is copied
# unmodified; the sandbox broker (P5.1) runs it as uid 1000 with a read-only root, no capabilities and no
# network but the egress proxy. The resulting image stays in the admin's registry.
#
# Two targets share the engine stage (allowlist `variants`), as opencode's and Codex's do (E0b, EMPTYOUT):
#   engine  the binary and its shell tooling on Debian (VARIANT=engine)
#   tools   the same stage on CPython 3.12 for the same Debian release (VARIANT=tools), plus opencode's wave 1
#           runtimes (tools/ is opencode's): Claude Code runs the programs it writes in its own Bash tool, so a
#           run image without Python leaves a `python3 x.py > out.txt` deliverable empty (CCPY). The lock's
#           musllinux wheels do not load on glibc; each such pin also carries its manylinux wheel's hash.
ARG DEBIAN=debian@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
# CPython 3.12.15 on Debian 12.15 (bookworm-slim): the release and patch level of the base above and of the
# Alpine tools images' CPython. Debian's own python3 is 3.11.
ARG PYTHON_DEBIAN=python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG ALPINE=alpine@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
ARG VARIANT=engine
# npm for the builder that installs tools/node/package-lock.json (docx, exceljs: plain JavaScript, so one tree
# serves the Alpine and the Debian images). npm itself never reaches an image.
ARG NODE_DEBIAN=node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c

# The broker's connect(port) relay without python3 (P5.1-f), from the opencode image's source.
FROM ${ALPINE} AS relay
RUN apk add --no-cache gcc musl-dev
COPY relay/pxh-relay.c /src/pxh-relay.c
RUN gcc -static -Os -s -Wall -Wextra -Werror -o /pxh-relay /src/pxh-relay.c

FROM ${DEBIAN} AS base-engine
FROM ${PYTHON_DEBIAN} AS base-tools

FROM base-${VARIANT} AS engine
ARG ENGINE_VERSION
ARG ENGINE_SHA256
# The binary is glibc-linked (Debian rather than Alpine). bash: Claude Code's Bash tool; git and rg: the
# engine's work in a repository workspace and its Grep tool. `rm` is coreutils': WorkspaceSync removes paths
# with `rm -rf --` (P5.5).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -d /home/engine -s /bin/bash engine \
    && mkdir -p /workspace /state /skills \
    && chown 1000:1000 /workspace /state
COPY --chmod=0555 engine-binary /usr/local/bin/claude
RUN echo "${ENGINE_SHA256}  /usr/local/bin/claude" | sha256sum -c -
COPY --from=relay --chmod=0555 /pxh-relay /usr/local/bin/pxh-relay
COPY licenses/ /usr/share/licenses/pxh-claude-code/
LABEL io.pipeshub.engine.kind="claude_code" \
      io.pipeshub.engine.version="${ENGINE_VERSION}" \
      io.pipeshub.engine.binary-digest="sha256:${ENGINE_SHA256}" \
      io.pipeshub.engine.supply="admin"
ENV HOME=/tmp
USER 1000:1000
WORKDIR /workspace
CMD ["/bin/sh", "-c", "while :; do sleep 3600; done"]

# The lock's exact tree: integrity-checked by npm ci, no install scripts.
FROM ${NODE_DEBIAN} AS node-packages
RUN --mount=type=bind,source=tools/node,target=/build \
    mkdir /out && cp /build/package.json /build/package-lock.json /out/ && cd /out \
    && npm ci --omit=dev --ignore-scripts --no-audit --no-fund --no-update-notifier \
    && rm package.json package-lock.json

# Wheels only, every one hash-pinned, no resolver: opencode's tools stages on Debian. Unlike the Alpine
# images, the libraries are not stripped: strip on auditwheel's patchelf-rewritten manylinux libraries leaves
# ELF segments misaligned (numpy's OpenBLAS then fails to load: "not page-aligned").
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
# Node for the office skill packs' docx and exceljs (SK-03): the official glibc build of the same release as
# the Alpine images' (Debian's own nodejs is 18), the binary only; it links only glibc and libstdc++.
COPY --from=node-packages /usr/local/bin/node /usr/local/bin/node
COPY --from=node-packages /usr/local/LICENSE /usr/share/licenses/nodejs/LICENSE
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
COPY tools/licenses/ /usr/share/licenses/pxh-claude-code-tools/
# R3 / OWD-6: `ph` and the static /pipeshub contract, installed by the package's own script
# (backend/python/harness_engines/pipeshub/install.sh). /pipeshub/session is a read-only mount the controller seeds
# at each turn from the run's grant (tool index and schemas, gateway, skills); nothing a run can call is baked in here.
RUN --mount=type=bind,source=pipeshub,target=/build-pipeshub \
    sh /build-pipeshub/install.sh \
    && /usr/local/bin/ph --version
LABEL io.pipeshub.engine.variant="tools"
ENV MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    PYTHONDONTWRITEBYTECODE=1
USER 1000:1000
