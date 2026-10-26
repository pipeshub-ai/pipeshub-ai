# syntax=docker/dockerfile:1
# Code-execution sandbox. Python and Node are pinned by digest; Python
# packages come from requirements.txt; npm packages are pinned below.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
ARG NODE_IMAGE=node:22-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.22@sha256:9874eb7afe5ca16c363fe80b294fe700e460df29a55532bbfea234a0f12eddb1

FROM ${UV_IMAGE} AS uv
FROM ${NODE_IMAGE} AS node-bins

FROM ${PYTHON_IMAGE}
LABEL maintainer="pipeshub" \
      description="Unified sandbox for Python, TypeScript, SQLite, and PostgreSQL code execution"
ENV DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# iptables: containers on the egress bridge install their firewall at start,
# then drop to `sandbox` via setpriv. Without iptables, networked runs fall
# back to no network.
RUN rm -f /etc/apt/apt.conf.d/docker-clean
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        fonts-dejavu-core \
        sqlite3 \
        postgresql-client \
        curl \
        tzdata \
        iptables \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node-bins /usr/local/bin/node /usr/local/bin/node
COPY --from=node-bins /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && \
    ln -sf /usr/local/bin/node /usr/bin/node

COPY --from=uv /uv /uvx /bin/
COPY requirements.txt /tmp/requirements.txt
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv pip install --system -r /tmp/requirements.txt && \
    rm -rf /tmp/*

RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm install -g tsx@4.23.15 && npm cache clean --force

RUN mkdir -p /src /output /install && chmod 777 /src /output /install
RUN useradd --create-home --shell /bin/bash sandbox
WORKDIR /home/sandbox
# Installed as root, then handed to sandbox. A cache mount is not writable
# by a non-root USER, and the packages only need to be readable at runtime.
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm init -y && \
    npm install --save \
        fs-extra@11.4.1 \
        sharp@0.35.5 \
        @types/node@22.20.5 \
        csv-stringify@6.9.0 \
        json2csv@5.0.7 \
        papaparse@5.7.0 \
        pptxgenjs@4.0.1 \
        docx@9.11.0 \
        exceljs@4.4.0 \
        pdfkit@0.20.2 \
        pdf-lib@1.17.1 \
    && npm cache clean --force \
    && chown -R sandbox:sandbox /home/sandbox
USER sandbox
WORKDIR /src
