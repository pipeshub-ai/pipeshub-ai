import 'reflect-metadata';
import { expect } from 'chai';
import { readdirSync, readFileSync } from 'fs';
import { join } from 'path';
import { schemaRequirements } from '../../../src/libs/repositories/doc-store';

// A revision with min_code_epoch above this build's number makes the API refuse to start on PostgreSQL.
describe('doc-store schema requirements follow the migrations', () => {
  const versions = join(__dirname, '../../../../../db/versions');

  for (const [branch, { code }] of Object.entries(schemaRequirements())) {
    it(`${branch}: the code epoch reaches every min_code_epoch and names a revision that exists`, () => {
      const files = readdirSync(join(versions, branch)).filter((f) => /^\d{4}_.*\.py$/.test(f));
      const minimum = Math.max(
        0,
        ...files.map((f) => Number(/^min_code_epoch = (\d+)/m.exec(readFileSync(join(versions, branch, f), 'utf8'))?.[1] ?? 0)),
      );
      expect(code).to.be.at.least(minimum);
      expect(code).to.be.at.most(Math.max(...files.map((f) => Number(f.slice(0, 4)))));
    });
  }
});
