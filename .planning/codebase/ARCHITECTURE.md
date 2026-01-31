# Architecture

**Analysis Date:** 2026-01-30

## Pattern Overview

**Overall:** Layered Architecture with Dependency Injection (Inversify) and Modular Service Pattern

**Key Characteristics:**
- Express.js REST API server with explicit module-level dependency injection containers
- Modular service-oriented design with feature-based module organization
- Inversify IoC container pattern with per-module DI configuration
- Middleware pipeline for cross-cutting concerns (auth, validation, error handling)
- Event-driven asynchronous communication via Kafka producers
- Multi-database support: MongoDB (primary), ArangoDB (knowledge graphs), Redis (caching/sessions), etcd/Redis KV store (configuration)

## Layers

**Request Entry Layer:**
- Purpose: Accept HTTP requests, apply security and validation middleware
- Location: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\app.ts`
- Contains: Express app setup, middleware pipeline, route registration
- Depends on: All service modules, middleware implementations, error handling
- Used by: Node.js runtime at startup via `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\index.ts`

**Route Layer:**
- Purpose: Define HTTP endpoints and request routing per module
- Location: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\*/routes\` (e.g., `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\auth\routes\userAccount.routes.ts`)
- Contains: Route definitions using Express Router, middleware attachment, schema validation setup
- Depends on: Controller layer, middleware implementations, DI containers
- Used by: Application main router configuration in `app.ts`

**Controller/Handler Layer:**
- Purpose: Process requests, orchestrate business logic, format responses
- Location: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\*/controller\` (e.g., `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\enterprise_search\controller\es_controller.ts`)
- Contains: HTTP request handlers, request validation, response building
- Depends on: Service layer, models, utilities, error classes
- Used by: Routes layer

**Service Layer:**
- Purpose: Implement business logic, coordinate operations across data sources
- Location: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\*/services\` (e.g., `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\auth\services\iam.service.ts`)
- Contains: Domain logic, data access orchestration, third-party service integration
- Depends on: Models, data sources, utilities, infrastructure services
- Used by: Controllers, other services, event producers

**Data Access Layer:**
- Purpose: Interact with databases and external data sources
- Location: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\*/schema\` (Mongoose models), database services in `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\services\`
- Contains: Mongoose schemas, Arango queries, data persistence operations
- Depends on: Database connection services (MongoService, ArangoService)
- Used by: Service layer

**Infrastructure Services Layer:**
- Purpose: Provide cross-cutting infrastructure (logging, databases, caching, events)
- Location: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\services\` (e.g., `logger.service.ts`, `mongo.service.ts`, `redis.service.ts`, `arango.service.ts`)
- Contains: Database connections, caching, message queues, logging
- Depends on: Configuration, external libraries
- Used by: Service layer, containers, middleware

**Dependency Injection Container Layer:**
- Purpose: Manage service instantiation and dependency resolution per module
- Location: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\*/container\` (e.g., `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\tokens_manager\container\token-manager.container.ts`)
- Contains: Inversify container setup, service binding, lifecycle management
- Depends on: All service classes within the module
- Used by: Application initialization, route handlers via middleware

**Middleware Layer:**
- Purpose: Cross-cutting request/response processing
- Location: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\middlewares\` (e.g., `auth.middleware.ts`, `error.middleware.ts`, `validation.middleware.ts`)
- Contains: Authentication, authorization, validation, error handling, request context
- Depends on: Services (Logger, AuthTokenService), error classes
- Used by: Request pipeline in `app.ts`

## Data Flow

**Typical Request Flow:**

1. HTTP request arrives → Express request pipeline
2. Global middlewares process: `requestContextMiddleware`, CORS, XSS sanitization, rate limiting
3. Module-specific route handler matched
4. Route-level middleware executes: `attachContainerMiddleware` injects DI container, auth middleware validates token, `ValidationMiddleware` validates request schema
5. Controller method invoked with container context
6. Controller retrieves services from DI container: `container.get<ServiceType>('ServiceType')`
7. Service executes business logic: queries data layer, calls external services, publishes events
8. Response formatted by controller
9. Response middleware (if any) processes: error middleware catches exceptions
10. HTTP response sent to client

**Authentication Flow:**

1. User sends credentials to `/api/v1/userAccount/initAuth` endpoint
2. `UserAccountController.initAuth()` processes request
3. `IamService` verifies credentials, creates session in `SessionService`
4. JWT token generated via `AuthTokenService`
5. Subsequent requests include JWT in Authorization header
6. `AuthMiddleware` validates token using `AuthTokenService.verifyToken()`
7. Authenticated user context injected into request via `req.user`

**Knowledge Base/Search Flow:**

1. User initiates search or starts conversation at `/api/v1/conversations` or `/api/v1/search`
2. `es_controller.ts` handles request (enterprise search module)
3. Controller builds search context using utilities in `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\enterprise_search\utils\utils.ts`
4. Semantic search or AI conversation command executed via `AIServiceCommand`
5. Search results retrieved from Arango (knowledge base) or AI service invoked
6. Citations built from results
7. Response streamed to client via SSE (Server-Sent Events) or returned in response body
8. Conversation saved to MongoDB via `Conversation` schema
9. Events published to Kafka topics for async processing

**Configuration Flow:**

1. Application initializes configuration from sources (env vars, Redis, etcd)
2. `loadAppConfig()` in tokens_manager module loads core configuration
3. `loadConfigurationManagerConfig()` loads KV store configuration
4. Pre-init migration: `checkAndMigrateIfNeeded()` migrates config from etcd to Redis if needed
5. Containers initialize with loaded configuration
6. Services access configuration from DI container during initialization

**State Management:**

- **Session State:** Redis via `SessionService`, key-value store for user sessions
- **Configuration State:** Redis or etcd (configurable) via `KeyValueStoreService` for application configuration
- **Document State:** MongoDB for conversations, messages, user data; ArangoDB for knowledge base structure
- **Cache State:** Redis for frequently accessed data
- **Event State:** Kafka topics for asynchronous event propagation between services

## Key Abstractions

**Module Container Pattern:**
- Purpose: Encapsulate service creation and dependency resolution per feature module
- Examples: `TokenManagerContainer`, `AuthServiceContainer`, `KnowledgeBaseContainer`, `EnterpriseSearchAgentContainer`
- Pattern: Static `initialize()` method returns configured Inversify `Container` instance; used to bind services, initialize resources, register dependencies
- Located in: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\*/container\*`

