import { expect } from 'chai';
import { ANSWER_ERROR_CODES } from '../../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/enums';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { finalAnswer } from '../../../../src/modules/agent-harness/projection/transcript';
import { buildAIResponseMessage } from '../../../../src/modules/enterprise_search/utils/utils';

/** EU-8 (QA17-O2): Node's own error answers carry catalogued answer codes, and a saved error answer keeps its code. */

const RUN = 'run-errux-answer';

const ended: JournalEvent = {
  orgId: 'org-1',
  sessionId: 'ses-1',
  streamId: 'ses-1',
  seq: 1,
  runId: RUN,
  type: 'run.failed',
  schemaVersion: 1,
  critical: true,
  ts: '2026-10-10T12:00:01.000Z',
  writer: 'node',
  visibility: 'owner',
  data: { errorType: 'deadline_exceeded', message: 'The run passed its deadline' },
} as JournalEvent;

describe('ERR-UX EU-8: error answers are worded by catalogued codes', () => {
  it("Node's deadline answer carries a catalogued answer code", () => {
    const answer = finalAnswer(foldTimeline([ended], RUN));
    expect(answer?.answerMatchType).to.equal('Error');
    expect(ANSWER_ERROR_CODES as readonly string[]).to.include(answer?.errorCode);
  });

  it('a saved error answer keeps its code; a plain answer has none', () => {
    const error = buildAIResponseMessage({
      statusCode: 200,
      data: { answer: 'The AI model took too long to answer.', citations: [], answerMatchType: 'Error', errorCode: 'timeout' },
    } as never);
    expect(error).to.include({ messageType: 'error', errorCode: 'timeout' });
    const plain = buildAIResponseMessage({
      statusCode: 200,
      data: { answer: 'Twenty-five days.', citations: [], answerMatchType: 'Derived From Blocks' },
    } as never);
    expect(plain.messageType).to.equal('bot_response');
    expect(plain).to.not.have.property('errorCode');
  });
});
