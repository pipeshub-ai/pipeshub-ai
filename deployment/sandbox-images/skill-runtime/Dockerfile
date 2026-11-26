# Test variant only (SK1), not a release image: the pinned engine images plus what third-party skill
# packs (Anthropic's pdf and pptx skills) need to run offline. Build with build.sh, which takes the base
# images from backend/python/harness_engines/allowlist.json and checks the Codex binary's digest first.
#
#   opencode  the release opencode tools image (E0b) + skills.lock
#   codex     the same layers + the pinned Codex app-server binary, labelled as the Codex engine; the
#             opencode binary stays in the image unused (one shared runtime layer instead of two)
ARG PYTHON_ALPINE=python:3.12-alpine3.24@sha256:0687a6bc9716edc2a6ee0fbfb0f87e7ee358b262b67c9215de91bc9b2d38ba71
ARG TOOLS_IMAGE
ARG CODEX_IMAGE

FROM ${PYTHON_ALPINE} AS wheels
RUN --mount=type=bind,source=.,target=/build \
    python3 -m pip install --no-cache-dir --root-user-action=ignore --disable-pip-version-check \
        --require-hashes --only-binary=:all: --no-deps --no-compile --target /site -r /build/skills.lock \
    && rm -rf /site/bin

FROM ${CODEX_IMAGE} AS codex-binary

FROM ${TOOLS_IMAGE} AS opencode
USER 0:0
COPY --from=wheels /site/ /usr/local/lib/python3.12/site-packages/
# The note and the check read both locks: the model is told exactly what is installed, and nothing else is.
RUN --mount=type=bind,source=.,target=/build \
    cat /build/skills.lock /build/requirements.lock > /tmp/all.lock \
    && python3 -m compileall -q -j 0 /usr/local/lib/python3.12/site-packages \
    && python3 /build/runtime_note.py /tmp/all.lock /build/node-package-lock.json > /usr/share/pxh-engine/RUNTIME.md \
    && python3 /build/runtime_note.py --check /tmp/all.lock /build/node-package-lock.json \
    && rm -rf /tmp/* /tmp/.[!.]*
COPY licenses/ /usr/share/licenses/pxh-skill-runtime/
LABEL io.pipeshub.engine.variant="skill-runtime-test"
USER 1000:1000

FROM opencode AS codex
ARG CODEX_VERSION
ARG CODEX_SHA256
USER 0:0
COPY --from=codex-binary /usr/local/bin/codex-app-server /usr/local/bin/codex-app-server
COPY --from=codex-binary /usr/share/licenses/pxh-codex/ /usr/share/licenses/pxh-codex/
# Codex runs every command with `bash -lc` as the user's login shell (E8's image sets it the same way).
RUN echo "${CODEX_SHA256}  /usr/local/bin/codex-app-server" | sha256sum -c - \
    && sed -i 's#^\(engine:.*\):/bin/sh$#\1:/bin/bash#' /etc/passwd \
    && grep -q '^engine:.*:/bin/bash$' /etc/passwd
LABEL io.pipeshub.engine.kind="codex" \
      io.pipeshub.engine.version="${CODEX_VERSION}" \
      io.pipeshub.engine.binary-digest="sha256:${CODEX_SHA256}"
USER 1000:1000
