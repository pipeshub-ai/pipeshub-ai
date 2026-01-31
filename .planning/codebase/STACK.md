# Technology Stack

**Analysis Date:** 2026-01-30

## Languages

**Primary:**
- TypeScript 5.7.2 - Backend (Node.js) and Frontend build system
- Python 3.10 - Backend services (indexing, query, connectors, document processing)
- JavaScript/JSX - Frontend UI (React 18.3.1)

**Secondary:**
- Shell - Docker and process management scripts

## Runtime

**Environment:**
- Node.js 20.x - Backend API server, frontend build tooling
- Python 3.10 - Python services in microservices architecture

**Package Managers:**
- npm - Node.js dependencies (backend and frontend)
- yarn 1.22.22 - Frontend package manager
- uv - Python package manager (for faster pip operations)

**Lockfiles:**
- package-lock.json (backend Node.js)
- package.json exists for both backend and frontend

## Frameworks

**Core Backend:**
- Express 4.21.2 - REST API framework
- FastAPI 0.115.6 - Python async web framework for query, connector, docling, and indexing services

**Frontend:**
- React 18.3.1 - UI library
- Vite 7.2.6 - Build tool and dev server
- React Router 6.30.2 - Client-side routing
- MUI (Material-UI) 5.16.7 - Component library with data grid and date pickers
- Redux 2.3.0 (@reduxjs/toolkit) - State management

**Testing:**
- Mocha 12.0.0-beta-2 - Test runner (Node.js backend)
- Chai 5.1.2 - Assertion library
- Sinon 19.0.2 - Mocking library
- Jest 29.5.14 - Test framework (types available)

**Build/Dev:**
- TypeScript 5.5.4, 5.7.2 - Transpilation and type checking
- Vite 7.2.6 - Frontend bundler
- Nodemon 3.1.11 - Development server auto-reload
- ESLint 8.57.0, 9.39.1 - Code linting
- Prettier 3.3.3, 3.4.2 - Code formatting
- ts-node 10.9.2 - TypeScript execution

## Key Dependencies

**Critical Backend:**
- mongodb 6.14.2 - Document database
- mongoose 8.9.2 - MongoDB ODM
- arangojs 10.1.1 - ArangoDB client for graph operations
- ioredis 5.4.2 - Redis client
- kafkajs 2.2.4 - Kafka message broker client
- @qdrant/js-client-rest 1.14.1 - Qdrant vector DB client

**LLM & AI:**
- langchain 1.1.3 - LLM framework
- langchain-openai 1.1.1 - OpenAI integration
- langchain-anthropic 1.2.0 - Anthropic Claude integration
- langchain-aws 1.1.0 - AWS Bedrock integration
- langchain-google-genai 3.0.0 - Google Generative AI
- langchain-qdrant 1.0.0 - Qdrant vector store
- sentence-transformers 3.4.1 - Embedding models
- fastembed 0.5.1 - Fast local embeddings

**Document Processing:**
- docling 2.60.0 - PDF/document parsing
- pdf2image 1.17.0 - PDF to image conversion
- ocrmypdf 16.8.0 - OCR processing
- python-docx 1.1.2 - Word document handling
- openpyxl 3.1.5 - Excel file handling
- beautifulsoup4 4.12.3 - HTML parsing

**File Storage:**
- aws-sdk 2.1534.0 - AWS S3 integration
- @azure/storage-blob 12.25.0 - Azure Blob Storage
- @azure/storage-file-share 12.19.0 - Azure File Share
- google-cloud-storage 2.18.0 - Google Cloud Storage

**Authentication:**
- @azure/msal-browser 4.2.1, @azure/msal-react 3.0.4 - Azure AD (frontend)
- @azure/msal-node 3.8.4 - Azure AD (backend)
- passport 0.7.0 - Authentication middleware
- @node-saml/passport-saml 5.1.0 - SAML SSO
- google-auth-library 9.15.1 - Google OAuth
- passport-google-oauth20 2.0.0 - Google OAuth strategy
- jsonwebtoken 9.0.3 - JWT handling
- jwks-rsa 3.1.0 - JWKS support

**Messaging & Communication:**
- @slack/bolt 4.6.0 - Slack bot framework
- nodemailer 7.0.11 - Email sending
- discord.py 2.6.3 - Discord bot integration
- slack-sdk 3.27.0 - Slack SDK (Python)

**Data/Database:**
- redis 5.2.1 - Redis client (Python)
- etcd3 0.12.0, etcd3 1.1.2 - ETCD client (key-value store)
- python-arango 8.1.5 - ArangoDB Python client
- confluent-kafka 2.8.0 - Kafka Python client

**Frontend UI/UX:**
- @fullcalendar/react 6.1.15 - Calendar component
- @tiptap/react 2.27.1 - Rich text editor
- apexcharts 3.52.0 - Charting
- mapbox-gl 3.4.0 - Map visualization
- framer-motion 11.3.29 - Animations
- axios 1.12.0 - HTTP client

**Security:**
- bcryptjs 3.0.2 - Password hashing
- helmet 8.0.0 - HTTP security headers
- dompurify 3.2.5 - XSS protection
- xss 1.0.15 - XSS sanitization
- zod 3.23.8, 3.24.1 - Schema validation

**Utilities:**
- dayjs 1.11.18 - Date library
- lodash 4.17.23 - Utility functions
- uuid 11.0.3 - UUID generation
- joi-to-markdown 2.3 - HTML to markdown conversion
- prom-client 15.1.3 - Prometheus metrics

## Configuration

**Environment:**
- Environment variables loaded via dotenv 16.4.7
- Configuration managed via file: `backend/env.template`
- Key config file: `/c/Users/isuru/roo_code/AktorAI/knowledge-hub/backend/env.template`
- Redis key-value store or ETCD as config backends

**Build:**
- `backend/nodejs/apps/tsconfig.json` - TypeScript backend config
- `frontend/tsconfig.json` - React frontend TypeScript config
- `backend/nodejs/apps/.eslintrc.json` - Backend linting rules
- `backend/nodejs/apps/.prettierrc` - Backend code formatting (80 char width)

## Platform Requirements

**Development:**
- Node.js 20.x
- Python 3.10+
- Docker and Docker Compose (for full stack)
- Yarn 1.22.22 or npm

**Production:**
- Docker container deployment
- Multi-stage build: Python, Node.js, Frontend build stages
- Deployment targets: Cloud platforms (referenced in `Dockerfile.cloud`)
- Runtime exposed ports: 3000 (API), 8000 (Query), 8088 (Connector), 8091 (Indexing), 8081 (Other)

**External Services Required:**
- MongoDB - Document database
- Redis - Caching and session store
- ArangoDB - Graph database
- Qdrant - Vector database
- Kafka - Message broker
- ETCD - Configuration and distributed key-value store
- LLM APIs (OpenAI, Anthropic, Google, AWS Bedrock, Cohere, etc.)
- Cloud Storage (AWS S3, Azure Blob, Google Cloud Storage)

---

*Stack analysis: 2026-01-30*
