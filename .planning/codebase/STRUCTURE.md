# Codebase Structure

**Analysis Date:** 2026-01-30

## Directory Layout

```
knowledge-hub/
├── backend/                           # Backend application code
│   └── nodejs/
│       └── apps/
│           ├── src/                   # Main application source
│           │   ├── index.ts           # Entry point (startup)
│           │   ├── app.ts             # Express app setup, container initialization
│           │   ├── modules/           # Feature modules
│           │   ├── libs/              # Shared infrastructure services
│           │   └── utils/             # Utility functions
│           ├── tsconfig.json          # TypeScript configuration
│           ├── package.json           # Dependencies
│           └── Dockerfile             # Container build config
├── frontend/                          # Frontend application code
│   ├── src/                           # React source
│   │   ├── main.tsx                   # React entry point
│   │   ├── app.tsx                    # Root component with providers
│   │   ├── modules/                   # Feature modules
│   │   ├── components/                # Reusable components
│   │   ├── pages/                     # Page components
│   │   ├── store/                     # Redux state management
│   │   ├── routes/                    # Route configuration
│   │   ├── context/                   # React Context providers
│   │   ├── hooks/                     # Custom React hooks
│   │   └── utils/                     # Utility functions
│   └── package.json                   # Dependencies
├── deployment/                        # Deployment configurations
├── docs/                              # Documentation
├── scripts/                           # Build and utility scripts
├── Dockerfile                         # Root container build
├── .github/                           # GitHub workflows and templates
└── .planning/                         # GSD planning documents
    └── codebase/                      # Codebase analysis documents
```

## Directory Purposes

**Backend Source Structure:**

