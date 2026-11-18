import { expect } from 'chai';
import { schemaProblems } from '../../../src/libs/db/schema-assert';

describe('schemaProblems', () => {
  const req = { identity: { requires: 7, code: 7 } };

  it('accepts a matching schema', () => {
    expect(schemaProblems([{ branch: 'identity', epoch: 7, min_code_epoch: 0 }], req)).to.deep.equal([]);
  });

  it('reports migrate not run', () => {
    expect(schemaProblems([{ branch: 'identity', epoch: 6, min_code_epoch: 0 }], req)).to.deep.equal([
      'schema identity epoch 6 < required 7',
    ]);
  });

  it('reports code older than a contracted schema', () => {
    expect(schemaProblems([{ branch: 'identity', epoch: 8, min_code_epoch: 8 }], req)).to.deep.equal([
      'schema identity min_code_epoch 8 > code 7',
    ]);
  });

  it('refuses a build that does not know scheduled notifications on the platform_data schema that has them', () => {
    const rows = [{ branch: 'platform_data', epoch: 5, min_code_epoch: 5 }];
    expect(schemaProblems(rows, { platform_data: { requires: 4, code: 4 } })).to.deep.equal([
      'schema platform_data min_code_epoch 5 > code 4',
    ]);
    expect(schemaProblems(rows, { platform_data: { requires: 5, code: 5 } })).to.deep.equal([]);
  });

  it('treats a missing branch as epoch 0 and ignores unlisted branches', () => {
    expect(schemaProblems([], req)).to.deep.equal(['schema identity epoch 0 < required 7']);
    expect(schemaProblems([{ branch: 'other', epoch: 1, min_code_epoch: 1 }], {})).to.deep.equal([]);
  });
});
