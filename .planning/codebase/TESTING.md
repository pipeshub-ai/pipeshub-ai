# Testing Patterns

**Analysis Date:** 2026-01-30

## Test Framework

**Runner:**
- Mocha 12.x (beta)
- Config: Not explicitly configured; uses package.json `"test": "mocha"` command
- Alternative test tools available but not configured: Jest (types installed), Vitest (not found)

**Assertion Library:**
- Chai 5.x (BDD-style assertions: expect/should)
- Supporting package: @types/chai 5.x

**Mocking & Stubbing:**
- Sinon 19.x (spies, stubs, mocks)
- Types: @types/sinon 17.x

**Run Commands:**
```bash
npm test                    # Run all tests via Mocha
npm run lint              # ESLint validation
npm run format            # Prettier formatting
npm run lint:fix          # Auto-fix ESLint issues
npm run dev               # Watch mode for development
```

**Current Status:**
- No test files found in codebase (`*.test.ts`, `*.spec.ts`)
- Test infrastructure is configured but NOT currently in use
- All testing tools are installed as devDependencies but inactive

## Test File Organization

**Location (Recommended):**
- Co-located pattern: `*.test.ts` or `*.spec.ts` next to source files
- Alternative: Separate `__tests__` directory (not currently used)

**Naming Convention:**
- For file `src/libs/services/logger.service.ts` → `src/libs/services/logger.service.test.ts`
- For file `src/modules/auth/services/iam.service.ts` → `src/modules/auth/services/iam.service.test.ts`

**Not Yet Established:**
- No existing test structure to reference
- New tests should follow above naming pattern

## Test Structure

**Expected Suite Organization (based on installed tools):**

Using Mocha + Chai:
```typescript
import { expect } from 'chai';
import sinon from 'sinon';
import { IamService } from '../services/iam.service';

describe('IamService', () => {
  let service: IamService;
  let axiosStub: sinon.SinonStub;

  beforeEach(() => {
    // Setup
    service = new IamService(mockConfig, mockLogger);
    axiosStub = sinon.stub(axios, 'post');
  });

  afterEach(() => {
    // Cleanup
    sinon.restore();
  });

  describe('createOrg', () => {
    it('should create organization successfully', async () => {
      const orgData = { /* ... */ };
      axiosStub.resolves({ status: 201, data: { id: 'org-1' } });

      const result = await service.createOrg(orgData, 'token');

      expect(result.statusCode).to.equal(201);
      expect(result.data.id).to.equal('org-1');
      expect(axiosStub.calledOnce).to.be.true;
    });

    it('should throw AxiosError on API failure', async () => {
      axiosStub.rejects(new Error('API failed'));

      try {
        await service.createOrg({}, 'token');
        expect.fail('Should have thrown error');
      } catch (error) {
        expect(error).to.be.instanceOf(AxiosError);
      }
    });
  });
});
```

**Patterns:**
- Setup: `beforeEach()` initializes test state
- Teardown: `afterEach()` cleans up stubs and mocks
- Assertions: Chai `expect()` BDD syntax
- Test naming: Descriptive, starts with "should"

## Mocking

**Framework:** Sinon

**Patterns:**

Stub external dependencies:
```typescript
import sinon from 'sinon';
import axios from 'axios';

// Stub axios globally
sinon.stub(axios, 'post').resolves({ status: 201, data: {} });

// Or stub specific instance
const axiosStub = sinon.stub(axios, 'get');
axiosStub.withArgs('/users').resolves({ data: [] });
```

Mock injected services:
```typescript
const mockLogger = {
  info: sinon.stub(),
  error: sinon.stub(),
  warn: sinon.stub(),
  debug: sinon.stub(),
};

const service = new IamService(mockConfig, mockLogger);
```

Spy on methods:
```typescript
const spy = sinon.spy(service, 'createOrg');
await service.createOrg(data, token);
expect(spy.calledOnce).to.be.true;
expect(spy.getCall(0).args[0]).to.deep.equal(expectedData);
```

**What to Mock:**
- External HTTP calls (axios/fetch)
- Database operations (mongoose)
- Logger (in unit tests)
- Third-party service calls
- File system operations

