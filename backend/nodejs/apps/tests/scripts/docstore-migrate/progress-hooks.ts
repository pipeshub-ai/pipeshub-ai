/** Mocha root hooks: one line per test on stderr, so a log (the xunit reporter writes only to its file) names the test that is running or hung. */
export const mochaHooks = {
  beforeEach(this: Mocha.Context): void {
    process.stderr.write(`START ${this.currentTest?.fullTitle() ?? '?'}\n`);
  },
  afterEach(this: Mocha.Context): void {
    const t = this.currentTest;
    process.stderr.write(`${(t?.state ?? 'unknown').toUpperCase()} (${String(t?.duration ?? 0)} ms) ${t?.fullTitle() ?? '?'}\n`);
  },
};
