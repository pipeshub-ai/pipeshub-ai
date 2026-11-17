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