**What NOT to Mock:**
- Core JavaScript objects/methods
- TypeScript types
- Validation logic (test real validation)
- Error classes (test real error throwing)
- Business logic core implementations (integration tests)

## Fixtures and Factories

**Test Data (To be established):**

Recommended pattern for common test data:
```typescript
// src/libs/services/__fixtures__/logger.fixture.ts
export const mockLoggerConfig = {
  service: 'TestService',
};

export const createMockLogger = (overrides = {}) => ({
  info: sinon.stub(),
  error: sinon.stub(),
  warn: sinon.stub(),
  debug: sinon.stub(),
  ...overrides,
});
```

Recommended pattern for model factories:
```typescript
// src/modules/user_management/__fixtures__/user.factory.ts
export const createMockUser = (overrides = {}) => ({
  _id: 'user-123',
  email: 'user@example.com',
  fullName: 'Test User',
  orgId: 'org-123',
  isDeleted: false,
  ...overrides,
});
```

**Location:**
- `src/modules/*/fixtures/` or `src/libs/*/__fixtures__/`
- Or co-located in test file if single usage

## Coverage

**Requirements:**
- Not enforced (no coverage thresholds in package.json)
- Can be configured via Mocha: `mocha --reporter=html-cov`

**View Coverage (Once Configured):**
```bash
# Would require adding to package.json:
# "test:coverage": "nyc mocha" or "c8 mocha"
```

**Current Status:**
- Coverage tooling not installed (nyc/c8)
- Should be added if coverage tracking is required

## Test Types

**Unit Tests (Recommended First):**
- Scope: Single service/function in isolation
- Dependencies: Mocked with Sinon
- Example: Test `ValidationUtils.formatZodError()` independently
- Location: Next to source files (`.test.ts` files)
- Setup: Fast, no I/O

**Integration Tests (Not Currently Implemented):**
- Scope: Multiple services/components working together
- Dependencies: Real database or in-memory mock database
- Example: Test user creation flow with real schema validation
- Would require: Separate test database setup, test hooks
- Recommended tools: Mocha with fixtures, test containers

**E2E Tests (Not Currently Implemented):**
- Scope: Full application workflows
- Example: API request → validation → database → response
- Framework: Not selected (could use Supertest for API testing)
- Would require: Running server, test data cleanup

## Common Patterns

**Async Testing:**

Using Mocha with async/await:
```typescript
it('should create user', async () => {
  const user = await userService.createUser(userData);
  expect(user.id).to.exist;
});

// Error handling
it('should reject invalid email', async () => {
  try {
    await userService.createUser({ email: 'invalid' });
    expect.fail('Should throw');
  } catch (error) {
    expect(error).to.be.instanceOf(BadRequestError);
  }
});
```

**Error Testing:**

Testing custom error classes:
```typescript
it('should throw NotFoundError with metadata', () => {
  const error = new NotFoundError('User not found', { userId: '123' });

  expect(error).to.be.instanceOf(NotFoundError);
  expect(error.code).to.equal('HTTP_NOT_FOUND');
  expect(error.statusCode).to.equal(404);
  expect(error.metadata.userId).to.equal('123');
  expect(error.toJSON()).to.have.property('timestamp');
});
```

Testing error middleware:
```typescript
it('should return 404 for NotFoundError', async () => {
  const error = new NotFoundError('Resource not found');
  const req = {} as Request;
  const res = {
    headersSent: false,
    status: sinon.stub().returnsThis(),
    json: sinon.stub(),
  } as any;
  const next = sinon.stub();

  const middleware = ErrorMiddleware.handleError();
  middleware(error, req, res, next);

  expect(res.status.calledWith(404)).to.be.true;
});
```

**Testing Middleware:**

Pattern for middleware testing:
```typescript
it('should attach user to request', async () => {
  const req = {
    headers: { authorization: 'Bearer token' },
  } as any;
  const res = {} as Response;
  const next = sinon.spy();

  await authMiddleware(req, res, next);

  expect(req.user).to.exist;
  expect(next.calledOnce).to.be.true;
});
```

## Testing Services with Inversify DI

**Pattern for injectable services:**

