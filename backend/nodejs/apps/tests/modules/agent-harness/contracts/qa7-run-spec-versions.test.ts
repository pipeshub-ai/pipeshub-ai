import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import {
  RUN_SPEC_SCHEMA_VERSION,
  RUN_SPEC_UPCASTERS,
  readPinnedSpec,
} from '../../../../src/modules/agent-harness/domain/run-spec';
import { CONTRACTS_DIR } from '../../../support/harness/contracts';

/**
 * QA7-41 (CON-13, Node half): a RunSpec schemaVersion bump needs an upcaster
 * for every older version and a golden of it that upcasts to the expected
 * form. Python reads the same goldens in test_qa7_run_spec_versions.py.
 */
const read = (...path: string[]): unknown => JSON.parse(readFileSync(join(CONTRACTS_DIR, ...path), 'utf8'));
const VECTOR = read('vectors', 'run-spec-versions.json') as {
  current: number;
  goldens: Array<{ schemaVersion: number; spec: Record<string, unknown>; upcast: Record<string, unknown> }>;
};
const SCHEMA_VERSIONS = (read('core', 'run-spec.schema.json') as { properties: { schemaVersion: { enum: number[] } } })
  .properties.schemaVersion.enum;

describe('QA7-41 RunSpec versions (Node)', () => {
  it('the current version is the schema’s newest and the goldens cover every version', () => {
    expect(RUN_SPEC_SCHEMA_VERSION).to.equal(Math.max(...SCHEMA_VERSIONS));
    expect(VECTOR.current).to.equal(RUN_SPEC_SCHEMA_VERSION);
    expect(VECTOR.goldens.map((g) => g.schemaVersion)).to.deep.equal(SCHEMA_VERSIONS);
  });

  it('every older version has an upcaster', () => {
    const older = SCHEMA_VERSIONS.filter((v) => v < RUN_SPEC_SCHEMA_VERSION);
    expect(Object.keys(RUN_SPEC_UPCASTERS).map(Number).sort()).to.deep.equal(older.sort());
  });

  for (const golden of VECTOR.goldens) {
    it(`v${golden.schemaVersion} reads as its expected upcast form`, () => {
      expect(readPinnedSpec(golden.spec)).to.deep.equal(golden.upcast);
    });
  }
});
