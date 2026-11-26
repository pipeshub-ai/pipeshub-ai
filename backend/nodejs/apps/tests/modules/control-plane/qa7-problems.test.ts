import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join, resolve } from 'path';
import { ERROR_CODES, readProblem } from '../../../src/modules/control-plane';

/**
 * QA7-30 (FR-38, Node half): the Problem vector. Python reads it in
 * tests/unit/contracts_harness/test_qa7_problems.py, the frontend in
 * lib/contracts/__tests__/qa7-problems.test.ts.
 */
const VECTOR = JSON.parse(
  readFileSync(
    join(resolve(__dirname, '..', '..', '..', '..', '..', '..'), 'contracts', 'control-plane', 'vectors', 'problems.json'),
    'utf8',
  ),
) as {
  accepted: Array<{ code: string }>;
  refused: Array<{ reason: string; description: string; problem: unknown }>;
};

describe('QA7-30 one error model (Node)', () => {
  for (const body of VECTOR.accepted) {
    it(`accepts ${body.code}`, () => {
      expect(readProblem(body)).to.deep.include({ refusal: null });
    });
  }

  for (const { reason, description, problem } of VECTOR.refused) {
    it(`refuses ${description} (${reason})`, () => {
      expect(readProblem(problem)).to.deep.equal({ problem: null, refusal: reason });
    });
  }

  it('every catalogue code has an accepted problem', () => {
    expect(new Set(VECTOR.accepted.map((b) => b.code))).to.deep.equal(new Set(Object.keys(ERROR_CODES)));
  });
});
