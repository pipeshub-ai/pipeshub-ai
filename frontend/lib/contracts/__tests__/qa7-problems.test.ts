import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ERROR_CODES } from '@/lib/contracts/control-plane/generated/errors';
import { readProblem } from '@/lib/contracts/control-plane/problem';

/** QA7-30 (FR-38, frontend half): the Problem vector Python and Node read too. */
const VECTOR = JSON.parse(
  readFileSync(resolve(__dirname, '..', '..', '..', '..', 'contracts', 'control-plane', 'vectors', 'problems.json'), 'utf8'),
) as {
  accepted: Array<{ code: string }>;
  refused: Array<{ reason: string; description: string; problem: unknown }>;
};

describe('QA7-30 one error model (frontend)', () => {
  it.each(VECTOR.accepted.map((body) => [body.code, body] as const))('accepts %s', (_code, body) => {
    expect(readProblem(body).refusal).toBeNull();
  });

  it.each(VECTOR.refused.map((c) => [c.description, c] as const))('refuses %s', (_description, c) => {
    expect(readProblem(c.problem)).toEqual({ problem: null, refusal: c.reason });
  });

  it('every catalogue code has an accepted problem', () => {
    expect(new Set(VECTOR.accepted.map((b) => b.code))).toEqual(new Set(Object.keys(ERROR_CODES)));
  });
});
