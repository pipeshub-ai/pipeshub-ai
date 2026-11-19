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

  it('treats a missing branch as epoch 0 and ignores unlisted branches', () => {
    expect(schemaProblems([], req)).to.deep.equal(['schema identity epoch 0 < required 7']);
    expect(schemaProblems([{ branch: 'other', epoch: 1, min_code_epoch: 1 }], {})).to.deep.equal([]);
  });
});
