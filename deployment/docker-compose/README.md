<a name="readme-top"></a>

<p align="center">
<a href="https://www.pipeshub.com/">
<img width="10%" alt="PipesHub Logo" src="https://raw.githubusercontent.com/pipeshub-ai/media-assets/refs/heads/main/images/pipeshub-logo.svg"/>
</a>
</p>

<p align="center"><b>Docker Compose – Dev Stack</b></p>

<p align="center">
  <a href="https://docs.pipeshub.com/" target="_blank">
    <img src="https://img.shields.io/badge/Docs-View-informational?style=flat-square&logo=readthedocs&logoColor=white" alt="Docs" style="height:28px;">
  </a>
  &nbsp;&nbsp;
  <a href="https://discord.com/invite/K5RskzJBm2" target="_blank">
    <img src="https://img.shields.io/discord/1359557598222745670?label=Discord&logo=discord&logoColor=white&style=flat-square" alt="Discord" style="height:28px;">
  </a>
</p>

---

This folder contains all Docker Compose files for running **PipesHub AI** locally in development or production.

## 📁 Files in this Folder

| File | Purpose |
|---|---|
| `docker-compose.dev.neo4j.yml` | **Dev** — Neo4j (graph DB) + Redis (KV store) — recommended |
| `docker-compose.dev.yml` | **Dev** — ArangoDB (graph DB) + etcd (KV store) |
| `docker-compose.prod.yml` | **Production** — pulls pre-built image from Docker Hub |
| `docker-compose.integration.neo4j.yml` | Integration tests with Neo4j |
| `docker-compose.integration.arango.yml` | Integration tests with ArangoDB |
| `Dockerfile.dev` | Dev image with hot reload (nodemon + Vite + watchmedo) |
| `env.template` | Copy to `.env` and fill in your secrets |
| `scripts/dev_process_monitor.sh` | Entrypoint: starts all services with hot reload watchers |
| `scripts/start-ollama.sh` | Helper: starts Ollama and pulls a local AI model |

---

## 🚀 Developer Setup (Recommended)

Uses **Neo4j** as the graph database and **Redis** as the KV store. Includes **hot reload** for all services.

```bash
# 📁 Navigate to this folder
cd pipeshub-ai/deployment/docker-compose

# 📝 Set up your environment variables
cp env.template .env
# Edit .env — fill in SECRET_KEY, passwords, API keys, etc.

# 🔨 Build the dev image (first time, or after dependency changes)
docker compose -f docker-compose.dev.neo4j.yml build

# 🚀 Start all services in the background
docker compose -f docker-compose.dev.neo4j.yml up -d

# 🛑 To stop all services
docker compose -f docker-compose.dev.neo4j.yml down
```

> **Note:** Wait **30–60 seconds** after starting before opening the app. The Python services (Connector, Query) must be healthy before the Node backend finishes booting.

---

## 🌐 Services & Ports

| Service | URL |
|---|---|
| **Backend (Node.js API)** | http://localhost:3000 |
| **Frontend (Vite dev server)** | http://localhost:3001 |
| Neo4j Browser | http://localhost:7474 |
| MongoDB | localhost:27017 |
| Redis | localhost:6379 |
| Kafka | localhost:9092 |
| Qdrant (vector DB) | http://localhost:6333 |

---

## 🔥 Hot Reload

The dev image uses **bind mounts** — your local source code is synced live into the container. Save a file and the relevant service restarts automatically. **No rebuild required.**

| Layer | Tool | Watches |
|---|---|---|
| **Node.js backend** | `nodemon` | `backend/nodejs/apps/src/**/*.ts` |
| **Frontend** | Vite HMR | `frontend/src/**` |
| **Python services** | `watchmedo auto-restart` | `backend/python/app/**/*.py` |

> **Rebuild only when** you add/remove a package in `package.json` or `pyproject.toml`.

---

## 🛠️ Useful Commands

```bash
# 📋 Check running containers
docker compose -f docker-compose.dev.neo4j.yml ps

# 📜 Follow live logs
docker compose -f docker-compose.dev.neo4j.yml logs -f pipeshub-ai

# 🔨 Rebuild after Dockerfile or dependency changes
docker compose -f docker-compose.dev.neo4j.yml build --no-cache pipeshub-ai
docker compose -f docker-compose.dev.neo4j.yml up -d
```

---

## 🧩 Dev with ArangoDB + etcd (Alternative)

```bash
# 🔨 Build
docker compose -f docker-compose.dev.yml build

# 🚀 Start
docker compose -f docker-compose.dev.yml up -d

# 🛑 Stop
docker compose -f docker-compose.dev.yml down
```

Same ports apply. Frontend on `:3001`, backend on `:3000`.

---

## 🤖 Using Ollama (Local AI Models)

Run AI models locally without needing a cloud API key.

```bash
# 🚀 Start Ollama on your host machine
ollama serve

# ⬇️ Pull a model (default is phi4, override with env var)
OLLAMA_MODEL=llama3 ./scripts/start-ollama.sh
```

The app is pre-configured with `OLLAMA_API_URL=http://host.docker.internal:11434`.

---

## 📦 Production Deployment

```bash
# 📁 Navigate to this folder
cd pipeshub-ai/deployment/docker-compose

# 📝 Set environment variables
cp env.template .env

# 🚀 Start production stack (pulls image from Docker Hub — no build needed)
docker compose -f docker-compose.prod.yml -p pipeshub-ai up -d

# 🛑 Stop
docker compose -f docker-compose.prod.yml -p pipeshub-ai down
```

> **Important:** For cloud deployments, use an **HTTPS** endpoint. Browsers block certain requests over plain HTTP. Use a reverse proxy like Nginx, Traefik, or Cloudflare to terminate TLS.

---

## ❓ Troubleshooting

- **`"Failed to connect to services. Retrying..."`** — Wait 30–60s and refresh. Services are still booting.
- **Port already in use** — Stop local services on conflicting ports, or edit the ports in your compose file.
- **Code change not picked up** — Check `docker compose logs -f pipeshub-ai`. The watcher should log a restart.
- **New package not found** — Run `docker compose build` to reinstall dependencies into the image.

---

<p align="right"><a href="#readme-top">↑ back to top</a></p>
