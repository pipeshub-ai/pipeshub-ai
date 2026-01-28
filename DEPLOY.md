# Deploying Pipeshub AI on Railway

This guide outlines the steps to deploy Pipeshub AI and its dependencies on [Railway](https://railway.app/).

## Overview

Pipeshub AI requires several backing services to function:

1. **Main Application** (The `pipeshub-ai` repo)
2. **MongoDB** (Database)
3. **Redis** (Cache)
4. **ArangoDB** (Graph Database)
5. **Qdrant** (Vector Database)
6. **Etcd** (Configuration Store)
7. **Kafka & Zookeeper** (Event Streaming)

## Step 1: Provision Backing Services

Create a new Project in Railway and add the following services.

### 1. MongoDB (Managed)

- Add a **MongoDB** service from the Railway database creation menu.
- Note the `MONGO_URL` (connection string), `MONGOUSER`, `MONGOPASSWORD`, `MONGOHOST`, `MONGOPORT`.

### 2. Redis (Managed)

- Add a **Redis** service from the Railway database creation menu.
- Note the `REDIS_URL` or `REDISHOST`, `REDISPORT`, `REDISPASSWORD`.

### 3. ArangoDB (Docker Image)

- **Image**: `arangodb:3.12.4`
- **Variables**:
  - `ARANGO_ROOT_PASSWORD`: (Set a strong password)
- **Note**: The internal port is `8529`.

### 4. Qdrant (Docker Image)

- **Image**: `qdrant/qdrant:v1.15`
- **Variables**:
  - `QDRANT__SERVICE__API_KEY`: (Set a secret API key)
- **Note**: The internal HTTP port is `6333` and gRPC is `6334`.

### 5. Etcd (Docker Image)

- **Image**: `quay.io/coreos/etcd:v3.5.17`
- **Start Command**:
  ```bash
  etcd --name etcd-node --data-dir /etcd-data --listen-client-urls http://0.0.0.0:2379 --advertise-client-urls http://${RAILWAY_PRIVATE_DOMAIN}:2379 --listen-peer-urls http://0.0.0.0:2380 --initial-advertise-peer-urls http://${RAILWAY_PRIVATE_DOMAIN}:2380 --initial-cluster etcd-node=http://${RAILWAY_PRIVATE_DOMAIN}:2380
  ```
  _(Note: Railway injects `RAILWAY_PRIVATE_DOMAIN` which resolves to the service internal address. You may need to adjust the command if strict advertising is required, but for single node, `http://0.0.0.0:2379` usually suffices for listening.)_
- **Alternative Simple Data** (If custom command is complex): Just run the image. Default usually works for single node dev.

### 6. Kafka & Zookeeper

_Note: These are heavy services. Ensure your Railway plan supports them._

**Zookeeper**:

- **Image**: `confluentinc/cp-zookeeper:7.9.0`
- **Variables**:
  - `ZOOKEEPER_CLIENT_PORT`: `2181`
  - `ZOOKEEPER_TICK_TIME`: `2000`

**Kafka**:

- **Image**: `confluentinc/cp-kafka:7.9.0`
- **Variables**:
  - `KAFKA_BROKER_ID`: `1`
  - `KAFKA_ZOOKEEPER_CONNECT`: `zookeeper:2181` (Assuming the Zookeeper service is named `zookeeper`)
  - `KAFKA_LISTENER_SECURITY_PROTOCOL_MAP`: `ACCESS:PLAINTEXT`
  - `KAFKA_LISTENERS`: `ACCESS://0.0.0.0:9092`
  - `KAFKA_ADVERTISED_LISTENERS`: `ACCESS://${RAILWAY_PRIVATE_DOMAIN}:9092`
  - `KAFKA_INTER_BROKER_LISTENER_NAME`: `ACCESS`
  - `KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR`: `1`
  - `KAFKA_CREATE_TOPICS`: `record-events:1:1,entity-events:1:1,sync-events:1:1`

## Step 2: Deploy the Main Application

1. Deploy this repository to Railway.
2. Railway will automatically detect the `Dockerfile` in the root.
3. Configure the **Environment Variables** for this service using the values from Step 1.

### Required Environment Variables

| Variable                   | Description                | Example Value                                                                         |
| -------------------------- | -------------------------- | ------------------------------------------------------------------------------------- |
| `NODE_ENV`                 | Environment mode           | `production`                                                                          |
| `LOG_LEVEL`                | Logging level              | `info`                                                                                |
| `SECRET_KEY`               | Random secret string       | `your-secret-key-123`                                                                 |
| `ALLOWED_ORIGINS`          | CORS allowed origins       | `https://your-frontend-url.up.railway.app`                                            |
| `FRONTEND_PUBLIC_URL`      | Public URL of the app      | `https://your-frontend-url.up.railway.app`                                            |
| `CONNECTOR_PUBLIC_BACKEND` | Public URL for connectors  | `https://your-frontend-url.up.railway.app`                                            |
| **Databases**              |                            |                                                                                       |
| `MONGO_URI`                | MongoDB Connection URI     | `mongodb://${MONGOUSER}:${MONGOPASSWORD}@${MONGOHOST}:${MONGOPORT}/?authSource=admin` |
| `MONGO_DB_NAME`            | Database name              | `es`                                                                                  |
| `REDIS_URL`                | Redis Connection URI       | `redis://:${REDISPASSWORD}@${REDISHOST}:${REDISPORT}`                                 |
| `ARANGO_URL`               | Internal URL to ArangoDB   | `http://${ARANGO_SERVICE_NAME}:8529`                                                  |
| `ARANGO_DB_NAME`           | Database name              | `es`                                                                                  |
| `ARANGO_USERNAME`          | Username                   | `root`                                                                                |
| `ARANGO_PASSWORD`          | Password set in Step 1     | `your-arango-password`                                                                |
| `ETCD_URL`                 | Internal URL to Etcd       | `http://${ETCD_SERVICE_NAME}:2379`                                                    |
| `ETCD_HOST`                | Hostname of Etcd service   | `${ETCD_SERVICE_NAME}`                                                                |
| `KAFKA_BROKERS`            | Internal URL to Kafka      | `${KAFKA_SERVICE_NAME}:9092`                                                          |
| `QDRANT_HOST`              | Hostname of Qdrant service | `${QDRANT_SERVICE_NAME}`                                                              |
| `QDRANT_PORT`              | HTTP Port                  | `6333`                                                                                |
| `QDRANT_GRPC_PORT`         | gRPC Port                  | `6334`                                                                                |
| `QDRANT_API_KEY`           | Key set in Step 1          | `your-qdrant-key`                                                                     |
| **Internal**               |                            |                                                                                       |
| `QUERY_BACKEND`            | Internal Query URL         | `http://localhost:8000`                                                               |
| `CONNECTOR_BACKEND`        | Internal Connector URL     | `http://localhost:8088`                                                               |
| `INDEXING_BACKEND`         | Internal Indexing URL      | `http://localhost:8091`                                                               |

_(Replace `${VARIABLE_NAME}` with the actual values or service names provided by Railway. For service names, use the name you gave the service, e.g., `kafka`, `etcd`, `arango`.)_

### Optional Variables

- `OLLAMA_API_URL`: If you have a hosted Ollama instance (e.g., on another server), provide the URL here.

## Deployment Notes

- The application uses a `process_monitor.sh` script (defined in the Dockerfile) to run multiple internal processes (Node.js, python services) within the single container.
- Ensure the **Build Command** in Railway is empty (let it use the Dockerfile).
- Ensure the **Start Command** is empty (let it use the Dockerfile `CMD`).

## Security & Production Readiness

### HTTPS & SSL

- **Single Container Deployment**: If you use the default setup where the backend runs on port `3000` and serves the frontend, you only need one SSL certificate for your main domain.
- **Separated Deployment**: If you host the frontend separately (e.g., Vercel, Netlify), you must ensure BOTH the frontend and backend are served over HTTPS. Chrome and other browsers block mixed content (HTTP backend called from HTTPS frontend).

### CORS Configuration

- In production, set `ALLOWED_ORIGINS` to your actual frontend domain (e.g., `https://app.yourdomain.com`).
- For the default single-container setup, the backend serves the frontend from the same origin, so CORS is less critical but still good practice to restrict.

### Dependencies

- The Python backend requires `cachetools` and other libraries listed in `backend/python/pyproject.toml`. These are automatically installed during the Docker build.