**Service Command Pattern:**
- Purpose: Encapsulate external service invocations (AI, IAM, storage) with abstraction layer
- Examples: `AIServiceCommand`, `IAMServiceCommand`, `StorageServiceCommand`
- Pattern: Implements `ICommand` interface; used via `execute()` method with options object
- Located in: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\commands\`

**Error Hierarchy:**
- Purpose: Type-safe error handling with HTTP status code mapping
- Examples: `BaseError`, `BadRequestError`, `UnauthorizedError`, `InternalServerError`, domain-specific errors
- Pattern: Custom error classes extend `BaseError` with code, message, statusCode, metadata; caught by `ErrorMiddleware`
- Located in: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\errors\`

**Middleware Pipeline:**
- Purpose: Modular request/response processing without tangling controller logic
- Examples: `attachContainerMiddleware`, `authSessionMiddleware`, `ValidationMiddleware`, `ErrorMiddleware`
- Pattern: Express middleware functions attached in specific order in routes or app setup
- Located in: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\middlewares\` and module-specific middleware

**Event Producer Pattern:**
- Purpose: Async event publishing to Kafka for inter-service communication
- Examples: `TokenEventProducer`, `RecordsEventProducer`, `EntitiesEventProducer`
- Pattern: Producers initialized during container setup, `start()` connects to Kafka, `produce()` publishes events
- Located in: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\*/services\*_events.service.ts`

## Entry Points

**Application Startup:**
- Location: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\index.ts`
- Triggers: Node.js process start
- Responsibilities: Load env vars, instantiate Application class, execute pre-init migration, initialize services, start HTTP server, setup graceful shutdown handlers

**Application Class:**
- Location: `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\app.ts`
- Triggers: Called from `index.ts`
- Responsibilities: Initialize all module containers, configure Express middleware and routes, bind Prometheus service, setup API documentation, initialize notification service, start HTTP server

**HTTP Routes:**
- Location: All endpoints under `/api/v1/*` mapped from route files in `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\modules\*/routes\`
- Examples: `/api/v1/userAccount/*` (auth), `/api/v1/conversations` (enterprise search), `/api/v1/knowledgeBase/*` (knowledge base)
- Responsibilities: Route matching, middleware application, controller invocation

## Error Handling

**Strategy:** Centralized error middleware with custom error hierarchy and HTTP status code mapping

**Patterns:**
- Custom error classes extending `BaseError` with code, message, statusCode properties
- Controllers throw typed errors (e.g., `BadRequestError`, `UnauthorizedError`)
- `ErrorMiddleware.handleError()` catches all errors at end of pipeline
- Error response sanitized to remove stack traces from client responses
- Error logged with context (request info, metadata) via Logger service
- Development mode may include metadata; production never exposes stack traces
- Unhandled promise rejections and uncaught exceptions caught at process level and logged

## Cross-Cutting Concerns

**Logging:**
- Winston-based logger (`Logger` singleton in `c:\Users\isuru\roo_code\AktorAI\knowledge-hub\backend\nodejs\apps\src\libs\services\logger.service.ts`)
- Configured per service with metadata (service name, timestamps)
- Transports: File (error.log, combined.log), Console (dev only)
- Structured logging with metadata and context

**Validation:**
- Zod schema validation in routes via `ValidationMiddleware`
- Request body/query/params validated against defined schemas before controller execution
- Type-safe validation with clear error messages
- Prevents malformed requests from reaching business logic

**Authentication:**
- JWT token validation via `AuthMiddleware` and `AuthTokenService`
- Token verified, decoded to extract user context
- User context attached to request object: `req.user`
- Token scopes checked for permission enforcement via `TokenScopes` enum

**Authorization:**
- Role-based access control (RBAC) checked within controllers/services
- Organization/workspace isolation enforced at service layer
- Fine-grained permissions managed in modules (e.g., knowledge base sharing permissions)

**Request Context:**
- `requestContextMiddleware` sets up AsyncLocalStorage for request-scoped context
- Allows services to access request context without threading through function parameters
- Useful for request IDs, user context, correlation tracking
