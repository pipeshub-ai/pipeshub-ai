import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { NOT_STARTED } from '../../../../src/modules/agent-harness/domain/execution';
import { NO_COMPATIBLE_WORKER } from '../../../../src/modules/agent-harness/domain/worker-compat';
import { runFailure } from '../../../../src/modules/agent-harness/projection/transcript';
import { Timeline } from '../../../../src/modules/agent-harness/projection/timeline';

const VECTOR = join(
  __dirname,
  '../../../../../../../contracts/harness/vectors/run-error-codes.json',
);

const ended = (status: string, errorType: string): Timeline =>
  ({ end: { status, errorType }, approvals: [] }) as unknown as Timeline;

const DEFAULT = runFailure(ended('failed', 'no_such_code')).message;

/** ADR-OPS-01: a run an operator control cancelled ends as `operator_stopped`. */
const stopped = (): Timeline =>
  ({ end: { status: 'cancelled' }, approvals: [], stoppedBy: { scope: 'org', reason: 'incident' } }) as unknown as Timeline;

// The web app words each listed code (QA-17); every one must be a code Node actually ends a run with.
describe('run-error-codes.json is Node’s RUN_ERROR vocabulary', () => {
  const { codes } = JSON.parse(readFileSync(VECTOR, 'utf8')) as { codes: string[] };

  it('lists only codes Node sends', () => {
    const admission = new Set<string>([NOT_STARTED, NO_COMPATIBLE_WORKER]);
    for (const code of codes) {
      if (admission.has(code)) continue;
      if (code === 'operator_stopped') {
        expect(runFailure(stopped()).code).to.equal(code);
        continue;
      }
      const status = code === 'quarantined' ? 'quarantined' : 'failed';
      expect(runFailure(ended(status, code)).message, code).to.not.equal(DEFAULT);
    }
  });

  it('PERFA-9: a quarantined run is sent under the listed code, not under its cause', () => {
    expect(codes).to.include('quarantined');
    for (const cause of ['crash_loop', 'assembly_failed']) {
      expect(codes, cause).to.not.include(cause);
      expect(runFailure(ended('quarantined', cause)).code, cause).to.equal('quarantined');
    }
  });
});