**modules/**
- Purpose: Feature-based module organization; each module is a self-contained business domain
- Contains: Feature-specific controllers, services, routes, schemas, container, constants, validators
- Key files: Container class (DI setup), routes file, controllers, services
- Located at: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\`

**libs/**
- Purpose: Shared cross-cutting infrastructure services used by multiple modules
- Contains: Database services (mongo, arango, redis), authentication, middleware, error definitions, enums, utilities
- Key subdirectories:
  - `commands/`: External service command abstractions (AI, IAM, storage, configuration manager)
  - `enums/`: Shared enumerations (HTTP status, methods, token scopes, DB types)
  - `errors/`: Custom error class hierarchy (base, HTTP, database, validation errors)
  - `keyValueStore/`: Configuration store abstraction with etcd/Redis providers
  - `middlewares/`: Express middleware (auth, error, validation, request context, rate limiting)
  - `services/`: Core infrastructure services (Logger, MongoService, ArangoService, RedisService, AuthTokenService)
  - `types/`: Shared TypeScript interfaces and types
  - `utils/`: Helper functions for logging, error handling, XSS sanitization
- Located at: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\`

**Module Internal Structure (per module):**

Each module in `modules/` follows this pattern:

- `container/` - Inversify DI container setup (e.g., `token-manager.container.ts`)
- `routes/` - Express route definitions (e.g., `userAccount.routes.ts`)
- `controller/` - Request handlers/controllers (e.g., `userAccount.controller.ts`)
- `services/` - Business logic services (e.g., `iam.service.ts`, `session.service.ts`)
- `schema/` - Mongoose or other data model definitions
- `types/` - Module-specific TypeScript interfaces
- `validators/` - Request/data validation schemas (usually Zod)
- `constants/` - Module-specific constants and enums
- `utils/` - Module-specific utility functions
- `middlewares/` (optional) - Module-specific middleware
- `config/` (optional) - Module configuration loader

**Frontend Source Structure:**

- `components/` - Reusable React components
- `pages/` - Page-level components mapped to routes
- `store/` - Redux state management (slices, actions, selectors)
- `context/` - React Context providers (authentication, settings, notifications)
- `hooks/` - Custom React hooks
- `routes/` - Route configuration and navigation setup
- `utils/` - Utility functions (API clients, formatting, helpers)
- `locales/` - Internationalization/translation files
- `theme/` - Theming configuration and styles
- `actions/` - Server actions or Redux thunk actions
- `auth/` - Authentication logic and components
- `layouts/` - Layout wrapper components

## Key File Locations

**Backend Entry Points:**
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\index.ts`: Node.js process entry, Application initialization
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\app.ts`: Express app setup, all containers initialized, routes registered
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\package.json`: Dependencies, build scripts

**Frontend Entry Point:**
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\frontend\src\main.tsx`: React app bootstrap
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\frontend\src\app.tsx`: Root component with all provider setup

**Configuration Files:**
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\tsconfig.json`: TypeScript compiler options (strict mode, path mappings)
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\tokens_manager\config\config.ts`: Core app configuration loader (AppConfig)
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\configuration_manager\config\config.ts`: KV store configuration
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\env.template`: Environment variable template

**Core Services:**
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\services\logger.service.ts`: Centralized Winston-based logging
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\services\mongo.service.ts`: MongoDB connection and management
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\services\arango.service.ts`: ArangoDB connection for knowledge graphs
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\services\redis.service.ts`: Redis caching and sessions
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\services\authtoken.service.ts`: JWT token generation and verification
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\services\keyValueStore.service.ts`: Configuration store (etcd/Redis abstraction)

**Key Middleware:**
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\middlewares\auth.middleware.ts`: JWT validation, token scopes checking
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\middlewares\error.middleware.ts`: Centralized error handling and response formatting
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\middlewares\validation.middleware.ts`: Zod schema validation
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\middlewares\request.context.ts`: AsyncLocalStorage for request context

**Error Classes:**
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\errors\base.error.ts`: Base error class with code, message, statusCode
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\errors\http.errors.ts`: HTTP status errors (400, 401, 403, 404, 500, 502, 504)
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\errors\database.errors.ts`: Database-related errors
- `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\errors\validation.error.ts`: Validation errors

**Major Modules:**

- **auth** (`c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\auth\`): Authentication, user accounts, SAML integration, session management
- **user_management** (`c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\user_management\`): Users, organizations, teams, user groups
- **knowledge_base** (`c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\knowledge_base\`): KB management, documents, folders, records, indexing
- **enterprise_search** (`c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\enterprise_search\`): Semantic search, conversational AI, citations
- **storage** (`c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\storage\`): File uploads, downloads, providers (S3, Azure, GCS)
- **tokens_manager** (`c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\tokens_manager\`): Connector tokens, OAuth tokens, health checks
- **configuration_manager** (`c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\configuration_manager\`): Platform configuration, AI model settings, KV store migration
- **mail** (`c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\mail\`): Email sending, templates, SMTP configuration
- **oauth_provider** (`c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\oauth_provider\`): OAuth 2.0 authorization server, OIDC discovery
- **notification** (`c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\notification\`): WebSocket notifications, real-time updates
- **crawling_manager** (`c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\crawling_manager\`): Web crawling, data sync, connectors

## Naming Conventions

**Files:**
- TypeScript/JavaScript: kebab-case or camelCase with `.ts` or `.tsx` extension
  - Example: `user-account.controller.ts`, `userAccount.routes.ts`, `iam.service.ts`
  - Containers: `*.container.ts` (e.g., `token-manager.container.ts`)
  - Services: `*.service.ts` (e.g., `logger.service.ts`, `iam.service.ts`)
  - Controllers: `*.controller.ts` (e.g., `userAccount.controller.ts`)
  - Middleware: `*.middleware.ts` (e.g., `auth.middleware.ts`)
  - Schemas: `*.schema.ts` (e.g., `users.schema.ts`, `conversation.schema.ts`)
  - Routes: `*.routes.ts` (e.g., `userAccount.routes.ts`, `es.routes.ts`)
  - Validators: `validators.ts` or `*.validator.ts` (e.g., `validators.ts` for Zod schemas)
- Frontend React: PascalCase for component files
  - Example: `UserProfile.tsx`, `AuthProvider.tsx`, `KnowledgeBaseCard.tsx`

**Directories:**
- Feature modules: kebab-case (e.g., `user_management`, `knowledge_base`, `enterprise_search`)
- Subdirectories within modules: matching function (e.g., `container`, `routes`, `services`, `schema`)
- Frontend components: PascalCase (e.g., `UserProfile`, `AuthGuard`)
- Utility directories: kebab-case or camelCase (e.g., `file_processor`, `locales`, `store`)

**TypeScript/JavaScript:**
- Classes: PascalCase (e.g., `UserAccountController`, `AuthMiddleware`, `MongoService`)
- Interfaces/Types: PascalCase with I prefix optional (e.g., `IUser`, `AppConfig`, `AuthRequest`)
- Functions/Methods: camelCase (e.g., `initAuth()`, `validateRequest()`, `buildResponse()`)
- Constants/Enums: UPPER_SNAKE_CASE (e.g., `HTTP_STATUS.OK`, `TOKEN_SCOPES.ADMIN`)
- Variables: camelCase (e.g., `userId`, `authToken`, `errorMessage`)

## Where to Add New Code

**New Feature/Module:**
- Primary code: Create directory in `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\{feature-name}\`
- Structure: Include subdirectories for `container/`, `routes/`, `services/`, `schema/`, `validators/`, `types/`, `constants/`
- Container: Create `{feature-name}.container.ts` in `container/` directory following pattern in existing containers (initialize services, bind to Inversify container, expose dispose method)
- Routes: Create `*.routes.ts` file exporting function that takes Container parameter and returns Express Router
- Controller: Create `*.controller.ts` file with methods for each route handler
- Registration: Import and register routes in `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\app.ts` in `configureRoutes()` method and initialize container in `initialize()` method

**New Service/Utility:**
- Shared service (cross-module): Add to `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\services\` as `{name}.service.ts`
- Module-specific service: Add to `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\{module}\services\` as `{name}.service.ts`
- Utility/helper: Add to `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\utils\` or module-specific `utils/` directory

**New Route/Endpoint:**
- File: Add handler to appropriate controller in `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\{module}\controller\`
- Route: Add router definition in `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\{module}\routes\` with Zod schema validation
- Data Model: Add Mongoose schema to `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\{module}\schema\` if new collection needed

**New Middleware:**
- Global/Cross-cutting: Add to `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\middlewares\` as `{name}.middleware.ts`, register in `app.ts` `configureMiddleware()` method
- Module-specific: Add to `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\{module}\middlewares\`, attach in module routes

**New Error Type:**
- Add custom error class to appropriate file in `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\errors\` extending `BaseError`
- Set code (string identifier), message, statusCode (HTTP status)

**Frontend Component:**
- Location: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\frontend\src\components\` for reusable components
- Sections: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\frontend\src\sections\` for larger feature sections
- Pages: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\frontend\src\pages\` for page-level components

## Special Directories

**node_modules/:**
- Purpose: NPM dependencies (auto-generated)
- Generated: Yes
- Committed: No (listed in .gitignore)
- Management: `npm install` from `package.json`

**dist/ (build output):**
- Purpose: Compiled JavaScript output from TypeScript
- Generated: Yes (by `npm run build`)
- Committed: No (listed in .gitignore)
- Consumed by: Docker build, deployment

**public/ (frontend static assets):**
- Purpose: Static assets served by frontend (fonts, logos, images)
- Generated: No
- Committed: Yes
- Location: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\frontend\public\`

**docs/ and docs/:**
- Purpose: Project documentation, integration guides, architecture docs
- Generated: No
- Committed: Yes
- Notable: `CONNECTOR_INTEGRATION_PLAYBOOK.md` in root

**logs/ (runtime logs):**
- Purpose: Application log files (error.log, combined.log)
- Generated: Yes (at runtime)
- Committed: No
- Location: Root directory or configurable via Logger

**.planning/codebase/ (GSD analysis):**
- Purpose: Codebase analysis documents (ARCHITECTURE.md, STRUCTURE.md, CONVENTIONS.md, TESTING.md, CONCERNS.md)
- Generated: Yes (by GSD tools)
- Committed: Yes
- Management: Updated by `/gsd:map-codebase` commands
- Location: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\.planning\codebase\`

**.github/workflows/**
- Purpose: GitHub Actions CI/CD pipelines
- Generated: No
- Committed: Yes
- Contains: Build, test, deploy workflow definitions
