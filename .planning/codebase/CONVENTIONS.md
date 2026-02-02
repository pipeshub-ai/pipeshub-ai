# Coding Conventions

**Analysis Date:** 2026-01-30

## Naming Patterns

**Files:**
- Backend services: `*.service.ts` (e.g., `iam.service.ts`, `mail.service.ts`)
- Controllers: `*.controller.ts` (e.g., `users.controller.ts`, `org.controller.ts`)
- Routes: `*.routes.ts` (e.g., `users.routes.ts`, `saml.routes.ts`)
- Middleware: `*.middleware.ts` (e.g., `auth.middleware.ts`, `error.middleware.ts`)
- Schemas: `*.schema.ts` (e.g., `users.schema.ts`, `userCredentials.schema.ts`)
- Containers (DI): `*.container.ts` (e.g., `authService.container.ts`)
- Utilities: `*.utils.ts` (e.g., `validation.utils.ts`, `password.utils.ts`)
- Constants: `*.constant.ts` (e.g., `KeyValueStoreType.ts`)
- Enums: `*.enum.ts` (e.g., `http-status.enum.ts`)
- Errors: `*.error.ts` or `*.errors.ts` (e.g., `base.error.ts`, `http.errors.ts`)
- Frontend components: kebab-case with `.tsx` (e.g., `form-divider.tsx`, `custom-breadcrumbs.tsx`)
- Frontend hooks: `use-*.ts` pattern (e.g., `use-scroll-to-top.ts`, `use-boolean.ts`)
- Styles files: `styles.tsx` (e.g., in editor, custom-popover directories)

**Functions:**
- camelCase naming convention
- Async handlers: `async function handleAction(req, res)` pattern
- Utility functions: descriptive action verbs (e.g., `validateNoXSS()`, `formatZodError()`)
- Factory functions: `createRouter()`, `createContainer()` pattern

**Variables:**
- camelCase for all variables and constants
- Use `const` by default, `let` for reassignment
- UPPERCASE for true constants only (rare usage)

**Types:**
- Interfaces: PascalCase, no `I` prefix (e.g., `ErrorMetadata`, `LogoProps`)
- Types: PascalCase (e.g., `AuthenticatedUserRequest`, `ValidationErrorDetail`)
- Enums: PascalCase (e.g., `HTTP_STATUS`, `TokenScopes`)
- Request/Response types: suffix with `Request`, `Response` (e.g., `AuthenticatedUserRequest`)

## Code Style

**Formatting:**
- Tool: Prettier 3.x
- Line width: 80 characters
- Semicolons: true (enforced)
- Single quotes: true (enforced via prettier/prettier rule)
- Trailing commas: es5 (default Prettier behavior)

**Linting:**

Backend (`backend/nodejs/apps/.eslintrc.json`):
- Parser: @typescript-eslint/parser
- Base config: eslint:recommended, @typescript-eslint/recommended
- Integration: prettier/recommended (for conflict resolution)
- Rule enforcement: prettier/prettier set to error level

Frontend (`frontend/.eslintrc.cjs`):
- Parser: @typescript-eslint/parser with project-based type checking
- Base configs: airbnb, airbnb-typescript, airbnb/hooks, prettier
- Plugins: perfectionist, unused-imports, @typescript-eslint, prettier
- Sort imports using perfectionist plugin with custom groups
- Custom ESLint groups: custom-mui, custom-routes, custom-hooks, custom-utils, custom-components, custom-sections, custom-auth, custom-types

**TypeScript Configuration:**

