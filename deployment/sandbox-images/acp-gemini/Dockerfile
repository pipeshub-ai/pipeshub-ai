# Engine sandbox image for Gemini CLI's ACP mode (WP-13): Google's unmodified release bundle
# (gemini-cli-bundle.zip, Node), its entry point pinned by digest, run by the sandbox broker (P5.1) as uid 1000
# with a read-only root, no capabilities and no network but the egress proxy. Build with build.sh
# (engine-build.sh), which downloads and verifies the bundle against backend/python/harness_engines/allowlist.json
# and passes the entry point's digest in.
#
# One target, tools (allowlist `variants`), as the native engines' run images (E0b): CPython 3.12 for Alpine with
# opencode's hash-pinned data and office packages (tools/ is opencode's) beside Node, so the programs Gemini CLI
# writes run in its own shell.
ARG ALPINE=alpine@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
# CPython 3.12 on the same Alpine release (3.24); Alpine's own python3 is 3.14.
ARG PYTHON_ALPINE=python:3.12-alpine3.24@sha256:0687a6bc9716edc2a6ee0fbfb0f87e7ee358b262b67c9215de91bc9b2d38ba71
ARG VARIANT=tools
# npm for the builder that installs tools/node/package-lock.json (docx, exceljs: plain JavaScript, so one tree
# serves the Alpine and the Debian images). npm itself never reaches an image.
ARG NODE_DEBIAN=node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c

# The broker's connect(port) relay without python3 (P5.1-f), from the opencode image's source.
FROM ${ALPINE} AS relay
RUN apk add --no-cache gcc musl-dev
COPY relay/pxh-relay.c /src/pxh-relay.c
RUN gcc -static -Os -s -Wall -Wextra -Werror -o /pxh-relay /src/pxh-relay.c

FROM ${PYTHON_ALPINE} AS engine
ARG ENGINE_VERSION
ARG ENGINE_SHA256
# nodejs: the bundle's runtime (it needs Node 20 or later). rg: Gemini CLI's search tool uses a system ripgrep
# only from a trusted system path, else falls back to a slower one. git and bash: the engine's shell tool and
# repositories in a workspace. `rm` is busybox's: WorkspaceSync removes paths with `rm -rf --` (P5.5).
RUN apk add --no-cache nodejs ripgrep git bash \
    && node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 20 ? 0 : 1)' \
    && adduser -D -u 1000 -h /home/engine engine \
    && mkdir -p /workspace /state /skills \
    && chown 1000:1000 /workspace /state
COPY engine-tree/ /usr/local/lib/gemini-cli/
RUN echo "${ENGINE_SHA256}  /usr/local/lib/gemini-cli/gemini.js" | sha256sum -c - \
    && chown -R 0:0 /usr/local/lib/gemini-cli \
    && chmod -R a-w,a+rX /usr/local/lib/gemini-cli \
    && chmod 0555 /usr/local/lib/gemini-cli/gemini.js \
    && ln -s /usr/local/lib/gemini-cli/gemini.js /usr/local/bin/gemini
# The system settings and admin policy: the floor no run and no repository changes. Gemini CLI reads system settings
# only where every folder up to / is root's and not writable by others, and merges them over user and workspace
# settings; the workspace is trusted (GEMINI_CLI_TRUST_WORKSPACE) because Gemini CLI starts no MCP server in an
# untrusted folder and the tool gateway is one, so a repository's .gemini/settings.json and .gemini/policies must
# lose to these. The run's own settings are rendered into ~/.gemini/settings.json (harness_engines/acp_agents.json).
COPY --chmod=0444 etc/settings.json /etc/gemini-cli/settings.json
COPY --chmod=0444 etc/policies/ /etc/gemini-cli/policies/
RUN chmod 0755 /etc/gemini-cli /etc/gemini-cli/policies
COPY --from=relay --chmod=0555 /pxh-relay /usr/local/bin/pxh-relay
COPY licenses/ /usr/share/licenses/pxh-gemini/
LABEL io.pipeshub.engine.kind="acp-gemini" \
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

# Wheels only, every one hash-pinned, no resolver: the image holds exactly the lock (as opencode's tools target).
FROM ${PYTHON_ALPINE} AS wheels
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
# An ES module finds a bare specifier only in a node_modules folder above it (NODE_PATH is for require), so
# /node_modules points at the global tree: `import ExcelJS from "exceljs"` works from /workspace, /tmp and /skills.
COPY --from=node-packages /out/node_modules/ /usr/local/lib/node_modules/
RUN ln -s /usr/local/lib/node_modules /node_modules
# Bytecode is compiled here because the root is read-only at run time; pip goes: nothing can be installed in a
# sandbox.
RUN --mount=type=bind,source=tools,target=/build \
    python3 -m pip uninstall -y -q --root-user-action=ignore pip \
    && python3 -m compileall -q -j 0 /usr/local/lib/python3.12/site-packages \
    && mkdir -p /usr/share/pxh-engine \
    && python3 /build/runtime_note.py /build/requirements.lock /build/node/package-lock.json > /usr/share/pxh-engine/RUNTIME.md \
    && python3 /build/runtime_note.py --check /build/requirements.lock /build/node/package-lock.json \
    && rm -rf /tmp/* /tmp/.[!.]*
COPY tools/licenses/ /usr/share/licenses/pxh-gemini-tools/
# R3 / OWD-6: `ph` and the static /pipeshub contract, installed by the package's own script.
RUN --mount=type=bind,source=pipeshub,target=/build-pipeshub \
    sh /build-pipeshub/install.sh \
    && /usr/local/bin/ph --version
LABEL io.pipeshub.engine.variant="tools"
ENV MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    PYTHONDONTWRITEBYTECODE=1
USER 1000:1000
