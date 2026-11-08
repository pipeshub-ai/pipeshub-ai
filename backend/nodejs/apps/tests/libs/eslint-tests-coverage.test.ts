import { expect } from 'chai';
import { ESLint } from 'eslint';
import fs from 'node:fs';
import path from 'node:path';

const appRoot = path.resolve(__dirname, '../..');

describe('ESLint covers tests/', function () {
  this.timeout(180000);

  it('lints a new file under tests/ instead of failing to parse it', async () => {
    // A file that exists only for this run, so the check holds for any new test file.
    const probe = path.join(appRoot, 'tests', 'libs', `lint-probe-${String(process.pid)}.ts`);
    // Style findings tests do on purpose (an untyped catch callback, a number in a template) stay quiet; an unused variable does not.
    fs.writeFileSync(
      probe,
      'const unusedProbe = 1;\nexport const text = (n: number): string => `${n}`;\nexport const settled = Promise.resolve().catch((e) => e);\n',
    );
    try {
      const [result] = await new ESLint({ cwd: appRoot }).lintFiles([probe]);
      expect(result.messages.filter((m) => m.fatal), 'parse errors').to.deep.equal([]);
      // The type-aware rules run on it, so a real mistake in a new test is reported.
      const rules = result.messages.map((m) => m.ruleId);
      expect(rules).to.include('@typescript-eslint/no-unused-vars');
      expect(rules).to.not.include('@typescript-eslint/use-unknown-in-catch-callback-variable');
      expect(rules).to.not.include('@typescript-eslint/restrict-template-expressions');
    } finally {
      fs.rmSync(probe, { force: true });
    }
  });
});
