import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { classifyResponder, type ClassifyInput, type Responder } from '../classify';

interface Row extends ClassifyInput {
  name: string;
  expected: Responder;
}

const FIXTURE = resolve(process.cwd(), '../backend/nodejs/apps/tests/fixtures/respond-mode-cases.json');
const rows = JSON.parse(readFileSync(FIXTURE, 'utf8')) as Row[];

describe('classifyResponder (PH10-07: parity with the Node twin through the shared table)', () => {
  it('reads the shared fixture', () => {
    expect(rows).toHaveLength(38);
  });

  it.each(rows.map((r) => [r.name, r] as const))('%s', (_name, row) => {
    expect(
      classifyResponder({ mentions: row.mentions, respondMode: row.respondMode, sessionKind: row.sessionKind }),
    ).toBe(row.expected);
  });
});
