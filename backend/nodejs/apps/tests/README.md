# Node.js Backend Unit Tests

Unit tests for the PipesHub Node.js backend service using **Mocha** (test runner) and **c8** (code coverage).

## Prerequisites

```bash
cd backend/nodejs/apps
npm install
```

## Running Tests

```bash
# Run all unit tests (parallel, 4 workers)
npm run test

# Run tests with detailed coverage report (text + lcov + html)
npm run test:coverage

# Run tests with coverage thresholds (90% lines/functions/statements, 80% branches)
npm run test:coverage-check

# Run in CI mode (coverage thresholds + text + lcov output)
npm run test:ci
```

## Running Specific Tests

```bash
# Run a specific test file
npx mocha --require ts-node/register tests/libs/utils/password.utils.test.ts

# Run all tests in a directory
npx mocha --require ts-node/register 'tests/libs/keyValueStore/**/*.test.ts'

# Run tests matching a grep pattern
npx mocha --require ts-node/register --grep "should encrypt"
```

## Directory Structure

```
tests/
├── setup.ts                          # Global test setup (runs before all tests)
├── app.test.ts                       # App-level integration tests
├── index.test.ts                     # Entry point tests
├── helpers/                          # Shared test utilities
├── utils/                            # Utility function tests
│   ├── generic-functions.test.ts
│   ├── safe-integer.test.ts
│   └── xss-sanitization.test.ts
├── libs/                             # Library tests
│   ├── encryptor/                    # Encryption utility tests
│   ├── enums/                        # Enum definition tests
│   ├── keyValueStore/                # KV store tests (Redis, etcd, in-memory)
│   ├── middlewares/                  # Middleware tests (auth, rate-limit, etc.)
│   └── types/                        # Type validation tests
└── modules/                          # Business module tests
```

## Conventions

- Test files use the `*.test.ts` naming convention
- Tests are written in TypeScript and transpiled via `ts-node` with `tsconfig.test.json`
- Tests run in parallel by default (4 workers) for speed
- Coverage thresholds: **90%** lines/functions/statements, **80%** branches

## Coverage Reports

After running `npm run test:coverage`, reports are generated in:
- Terminal: text summary
- `coverage/lcov-report/index.html`: interactive HTML report
- `coverage/lcov.info`: lcov format (for CI integrations)
