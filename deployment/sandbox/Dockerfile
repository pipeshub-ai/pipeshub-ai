FROM python:3.12-slim

LABEL maintainer="pipeshub" \
      description="Unified sandbox for Python, TypeScript, SQLite, and PostgreSQL code execution"

# System dependencies:
#   - Cairo/Pango/DejaVu  — SVG/PDF rendering
#   - SQLite / PostgreSQL — database execution paths
#   - LibreOffice (core + impress + writer) + poppler-utils — visual QA
#     loop: convert .pptx / .docx to PDF, then split into per-page JPEGs.
#     Adds ~400 MB but is gated by the SANDBOX_VISUAL_QA env flag on the
#     agent side, so operators who are tight on disk can skip the feature.
#   - fonts-liberation / fonts-noto-core — readable, Office-compatible
#     fonts for LibreOffice rendering
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        fonts-dejavu-core \
        fonts-liberation \
        fonts-noto-core \
        sqlite3 \
        postgresql-client \
        curl \
        gnupg \
        libreoffice-core \
        libreoffice-impress \
        libreoffice-writer \
        libreoffice-calc \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Node.js 20 via nodesource
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# Global Node tools
RUN npm install -g tsx && npm cache clean --force

# Install uv for fast Python package management
RUN pip install --no-cache-dir uv

# Python data-science and document-generation packages
RUN uv pip install --system --no-cache \
    pandas \
    matplotlib \
    seaborn \
    plotly \
    kaleido \
    openpyxl \
    python-docx \
    python-pptx \
    Pillow \
    reportlab \
    fpdf2 \
    cairosvg \
    numpy \
    scipy \
    tabulate \
    xlsxwriter \
    jinja2

# In-repo helper libraries (pipeshub-slides, pipeshub-docs) shipped to the
# image as a known filesystem location. They will be symlinked into the
# sandbox user's node_modules below so agent code can `import
# "pipeshub-slides"` without network access.
COPY lib/pipeshub-slides /usr/local/lib/pipeshub/pipeshub-slides
COPY lib/pipeshub-docs   /usr/local/lib/pipeshub/pipeshub-docs

# The helper libs are distributed as TypeScript source + compiled `dist/`
# output. In case the copied source lacks an up-to-date `dist/` (e.g. a
# developer forgot to `npm run build` before building the image), compile
# it here so `import "pipeshub-slides"` at runtime always resolves to
# plain `.js` — Node's native ESM loader doesn't run `.ts` files and
# `tsx` refuses to transform node_modules on recent Node versions. We
# remove dev dependencies right after to keep image size down.
RUN for lib in /usr/local/lib/pipeshub/pipeshub-slides /usr/local/lib/pipeshub/pipeshub-docs; do \
        if [ ! -f "$lib/dist/index.js" ]; then \
            (cd "$lib" && npm install --no-audit --no-fund --loglevel=error && \
             npm run build && rm -rf node_modules); \
        fi; \
    done

# Non-root user
RUN useradd --create-home --shell /bin/bash sandbox
USER sandbox

# Pre-install Node packages in the sandbox user's home. Batched into a
# single install to keep the image layer compact.
#
# Document-generation stack:
#   - pptxgenjs    — rich .pptx from TypeScript
#   - docx         — rich .docx from TypeScript (docx-js)
#   - react, react-dom, react-icons — rasterise icons into slides/docs
#   - sharp        — image processing (already needed for chart compositing)
#
# Plus the long-standing utilities (fs-extra, csv-stringify, json2csv).
WORKDIR /home/sandbox
RUN npm init -y 2>/dev/null && \
    npm install --save \
        fs-extra \
        sharp \
        @types/node \
        csv-stringify \
        json2csv \
        pptxgenjs \
        docx \
        react \
        react-dom \
        react-icons \
    && npm cache clean --force

# Make the pre-shipped helper libraries resolvable as regular npm modules by
# symlinking them into the sandbox user's node_modules tree. Kept for
# backwards compatibility with any CJS code that lands here.
RUN ln -s /usr/local/lib/pipeshub/pipeshub-slides /home/sandbox/node_modules/pipeshub-slides && \
    ln -s /usr/local/lib/pipeshub/pipeshub-docs   /home/sandbox/node_modules/pipeshub-docs

# --------------------------------------------------------------------------
# ESM resolution path
# --------------------------------------------------------------------------
# Agent scripts run as `.mts` (ESM) so top-level `await` works. Node's ESM
# resolver walks only filesystem ancestors of the script looking for
# `node_modules/<name>`; from `/src/main.mts` the ancestors are
# `/src/node_modules/` and `/node_modules/` — **NOT** the user's HOME.
# So we also expose the helper libraries under `/node_modules/` (which is
# writable at build time and preserved at runtime). User-requested packages
# are extracted here too at run-time (see DockerExecutor._install_dependencies),
# so we also ensure the directory is world-writable for `put_archive` and for
# non-root sandbox users who may need to unpack into it.
USER root
RUN mkdir -p /node_modules && \
    chmod 1777 /node_modules && \
    ln -s /usr/local/lib/pipeshub/pipeshub-slides /node_modules/pipeshub-slides && \
    ln -s /usr/local/lib/pipeshub/pipeshub-docs   /node_modules/pipeshub-docs
USER sandbox

WORKDIR /src
