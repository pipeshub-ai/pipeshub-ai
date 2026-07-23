FROM python:3.12-slim

LABEL maintainer="pipeshub" \
      description="Unified sandbox for Python, TypeScript, SQLite, and PostgreSQL code execution"

# System deps: Cairo/Pango for SVG/PDF rendering, SQLite, PostgreSQL client, curl for Node setup
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

# Directories the sandbox user needs to write into at runtime.
# /src   — container cwd; programs write output files here
# /output — $OUTPUT_DIR; alternate artifact output path
# /install — npm install --prefix target for TypeScript packages
RUN mkdir -p /src /output /install && chmod 777 /src /output /install

# Non-root user
RUN useradd --create-home --shell /bin/bash sandbox
USER sandbox
WORKDIR /home/sandbox

# Pre-install Node packages in the sandbox user's home. The office-document
# creation libraries (pptxgenjs/docx/exceljs/pdfkit/pdf-lib) are deliberately
# pre-installed: the builtin skill packs steer agents to Node for document
# CREATION, and if only the Python equivalents are pre-installed the model
# takes the zero-friction path (python-pptx et al.) despite the guidance.
RUN npm init -y && \
    npm install --save \
        fs-extra \
        sharp \
        @types/node \
        csv-stringify \
        json2csv \
        papaparse \
        pptxgenjs \
        docx \
        exceljs \
        pdfkit \
        pdf-lib \
    && npm cache clean --force

WORKDIR /src
