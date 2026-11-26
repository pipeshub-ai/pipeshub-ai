/**
 * UI-B1 G4, ADR-UI-01 D4: a job's own timeline is pushed live over the run stream route the chat already uses,
 * named by the job's run id (`GET …/runs/{jobRunId}/stream`): the same frames, the same resumable ids, the same
 * owner and shared audiences. The panel that read the job's timeline every 3 s follows this stream instead.
 */
import { expect } from 'chai';
import type { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { parseSse, readJournal } from '../../../contracts/support/durable';
import { InMemoryRecordReadAccess } from '../../../../src/modules/agent-harness/adapters/in-memory-record-access';
import { startStreamHttp, streamRun } from '../support/stream-http';

const parent = readJournal('agent_quick_tool_call');
const JOB = 'job-run-1';
/** The same work journaled by a job: on its own stream (its run id), as every child run journals. */
const journal = {
  ...parent,
  runId: JOB,
  events: parent.events.map((event) => ({ ...event, runId: JOB, streamId: JOB })),
};
const run = {
  ...streamRun(journal),
  parent: { runId: parent.runId, invocationId: 't1.0', depth: 1 },
} as unknown as HarnessRun;
const PATH = `/agents/agent-1/conversations/${journal.sessionId}/runs/${JOB}/stream`;

describe('UI-B1 G4: a job streams its own timeline live (ADR-UI-01 D4)', () => {
  let http: Awaited<ReturnType<typeof startStreamHttp>>;
  beforeEach(async () => {
    http = await startStreamHttp({ journal, run, records: new InMemoryRecordReadAccess(), citations: [], chat: false });
  });
  afterEach(async () => {
    await http.close();
  });

  it("UIB1-S1: the owner gets the job's own frames with ids, its tool calls, and its end", async () => {
    const reply = await http.get(PATH, 'owner');
    expect(reply.status).to.equal(200);
    expect(reply.headers.get('content-type')).to.equal('text/event-stream');
    const frames = parseSse(await reply.text());
    expect(frames.every((frame) => typeof frame.id === 'string')).to.equal(true);
    expect(frames.some((frame) => frame.frame.type === 'TOOL_CALL_START')).to.equal(true);
    expect(frames.at(-1)!.frame.type).to.equal('RUN_FINISHED');
    expect(JSON.stringify(frames.at(-1)!.frame)).to.include(JOB);
  });

  it('UIB1-S2: a dropped connection resumes after Last-Event-ID with exactly the rest', async () => {
    const clockless = (frames: ReturnType<typeof parseSse>) =>
      JSON.parse(JSON.stringify(frames).replace(/"timestamp":"[^"]+"/g, '"timestamp":"<clock>"')) as unknown;
    const whole = parseSse(await (await http.get(PATH, 'owner')).text());
    const cut = 4;
    const rest = parseSse(await (await http.get(PATH, 'owner', { 'Last-Event-ID': whole[cut]!.id! })).text());
    expect(clockless(rest)).to.deep.equal(clockless(whole.slice(cut + 1)));
  });

  it('UIB1-S3: someone the conversation is not shared with finds no job; a shared reader gets the shared audience', async () => {
    expect((await http.get(PATH, 'stranger')).status).to.equal(404);
    const shared = parseSse(await (await http.get(PATH, 'viewer')).text()).map((frame) => frame.frame.type);
    expect(shared).to.not.include('TOOL_CALL_START');
    expect(shared.at(-1)).to.equal('RUN_FINISHED');
  });
});
