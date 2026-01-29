# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PipesHub is a workplace AI platform for enterprise search and workflow automation. It indexes data from 30+ sources (Google Workspace, Microsoft 365, Slack, Jira, Confluence, etc.) and enables natural language search with knowledge graphs.

## Architecture

Three-tier architecture with dual backend:

```
frontend/          → React/TypeScript UI (Vite, port 3001)
backend/nodejs/    → Express API, auth, business logic (port 3000)
backend/python/    → AI/ML microservices (FastAPI)
  - connectors_main.py   → Data source connections (port 8088)
  - indexing_main.py     → Document processing, vector indexing (port 8081)
  - query_main.py        → Search and retrieval (port 8000)
  - docling_main.py      → Document parsing
```

**Key infrastructure**: MongoDB (primary DB), ArangoDB (graph/knowledge), Qdrant (vector search), Redis (cache), Kafka (messaging), ETCD (config).

## Common Commands

### Frontend
```bash
cd frontend
yarn install           # Install dependencies (preferred)
yarn dev               # Development server (port 3001)
yarn build             # Production build (tsc && vite build)
yarn lint              # ESLint check
yarn lint:fix          # Auto-fix lint issues
yarn fm:check          # Prettier check
yarn fm:fix            # Auto-format
```

### Node.js Backend
```bash
cd backend/nodejs/apps
npm install
npm run dev            # Development with nodemon
npm run build          # TypeScript compilation
npm run lint           # ESLint
npm run format         # Prettier
npm test               # Mocha tests
```

### Python Backend
```bash
cd backend/python
python3.10 -m venv venv
source venv/bin/activate
pip install -e .
python -m spacy download en_core_web_sm
python -c "import nltk; nltk.download('punkt')"

# Run services (each in separate terminal)
python -m app.connectors_main
python -m app.indexing_main
python -m app.query_main
python -m app.docling_main
```

### Docker Deployment
```bash
cd deployment/docker-compose
docker compose -f docker-compose.dev.yml up --build -d   # Development
docker compose -f docker-compose.prod.yml up -d          # Production
```

## Code Organization

### Frontend (`frontend/src/`)
- `pages/` - Route entry points
- `sections/` - Feature implementations (accountdetails, qna, knowledgebase, oauth)
- `components/` - Reusable UI components
- `layouts/` - Page layouts (dashboard, auth)
- `hooks/` - Custom React hooks
- `utils/` - API services and utilities
- `store/` - Redux state management
- `context/` - React context providers

### Node.js Backend (`backend/nodejs/apps/src/`)
- `modules/` - Feature modules with consistent structure:
  - auth, user_management, enterprise_search, knowledge_base
  - storage, tokens_manager, configuration_manager, mail
  - notification, crawling_manager, api-docs, oauth_provider
- Each module contains: routes, services, container (Inversify DI)

### Python Backend (`backend/python/app/`)
- `connectors/` - Data source integrations (30+ connectors)
- `modules/indexing/` - Vector and graph indexing pipelines
- `modules/retrieval/` - Semantic search implementation
- `modules/qna/` - Question-answering pipeline
- `agents/` - AI agent tools and actions
- `services/` - Database and messaging services
- `config/` - Configuration management

## Key Patterns

- **Dependency Injection**: Node.js uses Inversify; Python uses dependency-injector
- **LLM Integration**: LangChain-based pipelines supporting 15+ providers (OpenAI, Anthropic, Cohere, Ollama, etc.)
- **Vector Search**: Qdrant for semantic similarity
- **Graph Database**: ArangoDB for knowledge graphs and relationships
- **Async Communication**: Kafka for inter-service messaging

## CI/CD Validations

PRs to main trigger:
- Frontend: `npm install && npm run build`
- Node.js Backend: `npm install && npm run build`
- Python: `ruff check .` with rules for type annotations (ANN201-206), naming (N804), unused variables (F841), magic numbers (PLR2004)

## Environment Setup

Required infrastructure (run via Docker):
- Redis (6379), Qdrant (6333), ETCD (2379), ArangoDB (8529), MongoDB (27017), Kafka+Zookeeper (9092, 2181)

See `CONTRIBUTING.md` for detailed Docker commands and `deployment/docker-compose/env.template` for environment variables.

## Important Notes

- HTTPS required in production (browsers block HTTP for security features)
- Whitelabel support via `VITE_*` environment variables
- Node.js version: 20.x; Python version: 3.10
- Frontend prefers Yarn; Backend uses npm
