# Multi-stage Dockerfile for all services

# Stage 1: Build
FROM node:24@sha256:9d266751ca3d7fdbe651c878ffd5ad0a42fed49a18c283509267c97f025b7496 AS builder

# Set working directory for the build
WORKDIR /build

RUN apt-get update && apt-get install -y iputils-ping telnet traceroute dnsutils net-tools curl wget

# Install dependencies and build for each service
# Prepare and build storage-backend
COPY package*.json ./
COPY tsconfig.json ./
# No install scripts on any architecture: the lock ships every native part prebuilt (sharp, msgpackr-extract),
# and what the scripts would add is telemetry (@scarf/scarf), a bun binary nothing runs, or a version notice
# (contracts/supply-chain/install-scripts.json). An allowlisted `npm rebuild` would run here if one were needed.
RUN npm config set legacy-peer-deps true && \
    npm ci --prefix ./ --ignore-scripts
COPY src ./src
RUN npm run build --prefix ./
# tsc does not emit .hbs, and emailTemplates.ts reads them from ${process.cwd()}/src
# at runtime, so every template under src is staged here with its path kept.
RUN mkdir -p /build/templates && cd src && find . -name '*.hbs' -exec cp --parents {} /build/templates/ \;

# Stage 2: Runtime
FROM node:24@sha256:9d266751ca3d7fdbe651c878ffd5ad0a42fed49a18c283509267c97f025b7496

# Install essential networking tools and SSH server
RUN apt-get update && apt-get install -y \
    iputils-ping telnet traceroute dnsutils net-tools curl wget \
    openssh-server

# pm2 from its committed lock (integrity-checked by npm ci), not the registry's latest
COPY docker/pm2/package.json docker/pm2/package-lock.json /opt/pm2/
RUN npm ci --prefix /opt/pm2 --omit=dev --ignore-scripts --no-audit --no-fund && \
    ln -s /opt/pm2/node_modules/.bin/pm2 /usr/local/bin/pm2 && \
    npm cache clean --force

# Set working directory for the runtime
WORKDIR /app

# Copy built services and dependencies from the builder stage
COPY --from=builder /build/dist ./dist
COPY --from=builder /build/node_modules ./node_modules
COPY package*.json ./

COPY --from=builder /build/templates ./src
COPY --from=builder /build/src/modules/api-docs/pipeshub-openapi.yaml ./dist/modules/api-docs/pipeshub-openapi.yaml

# Expose ports used by the services (adjust based on actual service ports)
EXPOSE 3000

CMD ["/bin/sh", "-c", "node dist/index.js"]
