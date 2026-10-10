import { expect } from 'chai';
import Ajv from 'ajv';
import * as fs from 'fs';
import * as path from 'path';

const CONTRACTS_DIR = path.resolve(__dirname, '../../../../../contracts');

const readJson = (file: string): unknown => JSON.parse(fs.readFileSync(file, 'utf8'));

const contractNames = fs
  .readdirSync(CONTRACTS_DIR, { withFileTypes: true })
  .filter((d) => d.isDirectory())
  .map((d) => d.name);

interface Examples {
  [direction: string]: { valid: unknown[]; invalid: unknown[] };
}

describe('contract schemas (PH02-23)', () => {
  it('finds at least one contract', () => {
    expect(contractNames).to.include('attachments-validate');
  });

  for (const name of contractNames) {
    const examples = readJson(path.join(CONTRACTS_DIR, name, 'examples.json')) as Examples;
    for (const [direction, cases] of Object.entries(examples)) {
      describe(`${name} ${direction}`, () => {
        const ajv = new Ajv({ allErrors: true });
        const validate = ajv.compile(
          readJson(path.join(CONTRACTS_DIR, name, `${direction}.schema.json`)) as object,
        );

        it('has valid and invalid examples', () => {
          expect(cases.valid.length).to.be.greaterThan(0);
          expect(cases.invalid.length).to.be.greaterThan(0);
        });

        cases.valid.forEach((payload, i) => {
          it(`accepts valid example ${i}`, () => {
            expect(validate(payload), JSON.stringify(validate.errors)).to.equal(true);
          });
        });

        cases.invalid.forEach((payload, i) => {
          it(`rejects invalid example ${i}`, () => {
            expect(validate(payload)).to.equal(false);
          });
        });
      });
    }
  }
});
