import sinon from 'sinon';
import { inspect } from 'node:util';

export interface MockLogger {
  info: sinon.SinonStub;
  error: sinon.SinonStub;
  warn: sinon.SinonStub;
  debug: sinon.SinonStub;
  logRequest: sinon.SinonStub;
  updateDefaultMeta: sinon.SinonStub;
}

export function createMockLogger(): MockLogger {
  return {
    info: sinon.stub(),
    error: sinon.stub(),
    warn: sinon.stub(),
    debug: sinon.stub(),
    logRequest: sinon.stub(),
    updateDefaultMeta: sinon.stub(),
  };
}

const LOG_LEVELS = ['error', 'warn', 'info', 'debug'] as const;
// util.inspect cuts arrays after 100 items and strings after 10,000 characters by default; a user list must be searched whole.
const INSPECT_ALL = { depth: null, maxArrayLength: null, maxStringLength: null, breakLength: Infinity };

/**
 * Fails when any of `values` reached a log call on `logger` (a MockLogger, or a real logger whose level methods
 * are sinon stubs). Arguments are rendered whole with util.inspect, so an Error's message, nested meta and every
 * item of a long list are searched; JSON.stringify would turn an Error into {}. Matching ignores case. Pass the email whole and the first and last
 * names separately (docs/logging.md, "Personal data"). Fails when there were no log calls, so a silenced logger
 * cannot pass.
 */
export function assertNotLogged(
  logger: Partial<Record<(typeof LOG_LEVELS)[number], sinon.SinonStub>>,
  ...values: string[]
): void {
  const lines = LOG_LEVELS.flatMap((level) =>
    (logger[level]?.getCalls() ?? []).map(
      (call) => `${level} ${inspect(call.args, INSPECT_ALL)}`,
    ),
  );
  if (lines.length === 0) throw new Error('the logger got no log calls');
  for (const value of values) {
    const hit = lines.find((line) => line.toLowerCase().includes(value.toLowerCase()));
    if (hit !== undefined) throw new Error(`${JSON.stringify(value)} was logged: ${hit}`);
  }
}

const SNAKE_CASE_KEY = /^[a-z0-9_]+$/;

/**
 * Fails when a log call on `logger` passed a meta object with a key that is not snake_case (docs/logging.md,
 * "Conventions": field names are snake_case in Python and Node). Only the meta's own top-level keys are checked;
 * an Error or a non-object meta is skipped.
 */
export function assertSnakeCaseLogKeys(
  logger: Partial<Record<(typeof LOG_LEVELS)[number], sinon.SinonStub>>,
): void {
  for (const level of LOG_LEVELS) {
    for (const call of logger[level]?.getCalls() ?? []) {
      const meta: unknown = call.args[1];
      if (meta === null || typeof meta !== 'object' || meta instanceof Error) continue;
      const bad = Object.keys(meta).filter((key) => !SNAKE_CASE_KEY.test(key));
      if (bad.length > 0) {
        throw new Error(`${level} ${JSON.stringify(call.args[0])} has keys that are not snake_case: ${bad.join(', ')}`);
      }
    }
  }
}

/**
 * assertSnakeCaseLogKeys for every test of a describe: `check(this, logger…)` in its afterEach records a failure under
 * the test's title, and `report()` in its `after` throws them all once. Not thrown in afterEach: a failing afterEach
 * hook makes mocha skip the rest of the suite and its cleanup (tests/helpers/logger-isolation-global.ts).
 */
export function createSnakeCaseLogKeyGuard(): {
  check(context: Mocha.Context, ...loggers: Parameters<typeof assertSnakeCaseLogKeys>[0][]): void;
  report(): void;
} {
  const failures: string[] = [];
  return {
    check(context, ...loggers) {
      for (const logger of loggers) {
        try {
          assertSnakeCaseLogKeys(logger);
        } catch (error) {
          failures.push(`"${context.currentTest?.fullTitle()}": ${(error as Error).message}`);
        }
      }
    },
    report() {
      if (failures.length > 0) throw new Error(failures.splice(0).join('\n'));
    },
  };
}
