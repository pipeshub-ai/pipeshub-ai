import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join, resolve } from 'path';
import { ITEM_REF_PATTERN, parseItemRef } from '../../../src/modules/control-plane/domain/item-ref';

/**
 * QA7-40 (XL-12): Node's reader of contracts/control-plane/vectors/identifiers.json,
 * which Python reads in test_cp_identifiers.py. Node parses item refs with its own
 * pattern; it must accept and refuse exactly what the schema's `ItemRef` does.
 */
const CONTROL_PLANE = join(resolve(__dirname, '..', '..', '..', '..', '..', '..'), 'contracts', 'control-plane');
const read = (name: string): unknown => JSON.parse(readFileSync(join(CONTROL_PLANE, name), 'utf8'));
const cases = (
  read(join('vectors', 'identifiers.json')) as { cases: Record<string, { valid: string[]; invalid: string[] }> }
).cases['ItemRef']!;
const schemaPattern = (read('common.schema.json') as { $defs: Record<string, { pattern: string }> }).$defs['ItemRef']!
  .pattern;

describe('QA7-40 control-plane identifiers (Node)', () => {
  it('Node’s item-ref pattern is the schema’s, with capture groups', () => {
    expect(ITEM_REF_PATTERN.source.replace(/[()]/g, '').replace(/\\\//g, '/')).to.equal(schemaPattern);
  });

  for (const ref of cases.valid) {
    it(`parses ${ref}`, () => {
      const parts = parseItemRef(ref);
      expect(`${parts.kind}:${parts.publisher}/${parts.name}@${parts.version}`).to.equal(ref);
    });
  }

  for (const ref of cases.invalid) {
    it(`refuses ${JSON.stringify(ref)}`, () => {
      expect(() => parseItemRef(ref)).to.throw();
    });
  }
});