```typescript
// src/modules/auth/services/iam.service.test.ts
describe('IamService', () => {
  let service: IamService;
  let mockAppConfig: AppConfig;
  let mockLogger: Logger;
  let axiosStub: sinon.SinonStub;

  beforeEach(() => {
    mockAppConfig = {
      iamBackend: 'http://iam-service',
    } as any;

    mockLogger = {
      info: sinon.stub(),
      error: sinon.stub(),
    } as any;

    // Manually instantiate without container
    service = new IamService(mockAppConfig, mockLogger);
    axiosStub = sinon.stub(axios, 'post');
  });

  afterEach(() => {
    sinon.restore();
  });

  it('should create org via IAM backend', async () => {
    const orgData = {
      contactEmail: 'test@org.com',
      registeredName: 'Test Org',
      adminFullName: 'Admin',
      sendEmail: true,
    };

    axiosStub.resolves({
      status: 201,
      data: { id: 'org-1', name: 'Test Org' },
    });

    const result = await service.createOrg(orgData, 'token');

    expect(result.statusCode).to.equal(201);
    expect(axiosStub.calledOnce).to.be.true;
  });
});
```

Note: Services can be tested without the Inversify container by manually instantiating and providing mock dependencies.

## Testing Controllers (with Inversify)

**Pattern for controller testing:**

```typescript
// src/modules/user_management/controller/users.controller.test.ts
describe('UserController', () => {
  let controller: UserController;
  let req: AuthenticatedUserRequest;
  let res: Response;
  let mockMailService: MailService;
  let mockAuthService: AuthService;
  let mockLogger: Logger;
  let mockEventService: EntitiesEventProducer;

  beforeEach(() => {
    // Create stubs for all dependencies
    mockMailService = sinon.createStubInstance(MailService);
    mockAuthService = sinon.createStubInstance(AuthService);
    mockLogger = {
      info: sinon.stub(),
      error: sinon.stub(),
    } as any;
    mockEventService = sinon.createStubInstance(EntitiesEventProducer);

    const mockConfig = {} as AppConfig;

    // Instantiate controller with mocked dependencies
    controller = new UserController(
      mockConfig,
      mockMailService,
      mockAuthService,
      mockLogger,
      mockEventService,
    );

    req = {
      user: { orgId: 'org-123', id: 'user-1' },
    } as AuthenticatedUserRequest;

    res = {
      json: sinon.stub(),
      status: sinon.stub().returnsThis(),
    } as any;
  });

  it('should get all users for org', async () => {
    sinon.stub(Users, 'find').returnsThis();
    // ... continue test
  });
});
```

## Frontend Testing (React)

**Current Status:**
- No test framework configured for React
- ESLint and TypeScript provide some type safety

**Recommended Setup (Future Implementation):**
- Framework: Vitest (modern, Vite-compatible)
- DOM Testing: React Testing Library
- Snapshots: Vitest built-in

**Example pattern (not yet in use):**
```typescript
// src/components/logo/logo.test.tsx
import { render, screen } from '@testing-library/react';
import { Logo } from './logo';

describe('Logo Component', () => {
  it('should render logo link', () => {
    render(<Logo href="/" />);

    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('href', '/');
  });
});
```

## Development Testing Workflow

**Watch Mode (Development):**
```bash
npm run dev          # Runs with nodemon for file watching
```

**Validation Before Commit:**
```bash
npm run lint         # Check code style
npm run format       # Auto-fix formatting (if needed)
```

**Manual Testing:**
- No automated tests currently run in CI/CD
- Should consider adding pre-commit hooks once tests are written
- Mocha can be integrated into CI pipelines easily

---

*Testing analysis: 2026-01-30*

## Recommendations for Future Test Coverage

1. **Start with Unit Tests:** Service layer tests using Sinon mocking
2. **Add Fixtures:** Create reusable test data factories in `__fixtures__` directories
3. **Coverage Tool:** Add nyc or c8 for coverage reporting
4. **Pre-commit Hooks:** Integrate linting and basic tests via husky
5. **API Testing:** Add Supertest for E2E HTTP testing of routes
6. **Frontend Tests:** Consider Vitest + React Testing Library for component tests
