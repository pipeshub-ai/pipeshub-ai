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

# Non-root user
RUN useradd --create-home --shell /bin/bash sandbox
USER sandbox

# Pre-install Node packages in the sandbox user's home
WORKDIR /home/sandbox
RUN npm init -y 2>/dev/null && \
    npm install --save \
        fs-extra \
        sharp \
        @types/node \
        csv-stringify \
        json2csv \
    && npm cache clean --force

WORKDIR /src
