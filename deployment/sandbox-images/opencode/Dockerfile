# Engine sandbox images: the unmodified opencode release binary, pinned by digest, run by the sandbox
# broker (P5.1) as uid 1000 with a read-only root, no capabilities and no network but the egress proxy.
# Build with build.sh (engine-build.sh), which downloads and verifies the release asset against
# backend/python/harness_engines/allowlist.json and passes the binary's digest in.
#
# Two targets share the engine stage (allowlist `variants`):
#   engine  the binary and its shell tooling on Alpine (VARIANT=engine)
#   tools   the same stage on CPython 3.12 for Alpine (VARIANT=tools), plus the wave 1 runtimes:
#           the hash-pinned data and office packages in tools/requirements.lock (E0b), and Node with the
#           office skill packs' docx and exceljs from tools/node/package-lock.json (SK-03)
ARG ALPINE=alpine@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
# CPython 3.12 on the same Alpine release (3.24); Alpine's own python3 is 3.14.
ARG PYTHON_ALPINE=python:3.12-alpine3.24@sha256:0687a6bc9716edc2a6ee0fbfb0f87e7ee358b262b67c9215de91bc9b2d38ba71
ARG VARIANT=engine
# npm for the builder that installs tools/node/package-lock.json (docx, exceljs: plain JavaScript, so one tree
# serves the Alpine and the Debian images). npm itself never reaches an image.
ARG NODE_DEBIAN=node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c

# The broker's connect(port) relay without python3 (P5.1-f): a static binary of a few kB.
FROM ${ALPINE} AS relay
RUN apk add --no-cache gcc musl-dev
COPY relay/pxh-relay.c /src/pxh-relay.c
RUN gcc -static -Os -s -Wall -Wextra -Werror -o /pxh-relay /src/pxh-relay.c

FROM ${ALPINE} AS base-engine
FROM ${PYTHON_ALPINE} AS base-tools

FROM base-${VARIANT} AS engine
ARG ENGINE_VERSION
ARG ENGINE_SHA256
# libstdc++/libgcc: the musl build of Bun links them. rg: opencode downloads it from GitHub when it is
# missing (F4 §1.4); git and bash: the engine's shell tool and repositories in a workspace.
# `rm` is busybox's: WorkspaceSync removes paths with `rm -rf --` (P5.5).
# The config dir is root-owned with its .gitignore in place: opencode then skips its background
# `npm install` into it, and a read-only root never sees a missing-file write (F4 §1.4).
RUN apk add --no-cache libstdc++ libgcc ripgrep git bash \
    && adduser -D -u 1000 -h /home/engine engine \
    && mkdir -p /workspace /state /skills /opt/oc-xdg-config/opencode \
    && chown 1000:1000 /workspace /state \
    && printf 'node_modules\npackage.json\nbun.lock\n.gitignore\n' > /opt/oc-xdg-config/opencode/.gitignore \
    && chmod -R a-w /opt/oc-xdg-config
COPY --chmod=0555 engine-binary /usr/local/bin/opencode
RUN echo "${ENGINE_SHA256}  /usr/local/bin/opencode" | sha256sum -c -
COPY --from=relay --chmod=0555 /pxh-relay /usr/local/bin/pxh-relay
COPY licenses/ /usr/share/licenses/pxh-opencode/
LABEL io.pipeshub.engine.kind="opencode" \
      io.pipeshub.engine.version="${ENGINE_VERSION}" \
      io.pipeshub.engine.binary-digest="sha256:${ENGINE_SHA256}"
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

# Wheels only, every one hash-pinned, no resolver: the image holds exactly the lock. Installed and
# stripped in a builder so binutils never reach the image; test suites inside packages are dropped
# (never imported), and so are the font files reportlab ships under the GPL or no licence of their own
# and never loads to write a PDF (components.json, reportlab).
FROM base-tools AS wheels
RUN --mount=type=bind,source=tools,target=/build \
    apk add --no-cache binutils \
    && python3 -m pip install --no-cache-dir --root-user-action=ignore --disable-pip-version-check \
        --require-hashes --only-binary=:all: --no-deps --no-compile --target /site -r /build/requirements.lock \
    && find /site -depth -type d -name tests -path "/site/*/*" -exec rm -rf {} + \
    && find /site \( -name '*.so' -o -name '*.so.*' \) -type f -exec strip --strip-unneeded {} + \
    && rm -f /site/reportlab/fonts/DarkGarden* /site/reportlab/fonts/*.pfb /site/reportlab/fonts/*.afm \
        /site/reportlab/fonts/hb-test.ttf \
    && rm -rf /site/bin

FROM engine AS tools
USER 0:0
COPY --from=wheels /site/ /usr/local/lib/python3.12/site-packages/
# Node for the office skill packs' docx and exceljs (SK-03); Alpine's own build, no npm.
RUN apk add --no-cache nodejs
# An ES module finds a bare specifier only in a node_modules folder above it (NODE_PATH is for require), so
# /node_modules points at the global tree: `import ExcelJS from "exceljs"` works from /workspace, /tmp and /skills.
COPY --from=node-packages /out/node_modules/ /usr/local/lib/node_modules/
RUN ln -s /usr/local/lib/node_modules /node_modules
# Bytecode is compiled here because the root is read-only at run time. pip goes: nothing can be
# installed in a sandbox (no egress beyond the gateways either), so the engine never repeats run_code's
# reinstall-into-/tmp failure (EV1 open item 4).
RUN --mount=type=bind,source=tools,target=/build \
    python3 -c 'import sys; assert sys.version_info[:2] == (3, 12), "build the tools target with --build-arg VARIANT=tools"' \
    && python3 -m pip uninstall -y -q --root-user-action=ignore pip \
    && python3 -m compileall -q -j 0 /usr/local/lib/python3.12/site-packages \
    && mkdir -p /usr/share/pxh-engine \
    && python3 /build/runtime_note.py /build/requirements.lock /build/node/package-lock.json > /usr/share/pxh-engine/RUNTIME.md \
    && python3 /build/runtime_note.py --check /build/requirements.lock /build/node/package-lock.json \
    && rm -rf /tmp/* /tmp/.[!.]*
COPY tools/licenses/ /usr/share/licenses/pxh-opencode-tools/
# R3 / OWD-6: `ph` and the static /pipeshub contract, installed by the package's own script
# (backend/python/harness_engines/pipeshub/install.sh). /pipeshub/session is a read-only mount the controller seeds
# at each turn from the run's grant (tool index and schemas, gateway, skills); nothing a run can call is baked in here.
RUN --mount=type=bind,source=pipeshub,target=/build-pipeshub \
    sh /build-pipeshub/install.sh \
    && /usr/local/bin/ph --version
LABEL io.pipeshub.engine.variant="tools"
# Agg renders charts without a display; matplotlib keeps its font cache in the sandbox's /tmp.
ENV MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    PYTHONDONTWRITEBYTECODE=1
USER 1000:1000
