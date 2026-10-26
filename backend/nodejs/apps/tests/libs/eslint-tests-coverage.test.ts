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
    fs.writeFileSync(probe, 'const unusedProbe = 1;\n');
    try {
      const [result] = await new ESLint({ cwd: appRoot }).lintFiles([probe]);
      expect(result.messages.filter((m) => m.fatal), 'parse errors').to.deep.equal([]);
      // The type-aware rules run on it, so a real mistake in a new test is reported.
      expect(result.messages.map((m) => m.ruleId)).to.include('@typescript-eslint/no-unused-vars');
    } finally {
      fs.rmSync(probe, { force: true });
    }
  });
});
