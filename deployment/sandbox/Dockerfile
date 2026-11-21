# Pinned by digest (SEC-SC-11); Dependabot moves the digest and the tag together.
FROM node:24-slim@sha256:dc26ae92c9fb15cf6b1d63e20f0944b2efe0f115b20e00be48fb23fceb061c54 AS node-runtime

FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

LABEL maintainer="pipeshub" \
      description="Unified sandbox for Python, TypeScript, SQLite, and PostgreSQL code execution"

# System deps: Cairo/Pango for SVG/PDF rendering, SQLite, PostgreSQL client, curl.
# iptables: containers on the egress bridge install their egress firewall at start
# (app/agent_loop_lib/sandbox/coding/egress_firewall.py), then drop to `sandbox`
# with no capabilities via setpriv (util-linux). Without iptables, networked runs
# fall back to no network.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        fonts-dejavu-core \
        sqlite3 \
        postgresql-client \
        curl \
        gnupg \
        tzdata \
        iptables \
    && rm -rf /var/lib/apt/lists/*

# Node.js 24 LTS matches the application runtime (22 is the minimum).
# Node.js comes from the digest-pinned official image, not a remote setup script piped to a shell (SEC-D-11).
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && \
    node --version

# Global Node tools, from a committed lock (integrity-checked by npm ci)
COPY tsx/package.json tsx/package-lock.json /opt/tsx/
RUN npm ci --prefix /opt/tsx --omit=dev --ignore-scripts --no-audit --no-fund && \
    ln -s /opt/tsx/node_modules/.bin/tsx /usr/local/bin/tsx && \
    npm cache clean --force

# Python data-science and document-generation packages (requirements.in), installed from the hashed
# lock: wheels only, every file checked against its sha256, nothing resolved at build time (SEC-SC-07).
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --root-user-action=ignore --disable-pip-version-check \
        --require-hashes --only-binary=:all: --no-deps -r /tmp/requirements.lock && \
    rm /tmp/requirements.lock

# Non-root user. The runtime also pins this user explicitly so an image built
# on this one cannot silently replace the image default with root.
RUN useradd --create-home --shell /bin/bash sandbox

# Directories the sandbox user needs to write into at runtime.
# /src   — container cwd; programs write output files here
# /output — $OUTPUT_DIR; alternate artifact output path
# /install — npm install --prefix target for TypeScript packages
# /deps, /node_modules — pip install --target and the packages a run reads
# The root is read-only at run time (SEC-A-04): each of these is the container's
# own anonymous volume, which starts as this directory and its owner.
RUN mkdir -p /src /output /install /deps /node_modules && \
    chown sandbox:sandbox /src /output /install /deps /node_modules && \
    chmod 750 /src /output /install /deps /node_modules
USER sandbox
WORKDIR /home/sandbox

# Pre-install Node packages in the sandbox user's home. The office-document
# creation libraries (pptxgenjs/docx/exceljs/pdfkit/pdf-lib) are deliberately
# pre-installed: the builtin skill packs steer agents to Node for document
# CREATION, and if only the Python equivalents are pre-installed the model
# takes the zero-friction path (python-pptx et al.) despite the guidance.
# Versions are locked so the execution environment cannot drift between builds.
COPY --chown=sandbox:sandbox package.json package-lock.json remove-unused-image-size.mjs ./
RUN npm ci --omit=dev --ignore-scripts && \
    node remove-unused-image-size.mjs && \
    rm package-lock.json remove-unused-image-size.mjs && \
    node -e "const ExcelJS=require('exceljs'); const PptxGenJS=require('pptxgenjs'); new ExcelJS.Workbook(); new PptxGenJS().addSlide().addText('sandbox dependency check',{x:1,y:1,w:4,h:1});" && \
    npm cache clean --force

WORKDIR /src
