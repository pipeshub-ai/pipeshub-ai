/**
 * Loaded via .mocharc.yaml `require`: root hooks for every test file.
 *
 * `Logger.getInstance()` is one root per process (named loggers inherit from it), and modules keep the instance they
 * were given at load. A test that
 * assigns `instance.warn = ...` (rather than stubbing `Logger.prototype`) leaves an own property that shadows the class
 * method for every later file in the same mocha worker, so a later `sinon.stub(Logger.prototype, 'warn')` never sees a
 * call. Such a test is named, the shared logger is put back so that the tests after it are spared, and the run fails once, when the tests are done.
 */
import path from 'node:path';

const LEVELS = ['error', 'warn', 'info', 'debug'];
const offenders: string[] = [];
const loggerModule = (): { Logger?: { instance?: object } } | undefined => {
  const key = Object.keys(require.cache).find((k) =>
    k.endsWith(path.join('libs', 'services', 'logger.service.ts')),
  );
  return key ? (require.cache[key]?.exports as { Logger?: { instance?: object } }) : undefined;
};

type LoggerLike = Record<string, unknown> & { children?: Map<string, LoggerLike> };

export const mochaHooks = {
  afterEach(this: Mocha.Context): void {
    const root = loggerModule()?.Logger?.instance as LoggerLike | undefined;
    if (!root) return;
    // Every named logger inherits from the root, so a plain assignment on any of them shadows the class method too.
    for (const instance of [root, ...(root.children?.values() ?? [])]) {
      // A sinon stub is restored by whoever made it (often in an `after` hook); a plain assignment never is.
      const replaced = LEVELS.filter(
        (level) =>
          Object.prototype.hasOwnProperty.call(instance, level) &&
          !(instance[level] as { isSinonProxy?: boolean } | undefined)?.isSinonProxy,
      );
      if (replaced.length === 0) continue;
      for (const level of replaced) delete instance[level];
      // Not thrown here: a failing afterEach hook makes mocha skip the rest of the suite. It is reported once, below.
      offenders.push(
        `"${this.currentTest?.fullTitle()}" left ${replaced.join(', ')} replaced on the shared logger instance; ` +
          "stub Logger.prototype (sinon restores it) instead of assigning to the instance",
      );
    }
  },
  afterAll(): void {
    if (offenders.length === 0) return;
    throw new Error(offenders.splice(0).join('\n'));
  },
};
