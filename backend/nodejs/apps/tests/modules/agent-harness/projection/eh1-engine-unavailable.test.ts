/**
 * EH-11 (EH1, ENG-35): a run whose engine cannot run on this deployment (its
 * binary is not the allow-listed one, its image is a candidate build, or it is
 * missing) ends with words that tell the user to ask an admin, never the
 * generic "try again": trying again cannot help.
 */
import { expect } from 'chai';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { runFailure } from '../../../../src/modules/agent-harness/projection/transcript';
import { readJournal } from '../../../contracts/support/durable';

const base = readJournal('agent_quick_tool_call');
const claimed = base.events.filter((e) => e.type === 'run.admitted' || e.type === 'run.claimed');
const last = claimed[claimed.length - 1] as JournalEvent;

const failedWith = (errorType: string) =>
  foldTimeline(
    [...claimed, { ...last, seq: last.seq + 1, type: 'run.failed', writer: 'node', critical: true, data: { errorType } }],
    base.runId,
  );

describe('EH-11: an engine that cannot run here says so', () => {
  const generic = runFailure(failedWith('engine_kernel_error')).message;

  for (const code of ['binary_mismatch', 'candidate_image', 'engine_unavailable', 'engine_image_missing', 'engine_contract_unsupported', 'model_call_encoded']) {
    it(`${code}: names the engine as unavailable and points at the admin`, () => {
      const failure = runFailure(failedWith(code));
      expect(failure.code).to.equal(code);
      expect(failure.message).to.not.equal(generic);
      expect(failure.message).to.match(/admin/i);
      expect(failure.message).to.not.match(/try (sending|again)/i);
    });
  }
});
