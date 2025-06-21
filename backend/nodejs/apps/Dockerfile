# Multi-stage Dockerfile for all services

# Stage 1: Build
FROM node:20 AS builder

# Set working directory for the build
WORKDIR /build

RUN apt-get update && apt-get install -y iputils-ping telnet traceroute dnsutils net-tools curl wget

# Install dependencies and build for each service
# Prepare and build storage-backend
COPY package*.json ./
COPY tsconfig.json ./
# Set up architecture detection and conditional handling
RUN set -e; \
    # Detect architecture
    ARCH=$(uname -m); \
    echo "Building for architecture: $ARCH"; \
    # Common configuration for all platforms
    npm config set legacy-peer-deps true; \
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
        apt-get install -y libc6-dev-i386; npm install --prefix ./; \
    fi
COPY src ./src
RUN npm run build --prefix ./

# Stage 2: Runtime
FROM node:20

# Install essential networking tools and SSH server
RUN apt-get update && apt-get install -y \
    iputils-ping telnet traceroute dnsutils net-tools curl wget \
    openssh-server && npm install -g pm2

# Set working directory for the runtime
WORKDIR /app

# Copy built services and dependencies from the builder stage
COPY --from=builder /build/dist ./dist
COPY --from=builder /build/node_modules ./node_modules
COPY package*.json ./

COPY --from=builder /build/src/modules/mail/views ./src/modules/mail/views
COPY --from=builder /build/src/modules/storage/docs/swagger.yaml ./src/modules/storage/docs/swagger.yaml

# Expose ports used by the services (adjust based on actual service ports)
EXPOSE 3000

CMD ["/bin/sh", "-c", "node dist/index.js"]
