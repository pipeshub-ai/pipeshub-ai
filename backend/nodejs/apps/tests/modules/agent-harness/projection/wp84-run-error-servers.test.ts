/**
 * WP84-WORDS: a run that ended `mcp_tool_withdrawn` carries the labels of the servers that withdrew the tool from the
 * worker's transition to `run.failed`, the live `RUN_ERROR` frame and the saved failure, so the web app words it in
 * the reader's language and still names the server (WP-79 open item 1). Every other failure carries none.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { HarnessRun, transitionJournalEvents } from '../../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { commitRunBodySchema } from '../../../../src/modules/agent-harness/transport/schemas';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { runTranscript } from '../../../../src/modules/agent-harness/projection/transcript';
import { buildAIFailureResponseMessage } from '../../../../src/modules/enterprise_search/utils/utils';

const RUN = 'run-1';
const WORDS =
  "Atlassian no longer offers a tool this task was using, so the task stopped. Send your message again to continue without it, or ask your admin to review the server's tools.";

const run = (): HarnessRun =>
  ({
    runId: RUN,
    orgId: 'org-1',
    sessionId: 'ses-1',
    actorId: 'user-1',
    harnessMajor: 2,
    status: 'running',
    revision: 1,
    fence: 1,
  }) as unknown as HarnessRun;

function journal(failed: Record<string, unknown>): JournalEvent[] {
  const specs: Array<[string, Record<string, unknown>]> = [
    ['run.admitted', { runKind: 'interactive', principalRef: 'p_user' }],
    ['run.claimed', { attempt: 1, fence: 1, leaseMs: 60000 }],
    ['run.failed', failed],
  ];
  return specs.map(
    ([type, data], index) =>
      ({
        orgId: 'org-1',
        sessionId: 'ses-1',
        streamId: 'ses-1',
        seq: index + 1,
        runId: RUN,
        type,
        schemaVersion: 1,
        critical: true,
        ts: '2026-11-05T12:00:00.000Z',
        writer: 'node',
        visibility: 'owner',
        data,
      }) as JournalEvent,
  );
}

const commit = (detail: Record<string, unknown>) =>
  commitRunBodySchema.safeParse({
    commandId: 'c-1',
    attemptId: 'a-1',
    fence: 1,
    transition: { status: 'failed', errorType: 'mcp_tool_withdrawn', detail },
  });

describe('WP84-WORDS: a withdrawn tool’s servers reach the reader as data', () => {
  it('WP84-WORDS-3: the worker’s transition is accepted with its servers and journaled on run.failed', () => {
    expect(commit({ kind: 'fail', message: WORDS, servers: ['Atlassian'] }).success).to.equal(true);
    expect(commit({ kind: 'fail', servers: [] }).success, 'no empty list').to.equal(false);
    expect(commit({ kind: 'fail', servers: [''] }).success, 'no empty label').to.equal(false);
    const [event] = transitionJournalEvents(run(), {
      status: 'failed',
      errorType: 'mcp_tool_withdrawn',
      detail: { kind: 'fail', message: WORDS, servers: ['Atlassian'] },
    });
    expect(event?.data).to.deep.equal({ errorType: 'mcp_tool_withdrawn', message: WORDS, servers: ['Atlassian'] });
    const [plain] = transitionJournalEvents(run(), { status: 'failed', errorType: 'engine_failed' });
    expect(plain?.data).to.deep.equal({ errorType: 'engine_failed' });
  });

  it('WP84-WORDS-4: the saved failure and the live RUN_ERROR carry the servers beside the code', () => {
    const events = journal({ errorType: 'mcp_tool_withdrawn', message: WORDS, servers: ['Atlassian', 'GitHub'] });
    const transcript = runTranscript(events, RUN);
    expect(transcript).to.deep.include({ kind: 'failure', code: 'mcp_tool_withdrawn', servers: ['Atlassian', 'GitHub'] });
    const projector = new RunProjector({
      runId: RUN,
      sessionId: 'ses-1',
      conversationId: 'ses-1',
      title: 'q',
      goal: 'q',
      audience: 'owner',
    });
    const frames = [...projector.begin(), ...projector.project(events)];
    const error = frames.map((frame) => frame.frame as unknown as Record<string, unknown>).find((data) => data['type'] === 'RUN_ERROR');
    expect(error).to.deep.include({ code: 'mcp_tool_withdrawn', servers: ['Atlassian', 'GitHub'] });
  });

  it('WP84-WORDS-5: a failure with no servers carries none', () => {
    const transcript = runTranscript(journal({ errorType: 'capability_lost' }), RUN);
    expect(transcript).to.not.have.property('servers');
  });

  it('WP84-WORDS-6: the saved message keeps the servers for a reload, and only when there are some', () => {
    const saved = buildAIFailureResponseMessage(WORDS, undefined, {
      runId: RUN,
      errorCode: 'mcp_tool_withdrawn',
      errorServers: ['Atlassian'],
    });
    expect(saved).to.deep.include({ errorCode: 'mcp_tool_withdrawn', errorServers: ['Atlassian'] });
    const plain = buildAIFailureResponseMessage('x', undefined, { runId: RUN, errorCode: 'capability_lost' });
    expect(plain).to.not.have.property('errorServers');
  });
});
