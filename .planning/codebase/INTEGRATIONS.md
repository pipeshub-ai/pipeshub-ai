# External Integrations

**Analysis Date:** 2026-01-30

## APIs & External Services

**Large Language Models:**
- OpenAI - Chat and embedding models
  - SDK/Client: langchain-openai 1.1.1
  - Auth: API keys configured via environment

- Anthropic Claude - LLM inference
  - SDK/Client: langchain-anthropic 1.2.0
  - Auth: API keys via environment

- Google Generative AI - Gemini models
  - SDK/Client: langchain-google-genai 3.0.0
  - Auth: API keys via environment

- AWS Bedrock - AWS-managed LLMs
  - SDK/Client: langchain-aws 1.1.0
  - Auth: AWS credentials

- Cohere - Text generation
  - SDK/Client: langchain-cohere 0.5.0
  - Auth: API keys

- Groq - Fast inference
  - SDK/Client: langchain-groq 1.1.0
  - Auth: API keys

- Mistral AI - Open-weight models
  - SDK/Client: langchain-mistralai 0.2.12
  - Auth: API keys

- Voyage AI - Embeddings
  - SDK/Client: langchain-voyageai 0.3.0
  - Auth: API keys

**Collaboration & Communication:**
- Slack - Bot and workspace integration
  - SDK/Client: @slack/bolt 4.6.0
  - Implementation: `backend/nodejs/apps/src/integrations/slack-bot/`
  - Auth: Slack tokens, OAuth configured via environment

- Discord - Chat bot integration
  - SDK/Client: discord.py 2.6.3
  - Auth: Discord bot tokens

**Document & Content Sources:**
- GitHub - Source code repositories
  - SDK/Client: PyGithub 2.8.1
  - Auth: GitHub tokens/OAuth

- Notion - Knowledge base connector
  - SDK/Client: notion-client 2.2.1
  - Auth: Notion API tokens

- Google Drive - File storage integration
  - SDK/Client: google-api-python-client 2.161.0
  - Auth: google-auth-oauthlib 1.2.1 with OAuth flow

- GitLab - Repository connector
  - SDK/Client: python-gitlab 6.4.0
  - Auth: GitLab tokens

- Dropbox - File storage connector
  - SDK/Client: dropbox 12.0.2
  - Auth: Dropbox tokens

- Box - Enterprise file sharing
  - SDK/Client: box-sdk-gen 1.12.0
  - Auth: Box tokens

- Evernote - Note connector
  - SDK/Client: evernote3 >=1.25.14
  - Auth: Evernote tokens

- Trello - Project management
  - SDK/Client: py-trello 0.20.1
  - Auth: Trello API keys

- Monday.com - Workflow connector
  - SDK/Client: monday-api-python-sdk >=0.1.0
  - Auth: Monday tokens

- LinkedIn - Professional network
  - SDK/Client: linkedin-api-client >=0.3.0
  - Auth: LinkedIn tokens

- PagerDuty - Incident management
  - SDK/Client: pagerduty >=5.0.0
  - Auth: PagerDuty API keys

- Microsoft Graph - O365 integration
  - SDK/Client: msgraph-sdk 1.16.0
  - Auth: Azure AD OAuth

## Data Storage

**Databases:**
- MongoDB 6.14.2
  - Connection: `MONGO_URI` environment variable
  - Client: mongoose 8.9.2 (Node.js), pymongo via langchain (Python)
  - Purpose: Primary document database for enterprise search data

- ArangoDB 10.1.1
  - Connection: `ARANGO_URL`, `ARANGO_DB_NAME`, `ARANGO_USERNAME`, `ARANGO_PASSWORD`
  - Client: arangojs 10.1.1 (Node.js), python-arango 8.1.5 (Python)
  - Purpose: Graph database for relationships and entities

- Qdrant - Vector Database
  - Connection: `QDRANT_HOST`, `QDRANT_PORT`, `QDRANT_GRPC_PORT`, `QDRANT_API_KEY`
  - Client: @qdrant/js-client-rest 1.14.1, langchain-qdrant 1.0.0
  - Purpose: Vector embeddings storage for semantic search

- Redis 5.4.2
  - Connection: `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_URL`, `REDIS_DB`
  - Client: ioredis 5.4.2 (Node.js), redis 5.2.1 (Python)
  - Purpose: Caching, session store, key-value operations
  - KV Store Mode: Can be used with `KV_STORE_TYPE=redis`

**Configuration Storage:**
- ETCD - Distributed Configuration
  - Connection: `ETCD_URL`, `ETCD_HOST`, `ETCD_TIMEOUT`
  - Client: etcd3 0.12.0 (Python), etcd3 1.1.2 (Node.js)
  - Purpose: Centralized configuration and feature flags
  - Default KV store: `KV_STORE_TYPE=etcd`

**File Storage:**
- AWS S3
  - Connection: AWS credentials via environment
  - Client: aws-sdk 2.1534.0
  - Adapter: `backend/nodejs/apps/src/modules/storage/providers/s3.provider.ts`
  - Config: `S3StorageConfig` with accessKeyId, secretAccessKey, region, bucketName

- Azure Blob Storage
  - Connection: `AZURE_BLOB_CONNECTION_STRING` or account credentials
  - Client: @azure/storage-blob 12.25.0
  - Adapter: `backend/nodejs/apps/src/modules/storage/providers/azure.provider.ts`
  - Config: Connection string, account name, account key, container name

- Google Cloud Storage
  - Connection: Google credentials
  - Client: google-cloud-storage 2.18.0
  - Purpose: Cloud file storage integration

