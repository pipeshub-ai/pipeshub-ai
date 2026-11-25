import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { sectionOf } from '../jobs';
import { statusOfPersonRun } from '../jobs-list';
import type { PersonRuns } from '@/lib/contracts/harness/generated/ext/pipeshub/api/person-runs';

type PersonRun = PersonRuns['runs'][number];

/**
 * CJ14-FU-1: the sidebar's Jobs count is the server's `working`; the Jobs page's Working section must be the same
 * set. Both read `contracts/harness/vectors/working-jobs.json`: Node's `isWorkingJob`, and here the page's sections
 * from the list's `state` and `status` (every listed job is a child run, `kind=job`).
 */
const VECTOR = JSON.parse(
  readFileSync(path.resolve(__dirname, '../../../../contracts/harness/vectors/working-jobs.json'), 'utf8'),
) as { cases: Array<{ name: string; status: string; job: boolean; state: string; working: boolean }> };

describe('CJ14-FU-1: the Working section is the set the Jobs count counts', () => {
  it.each(VECTOR.cases.filter((c) => c.job).map((c) => [c.name, c] as const))('%s', (_name, c) => {
    const run = { state: c.state, status: c.status } as Pick<PersonRun, 'state' | 'status'>;
    expect(sectionOf(statusOfPersonRun(run)) === 'working').toBe(c.working);
  });
});
