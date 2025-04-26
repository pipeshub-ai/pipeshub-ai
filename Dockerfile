FROM python:3.10

WORKDIR /app

ENV DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC

# Install Node.js and necessary runtime libraries
RUN apt-get update && apt-get install -y \
    curl gnupg iputils-ping telnet traceroute dnsutils net-tools wget \
    librocksdb-dev libgflags-dev libsnappy-dev zlib1g-dev \
    libbz2-dev liblz4-dev libzstd-dev libssl-dev ca-certificates libspatialindex-dev libpq5 && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    apt-get install -y libreoffice && \
    apt-get install -y ocrmypdf tesseract-ocr ghostscript unpaper qpdf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set up Python environment
COPY ./services/python/pyproject.toml /app/python/
WORKDIR /app/python
RUN pip3 install -e .

# Download NLTK and spaCy models
RUN python3 -m nltk.downloader punkt && \
    python3 -m spacy download en_core_web_sm

RUN python -c "from langchain_huggingface import HuggingFaceEmbeddings; \
    model = HuggingFaceEmbeddings(model_name='BAAI/bge-large-en-v1.5')"

RUN python -c "from langchain_qdrant import FastEmbedSparse; \
    model = FastEmbedSparse(model_name='Qdrant/BM25')"

RUN python -c "from sentence_transformers import CrossEncoder; \
    model = CrossEncoder(model_name='BAAI/bge-reranker-base')"

WORKDIR /app

# Copy Python app files
COPY services/python/app/ /app/python/app/

# Set up Node.js backend
WORKDIR /app/backend
COPY services/nodejs/apps/package*.json ./
COPY services/nodejs/apps/tsconfig.json ./

# Set up architecture detection and conditional handling
RUN set -e; \
    # Detect architecture
    ARCH=$(uname -m); \
    echo "Building for architecture: $ARCH"; \
    # Platform-specific handling
    if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then \
        echo "Detected ARM architecture (M1/Apple Silicon)"; \
        # ARM-specific handling: Skip problematic binary or use alternative
        npm install --prefix ./ --ignore-scripts && \
        npm uninstall jpeg-recompress-bin --prefix ./ || true && \
        npm install imagemin-mozjpeg --prefix ./ --save; \
    else \
        echo "Detected x86 architecture"; \
        # Standard install for x86 platforms
        apt-get update && apt-get install -y libc6-dev-i386 && npm install --prefix ./; \
    fi

# Copy backend source files
COPY services/nodejs/apps/src ./src
RUN npm run build

# Set up frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm config set legacy-peer-deps true && npm install

# Copy frontend source files
COPY frontend/ ./
RUN npm run build && \
    cp -r dist/ /app/backend/dist/public/

# Expose necessary ports
EXPOSE 3000 8000 8088 8091

# Set working directory for final entrypoint
WORKDIR /app

# Start all services in development mode
CMD sh -c "cd /app/backend && node dist/index.js & \
           cd /app/python && python -m app.connectors_main & \
           cd /app/python && python -m app.indexing_main & \
           cd /app/python && python -m app.query_main & \
           wait"