**Caching:**
- Redis - In-memory cache and session store
  - Configuration: `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`
  - Prefix: `REDIS_KV_PREFIX` (default: "pipeshub:kv:")

## Authentication & Identity

**Auth Providers:**
- Azure AD / Microsoft Entra ID
  - Implementation: MSAL (Microsoft Authentication Library)
  - Frontend: @azure/msal-browser 4.2.1, @azure/msal-react 3.0.4
  - Backend: @azure/msal-node 3.8.4
  - Approach: OAuth 2.0 with MSAL
  - Controller: `backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts`

- SAML SSO
  - Implementation: Passport SAML strategy
  - Package: @node-saml/passport-saml 5.1.0
  - Callback URL: Configured in `saml.controller.ts`
  - Controller: `backend/nodejs/apps/src/modules/auth/controller/saml.controller.ts`

- Google OAuth
  - Frontend: @react-oauth/google 0.12.1
  - Backend: google-auth-library 9.15.1, passport-google-oauth20 2.0.0
  - Controller: `backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts`

- JWT/Custom Auth
  - Token generation: jsonwebtoken 9.0.3
  - JWT secrets: `jwtSecret`, `scopedJwtSecret` in config
  - Token expiry: `ACCESS_TOKEN_EXPIRY` (default 24h), `REFRESH_TOKEN_EXPIRY` (default 720h)
  - Service: `backend/nodejs/apps/src/libs/services/authtoken.service.ts`

## Messaging & Notifications

**Email:**
- Nodemailer 7.0.11
  - Purpose: Email sending for notifications and invitations
  - Module: `backend/nodejs/apps/src/modules/mail/`
  - Container: `MailServiceContainer` for initialization

**Real-time Notifications:**
- Socket.io 4.8.1
  - Purpose: WebSocket-based real-time communication
  - Use case: Live updates and notifications

- Kafka - Event streaming
  - Connection: `KAFKA_BROKERS` (default: localhost:9092)
  - Client: kafkajs 2.2.4 (Node.js), aiokafka 0.12.0 (Python)
  - SSL/SASL: Optional configuration for secure connections
  - Topics: Managed via `kafka-admin.service.ts`
  - Purpose: Asynchronous event processing and inter-service communication

**Chat Integration:**
- Slack Bot - `backend/nodejs/apps/src/integrations/slack-bot/`
  - Handler files:
    - `slackApp.ts` - Main Slack app initialization
    - `receiver.ts` - Express receiver for Slack events
    - `authorizeFn.ts` - Authorization logic
    - `utils/conversation.ts` - Conversation utilities
    - `utils/db.ts` - Database persistence

## Monitoring & Observability

**Error Tracking:**
- Not detected as dedicated integration

**Logging:**
- Winston 3.17.0 - Application logging
  - Service: `backend/nodejs/apps/src/libs/services/logger.service.ts`
  - Approach: Structured logging with logger instances per service

**Metrics:**
- Prometheus - Metrics export
  - Client: prom-client 15.1.3
  - Purpose: Performance monitoring and metrics collection

## CI/CD & Deployment

**Hosting:**
- Docker containerization
  - Main Dockerfile: `Dockerfile` (multi-stage build)
  - Cloud variant: `Dockerfile.cloud`
  - Multi-service architecture with process monitor script

**Services Orchestrated:**
- Node.js backend service (port 3000)
- Python Docling service (document processing)
- Python Indexing service (port 8091)
- Python Connector service (port 8088)
- Python Query service (port 8000)
- Frontend served from backend (port 3000)

## Webhooks & Callbacks

**Incoming Webhooks:**
- Slack Bot events - Received via `/slack/events` endpoint
- Document processing callbacks - From docling service
- Integration webhooks - From external data sources

**Outgoing Webhooks:**
- Notifications sent to Slack via bot
- Email notifications via Nodemailer
- Event publishing to Kafka topics for inter-service communication

## Environment Configuration

**Required Environment Variables:**
- `PORT` - API server port (default: 3000)
- `NODE_ENV` - Environment (development/production)
- `LOG_LEVEL` - Logging level
- `ALLOWED_ORIGINS` - CORS allowed origins
- `SECRET_KEY` - Application secret
- `FRONTEND_PUBLIC_URL` - Frontend URL
- `QUERY_BACKEND` - Query service URL
- `CONNECTOR_BACKEND` - Connector service URL
- `INDEXING_BACKEND` - Indexing service URL
- `KV_STORE_TYPE` - Key-value store selection (redis/etcd)
- `ETCD_URL`, `ETCD_HOST`, `ETCD_TIMEOUT` - ETCD configuration
- `ARANGO_URL`, `ARANGO_DB_NAME`, `ARANGO_USERNAME`, `ARANGO_PASSWORD` - ArangoDB config
- `KAFKA_BROKERS` - Kafka connection string
- `KAFKA_SASL_MECHANISM` - SASL auth (plain, scram-sha-256, scram-sha-512)
- `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB` - Redis config
- `MONGO_URI`, `MONGO_DB_NAME` - MongoDB connection
- `QDRANT_API_KEY`, `QDRANT_HOST`, `QDRANT_PORT`, `QDRANT_GRPC_PORT` - Qdrant config
- `OLLAMA_API_URL` - Local LLM model URL
- `ACCESS_TOKEN_EXPIRY`, `REFRESH_TOKEN_EXPIRY` - Token expiry times

**Secrets Location:**
- Environment variables via `.env` file (template: `backend/env.template`)
- Configuration persisted in ETCD or Redis based on `KV_STORE_TYPE`
- Encrypted storage credentials handled by `StorageService`

---

*Integration audit: 2026-01-30*
