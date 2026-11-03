/**
 * Mocha root hooks: one line per test on stderr, so a log (the xunit reporter writes only to its file) names the test that is running or hung.
 * The writer is taken when this file loads: a test that replaces process.stderr.write and then times out must not swallow the lines after it.
 */
const write = process.stderr.write.bind(process.stderr);

export const mochaHooks = {
  beforeEach(this: Mocha.Context): void {
    write(`START ${this.currentTest?.fullTitle() ?? '?'}\n`);
  },
  afterEach(this: Mocha.Context): void {
    const t = this.currentTest;
    write(`${(t?.state ?? 'unknown').toUpperCase()} (${String(t?.duration ?? 0)} ms) ${t?.fullTitle() ?? '?'}\n`);
  },
};