Backend (`backend/nodejs/apps/tsconfig.json`):
- Target: es2016
- Module: commonjs
- Strict: true (all strict flags enabled)
- noUnusedLocals: true
- noUnusedParameters: true
- noImplicitReturns: true
- Decorators: experimentalDecorators and emitDecoratorMetadata enabled (for inversify DI)
- rootDir: src
- outDir: dist
- Included: src/**, shared libs

Frontend (`frontend/tsconfig.json`):
- Target: ESNext
- Module: ESNext
- JSX: react-jsx
- Strict: true
- noEmit: true
- moduleResolution: Node

## Import Organization

**Order (Backend - Zod validation example from `users.routes.ts`):**
1. External libraries (express, zod, inversify)
2. Error imports (HTTP errors)
3. Service/middleware imports (Logger, validators)
4. Schema/type imports (database schemas)
5. Local middleware/utility imports
6. Controller imports

**Order (Frontend - using perfectionist plugin):**
1. Style imports
2. Type imports
3. Builtin/external imports
4. Custom groups in this order:
   - custom-mui (MUI components)
   - custom-routes (routing)
   - custom-hooks (custom hooks)
   - custom-utils (utilities)
   - internal (other src files)
   - custom-components (component imports)
   - custom-sections (section imports)
   - custom-auth (auth imports)
   - custom-types (type imports)
   - relative imports (parent, sibling, index)

**Path Aliases:**
- Frontend: `src/*` resolved to `./src` (baseUrl: ".")
- No @ prefix in use (unlike many projects)

## Error Handling

**Pattern - Base Error Class:**
Location: `backend/nodejs/apps/src/libs/errors/base.error.ts`

All errors extend `BaseError` with:
- `code`: string (e.g., 'HTTP_BAD_REQUEST')
- `statusCode`: number (HTTP status)
- `metadata`: optional error details
- `timestamp`: ISO date of error
- `toJSON()`: serialization with optional stack trace (never sent to clients)

**HTTP Errors:**
Location: `backend/nodejs/apps/src/libs/errors/http.errors.ts`

Specialized error classes for each HTTP status:
- `BadRequestError(message, metadata?)` → 400
- `UnauthorizedError(message, metadata?)` → 401
- `ForbiddenError(message, metadata?)` → 403
- `NotFoundError(message, metadata?)` → 404
- `ConflictError(message, metadata?)` → 409
- `InternalServerError(message, metadata?)` → 500
- `ServiceUnavailableError(message, metadata?)` → 503

**Usage Pattern (from `iam.service.ts`):**
```typescript
try {
  const response = await axios(config);
  return { statusCode: response.status, data: response.data };
} catch (error) {
  if (axios.isAxiosError(error)) {
    throw new AxiosError(/* ... */);
  }
  throw new InternalServerError(
    error instanceof Error ? error.message : 'Unexpected error occurred',
  );
}
```

**Middleware Error Handling:**
Location: `backend/nodejs/apps/src/libs/middlewares/error.middleware.ts`

- Catches all errors in Express
- Distinguishes between `BaseError` and unknown errors
- Never exposes stack traces to clients (only logs server-side in development)
- Sanitizes circular references in error responses
- Prevents double-response if headers already sent

## Logging

**Framework:** Winston 3.x

**Service Location:** `backend/nodejs/apps/src/libs/services/logger.service.ts`

**Patterns:**
- Inject Logger via inversify: `@inject('Logger') private logger: Logger`
- Use `logger.info()`, `logger.warn()`, `logger.error()`, `logger.debug()`
- Include context object as second parameter: `logger.error('Failed to...', { error: message })`
- Service context: `const loggerConfig = { service: 'Application' }`
- Structured logging for error details: include request context, metadata

**Usage Example (from `app.ts`):**
```typescript
this.logger = new Logger(loggerConfig);
this.logger.info('Application initialized successfully');
this.logger.error('Failed to initialize application', error.stack);
```

## Comments

**When to Comment:**
- Complex business logic requiring explanation
- Non-obvious algorithmic decisions
- Workarounds and their justifications
- TODO/FIXME with context (example: `// TODO: Initialize Logger separately and not in token manager`)

**JSDoc/TSDoc:**
- Used sparingly, mainly for public APIs
- Decorators documented in inversify usage
- Error metadata documented in type definitions

**Examples from codebase:**
```typescript
// Run migration from etcd to Redis BEFORE loading app config.
// This ensures secrets exist in Redis before we try to read them.
async preInitMigration(): Promise<void> { }

// Only include stack trace if explicitly requested (for server-side logging only)
// Never expose stack traces to clients - security best practice
if (includeStack) {
  json.stack = this.stack;
}
```

## Function Design

**Size:** Aim for single responsibility, typically under 50 lines

**Parameters:**
- Use destructuring for objects with multiple props (Request, Response are exceptions)
- No deep parameter nesting
- Optional params at end
- Type all parameters explicitly (strict TypeScript)

**Return Values:**
- Explicit return types required (`noImplicitReturns: true`)
- Return void for middleware/handlers
- Return objects for services: `{ statusCode: number, data: any }`
- Never rely on implicit undefined returns

**Examples:**

Async handler pattern (from `users.controller.ts`):
```typescript
async getAllUsers(
  req: AuthenticatedUserRequest,
  res: Response,
): Promise<void> {
  const users = await Users.find({ orgId: req.user?.orgId, isDeleted: false })
    .select('-email')
    .lean()
    .exec();
  res.json(users);
}
```

Service method pattern (from `iam.service.ts`):
```typescript
async createOrg(orgData: any, authServiceToken: string) {
  try {
    const config = { /* ... */ };
    const response = await axios(config);
    return { statusCode: response.status, data: response.data };
  } catch (error) {
    // error handling
  }
}
```

## Module Design

**Exports:**
- Use named exports for services, controllers, middleware
- Use default export for route handlers: `export default function createRouter(...)`
- Re-export from index files for public APIs

**Barrel Files:**
- Not widely used; most modules import directly from specific files
- When used, consolidate related exports (e.g., all error types from errors directory)

**Dependency Injection:**
- Backend uses inversify containers for service management
- Services marked with `@injectable()` decorator
- Dependencies injected via `@inject('ServiceName')` in constructor
- Containers: `ServiceContainer.initialize()` → `async initialize()` pattern

**Example (from app.ts):**
```typescript
this.tokenManagerContainer = await TokenManagerContainer.initialize(
  configurationManagerConfig,
);

this.app.use('/api/v1/users', createUserRouter(this.entityManagerContainer));
```

## Code Organization Layers (Backend)

**Routes Layer** (`src/modules/*/routes/*.routes.ts`):
- Express router creation
- Validation schemas (Zod)
- Middleware attachment
- Route path definition

**Controller Layer** (`src/modules/*/controller/*.controller.ts`):
- Request/response handling
- @injectable() decorated classes
- Calls services for business logic
- Error throwing (caught by error middleware)

**Service Layer** (`src/modules/*/services/*.service.ts`):
- Business logic
- Database queries
- External API calls
- @injectable() decorated classes

**Schema Layer** (`src/modules/*/schema/*.schema.ts`):
- Mongoose schemas
- Data models

**Middleware Layer** (`src/libs/middlewares/`):
- Cross-cutting concerns
- Authentication, validation, rate limiting, XSS sanitization
- Error handling

---

*Convention analysis: 2026-01-30*
