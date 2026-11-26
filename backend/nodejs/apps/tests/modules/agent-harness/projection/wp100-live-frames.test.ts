/**
 * WP-100 (ADR-RUN-01 D1, C10.05): what the live stream adds for a client that reads the outcome frames
 * (`X-PipesHub-Frames: 2`), and the approval count pushed to the card.
 *
 * WP100-ERR: the root RUN_ERROR carries the typed outcome the saved failure keeps, only for the owner's client that
 *   asked; every other client keeps the legacy frame the D-5 and LAT2 goldens pin.
 * WP100-COST: the run's spend is read from the ledger and pushed as `run_cost` when the run journals, throttled and
 *   only when it changed; never for another audience or a legacy client.
 * WP100-INBOX: the approvals inbox lists when each request was asked (WP-97's open item).
 * WP100-2P: an approval counted on an ask that needs several reaches the live card as `approval_counted`, counts only.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { corsOptions } from '../../../../src/libs/middlewares/cors.options';
import { InMemoryJournalTail } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-tail';
import { ApprovalQueueService } from '../../../../src/modules/agent-harness/application/approval-queue';
import { pendingApprovalsSchema } from '../../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/pending-approvals';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { InMemoryPayloadStore } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import { RunJournalReader } from '../../../../src/modules/agent-harness/application/run-journal-reader';
import { RunBudgetView, RunSpendReader } from '../../../../src/modules/agent-harness/application/run-spend';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { APPROVAL_COUNTED } from '../../../../src/modules/agent-harness/domain/interactions';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { CostPush } from '../../../../src/modules/agent-harness/projection/cost-push';
import { ChildInteractions } from '../../../../src/modules/agent-harness/projection/child-interactions';
import {
  FRAMES_HEADER,
  frameVersion,
  framesOf,
} from '../../../../src/modules/agent-harness/projection/frame-version';
import { AguiFrame, Audience } from '../../../../src/modules/agent-harness/projection/frames';
import { failedOutcome } from '../../../../src/modules/agent-harness/projection/outcome-view';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { FrameSink, RunStreamService } from '../../../../src/modules/agent-harness/projection/stream-service';
import { runTranscript } from '../../../../src/modules/agent-harness/projection/transcript';
import { parseSse, readJournal } from '../../../contracts/support/durable';
import { testCipher } from '../support/data-protection';
import { without } from '../support/without';

const RUN = 'run-1';

const event = (seq: number, type: string, data: Record<string, unknown>, writer: 'node' | 'worker' = 'node'): JournalEvent =>
  ({
    orgId: 'org-1',
    sessionId: 'ses-1',
    streamId: 'ses-1',
    seq,
    runId: RUN,
    type,
    schemaVersion: 1,
    critical: true,
    ts: '2026-11-06T09:00:00.000Z',
    writer,
    visibility: 'owner',
    data,
  }) as JournalEvent;

const failedRun = (failed: Record<string, unknown>): JournalEvent[] => [
  event(1, 'run.admitted', { runKind: 'interactive', principalRef: 'p_user' }),
  event(2, 'run.claimed', { attempt: 1, fence: 1, leaseMs: 60000 }),
  event(3, 'run.failed', failed),
];

const projector = (frames?: 2, audience: Audience = 'owner') =>
  new RunProjector({
    runId: RUN,
    sessionId: 'ses-1',
    conversationId: 'ses-1',
    title: 'q',
    goal: 'q',
    audience,
    ...(frames !== undefined ? { frames } : {}),
  });

const runError = (frames: Array<{ frame: AguiFrame }>): Record<string, unknown> | undefined =>
  frames.map((f) => f.frame as Record<string, unknown>).find((f) => f['type'] === 'RUN_ERROR' && f['parentRunId'] === undefined);

class Sink implements FrameSink {
  body = '';
  ended = false;
  write(chunk: string): boolean {
    this.body += chunk;
    return true;
  }
  end(): void {
    this.ended = true;
  }
  onDrain(): void {}
  get pending(): number {
    return 0;
  }
  frames(): Array<{ id?: string; frame: AguiFrame }> {
    return parseSse(this.body);
  }
}

/** Lets the stream's chain, the cost push's timer (0 ms here) and the reads it schedules run. */
const settle = async (): Promise<void> => {
  for (let i = 0; i < 3; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 2));
    for (let j = 0; j < 8; j += 1) await new Promise((resolve) => setImmediate(resolve));
  }
};

const spent = (usdMicros: number): RunBudgetView =>
  ({
    cap: { usdMicros: 5_000_000 },
    committed: { usdMicros },
    reserved: { usdMicros: 0 },
    available: { usdMicros: 5_000_000 - usdMicros },
    exhausted: false,
    estimated: 0,
  }) as unknown as RunBudgetView;

/** A run whose journal the test commits piece by piece, streamed by one Node with a ledger the test moves. */
class World {
  readonly recorded = readJournal('agent_quick_tool_call');
  readonly visible: JournalEvent[] = [];
  readonly tail = new InMemoryJournalTail();
  ledgerReads = 0;
  committed = 0;
  readonly run: HarnessRun;
  readonly service: RunStreamService;

  constructor(spend = true, costPushMs = 0) {
    this.run = {
      runId: this.recorded.runId,
      orgId: 'org-1',
      sessionId: this.recorded.sessionId,
      actorId: 'user-1',
      input: { query: this.recorded.query } as HarnessRun['input'],
      createdAt: 1_000,
      updatedAt: 2_000,
    } as HarnessRun;
    const journal = {
      readRun: (_org: string, runId: string, after: number, limit: number) =>
        Promise.resolve(this.visible.filter((e) => e.runId === runId && e.seq > after).slice(0, limit)),
    };
    const journals = new JournalService(new InMemoryPayloadStore(), testCipher());
    const ledger = {
      account: () => Promise.resolve({ cap: { usdMicros: 5_000_000 } } as never),
      remaining: () => {
        this.ledgerReads += 1;
        const view = spent(this.committed);
        return Promise.resolve({ ...view } as never);
      },
    };
    this.service = new RunStreamService({
      journal,
      journals,
      runJournal: new RunJournalReader({ journal, journals }),
      tail: this.tail,
      conversations: { finishedConversation: () => Promise.resolve({ _id: 'ses', messages: [] }) },
      liveBus: null,
      ...(spend ? { spend: new RunSpendReader(ledger as never) } : {}),
      costPushMs,
    });
  }

  commitThrough(index: number): void {
    for (const e of this.recorded.events.slice(0, index)) if (!this.visible.includes(e)) this.visible.push(e);
    this.tail.changed('org-1', this.run.runId);
  }
}

const costs = (sink: Sink) => sink.frames().filter((f) => f.frame.type === 'CUSTOM' && f.frame['name'] === 'run_cost');

describe('WP100-ERR: the live RUN_ERROR carries the typed outcome for a client that reads it', () => {
  it('WP100-ERR-1: the frames header names the version; anything else is the legacy frames', () => {
    expect(frameVersion(undefined)).to.equal(1);
    expect(frameVersion('1')).to.equal(1);
    expect(frameVersion('2')).to.equal(2);
    expect(frameVersion(' 2 ')).to.equal(2);
    expect(frameVersion('9'), 'a newer client reads the newest this Node serves').to.equal(2);
    expect(frameVersion(['2', '1'])).to.equal(2);
    expect(frameVersion('two')).to.equal(1);
    expect(frameVersion('2; drop')).to.equal(1);
    expect(framesOf({ headers: { [FRAMES_HEADER]: '2' } })).to.deep.equal({ frames: 2 });
    expect(framesOf({ headers: {} })).to.deep.equal({});
  });

  it('WP100-ERR-2: a legacy client’s RUN_ERROR is unchanged; a version-2 client’s carries what the saved failure keeps', () => {
    const events = failedRun({ errorType: 'mcp_tool_withdrawn', message: 'Atlassian stopped offering a tool.', servers: ['Atlassian'] });
    const legacy = runError([...projector().begin(), ...projector().project(events)]);
    const legacyProjector = projector();
    const legacyFrame = runError([...legacyProjector.begin(), ...legacyProjector.project(events)]);
    expect(legacyFrame).to.deep.equal(legacy);
    expect(legacyFrame).to.not.have.property('runOutcome');
    const live = projector(2);
    const frame = runError([...live.begin(), ...live.project(events)]);
    const saved = runTranscript(events, RUN);
    expect(saved!.kind).to.equal('failure');
    expect(frame?.['runOutcome'], 'the same typed outcome the conversation writer saves').to.deep.equal(
      failedOutcome(saved as Parameters<typeof failedOutcome>[0]),
    );
    const rest = without(frame as Record<string, unknown>, 'runOutcome');
    expect(rest, 'every legacy field is kept beside it').to.deep.equal(legacyFrame);
  });

  it('WP100-ERR-3: an operator’s stop and Node’s own failure carry it too', () => {
    const stopped = projector(2);
    const frames = [
      ...stopped.begin(),
      ...stopped.project([
        event(1, 'run.admitted', { runKind: 'interactive', principalRef: 'p_user' }),
        event(2, 'run.cancel_requested', { by: 'p_admin', reason: 'operator', stop: { scope: 'org', reason: 'incident' } }),
        event(3, 'run.cancelled', {}),
      ]),
    ];
    const stop = runError(frames);
    expect(stop?.['runOutcome']).to.deep.include({ kind: 'failed', reason: 'operator_stopped', action: 'retry' });
    expect((stop?.['runOutcome'] as Record<string, unknown>)['stop']).to.deep.equal({ scope: 'org', reason: 'incident' });
    const own = projector(2);
    own.begin();
    const [failed] = own.fail('save_error', 'The conversation could not be loaded.');
    expect(failed?.frame['runOutcome']).to.deep.include({ kind: 'failed', reason: 'save_error', closing: 'The conversation could not be loaded.' });
    const legacy = projector();
    legacy.begin();
    expect(legacy.fail('save_error', 'x')[0]?.frame).to.deep.equal({ type: 'RUN_ERROR', message: 'x', code: 'save_error' });
  });

  it('WP100-ERR-4: the stream serves it to the owner’s client that asked, never to a shared viewer or the legacy client', async () => {
    const outcomeOf = async (audience: Audience, frames?: 2) => {
      const world = new World(false);
      const events = failedRun({ errorType: 'engine_tools_unreachable' }).map((e) => ({ ...e, runId: world.run.runId }));
      world.visible.push(...events);
      const sink = new Sink();
      const handle = world.service.open({ run: world.run, audience, ...(frames !== undefined ? { frames } : {}) }, sink);
      await handle.done;
      return runError(sink.frames());
    };
    expect((await outcomeOf('owner', 2))?.['runOutcome']).to.deep.include({ kind: 'failed', reason: 'engine_tools_unreachable' });
    expect(await outcomeOf('owner')).to.not.have.property('runOutcome');
    expect(await outcomeOf('shared', 2)).to.not.have.property('runOutcome');
  });

  it('WP100-ERR-5: a split deployment lets the web app send the header (CORS)', () => {
    expect(corsOptions({}).allowedHeaders).to.include(FRAMES_HEADER);
  });
});

describe('WP100-COST: the run’s spend is pushed as the ledger moves', () => {
  it('WP100-COST-1: the first read is sent whatever it says, later ones only when they changed; a failed read sends nothing', async () => {
    const sent: AguiFrame[] = [];
    let value: RunBudgetView | null = null;
    let fail = false;
    let clock = 0;
    const push = new CostPush({
      runId: RUN,
      read: () => (fail ? Promise.reject(new Error('down')) : Promise.resolve(value)),
      schedule: (step) => void step(),
      send: (frame) => sent.push(frame),
      failed: () => sent.push({ type: 'failed' }),
      minMs: 0,
      now: () => clock,
    });
    push.tick();
    await settle();
    expect(sent).to.deep.equal([{ type: 'CUSTOM', name: 'run_cost', value: { runId: RUN, budget: null }, runId: RUN }]);
    push.tick();
    await settle();
    expect(sent, 'unchanged: nothing sent').to.have.length(1);
    value = spent(420_000);
    clock += 1;
    push.tick();
    await settle();
    expect(sent[1]?.['value']).to.deep.equal({ runId: RUN, budget: spent(420_000) });
    fail = true;
    push.tick();
    await settle();
    expect(sent[2]).to.deep.equal({ type: 'failed' });
    const before = sent.length;
    push.close();
    fail = false;
    value = spent(900_000);
    push.tick();
    await settle();
    expect(sent, 'closed: no more reads').to.have.length(before);
  });

  it('WP100-COST-2: ticks inside the interval coalesce into one read after it', async () => {
    let reads = 0;
    const push = new CostPush({
      runId: RUN,
      read: () => {
        reads += 1;
        return Promise.resolve(spent(reads));
      },
      schedule: (step) => void step(),
      send: () => undefined,
      minMs: 30,
    });
    push.tick();
    await new Promise((resolve) => setTimeout(resolve, 5));
    for (let i = 0; i < 5; i += 1) push.tick();
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(reads, 'the second read waits for the interval').to.equal(1);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(reads).to.equal(2);
    push.close();
  });

  it('WP100-COST-3: the owner’s version-2 stream pushes the cost at open and when the run journals spend', async () => {
    const world = new World();
    world.commitThrough(3);
    const sink = new Sink();
    const handle = world.service.open({ run: world.run, audience: 'owner', frames: 2 }, sink);
    await settle();
    expect(costs(sink).map((f) => f.frame['value'])).to.deep.equal([{ runId: world.run.runId, budget: spent(0) }]);
    const [first] = costs(sink);
    expect(first?.id, 'a state frame after the last positional one: a resume never replays it').to.match(/\+\d+$/);
    world.committed = 420_000;
    world.commitThrough(6);
    await settle();
    expect(costs(sink).map((f) => (f.frame['value'] as { budget: RunBudgetView }).budget.committed)).to.deep.equal([
      { usdMicros: 0 },
      { usdMicros: 420_000 },
    ]);
    const reads = world.ledgerReads;
    world.commitThrough(8);
    await settle();
    expect(costs(sink), 'the ledger did not move: nothing more').to.have.length(2);
    expect(world.ledgerReads).to.be.greaterThan(reads);
    handle.close();
  });

  it('WP100-COST-4: a legacy client, a shared viewer and a Node without the ledger get no cost frame and read no ledger', async () => {
    for (const [audience, frames, spend] of [
      ['owner', undefined, true],
      ['shared', 2, true],
      ['owner', 2, false],
    ] as const) {
      const world = new World(spend);
      world.commitThrough(4);
      const sink = new Sink();
      const handle = world.service.open({ run: world.run, audience, ...(frames !== undefined ? { frames } : {}) }, sink);
      await settle();
      world.committed = 100;
      world.commitThrough(7);
      await settle();
      expect(costs(sink), `${audience} ${String(frames)} ${String(spend)}`).to.have.length(0);
      expect(world.ledgerReads).to.equal(0);
      handle.close();
    }
  });
});

describe('WP100-FU-5: the cost lags no step and the run ends with its last one', () => {
  it('WP100-COST-5: a ledger that moves after the run last journaled is read once more an interval later, then no more', async () => {
    let committed = 0;
    let reads = 0;
    const sent: number[] = [];
    const push = new CostPush({
      runId: RUN,
      read: () => {
        reads += 1;
        return Promise.resolve(spent(committed));
      },
      schedule: (step) => void step(),
      send: (frame) => sent.push(((frame['value'] as { budget: RunBudgetView }).budget.committed as { usdMicros: number }).usdMicros),
      minMs: 20,
    });
    push.tick();
    await new Promise((resolve) => setTimeout(resolve, 5));
    // The model step's charge lands in the ledger after the step's journal event was read.
    committed = 420_000;
    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(sent).to.deep.equal([0, 420_000]);
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(reads, 'an idle run reads the ledger no more').to.equal(2);
    push.close();
  });

  it('WP100-COST-6: the run’s end pushes its last cost before the run’s end frame, inside the interval too', async () => {
    const world = new World(true, 60_000);
    world.commitThrough(3);
    const sink = new Sink();
    const handle = world.service.open({ run: world.run, audience: 'owner', frames: 2 }, sink);
    await settle();
    world.committed = 730_000;
    world.commitThrough(world.recorded.events.length);
    await handle.done;
    const frames = sink.frames().map((f) => f.frame);
    const last = frames.map((f, i) => [f, i] as const).filter(([f]) => f['name'] === 'run_cost').pop();
    expect((last?.[0]['value'] as { budget: RunBudgetView }).budget.committed).to.deep.equal({ usdMicros: 730_000 });
    const finished = frames.findIndex((f) => f.type === 'RUN_FINISHED');
    expect(finished).to.be.greaterThan(-1);
    expect(last![1]).to.be.lessThan(finished);
  });
});

describe('WP100-2P: the approval count moves on the live card', () => {
  const IX = 'ix.approve.k0123456789abcdef';
  const asked: JournalEvent[] = [
    event(1, 'run.admitted', { runKind: 'interactive', principalRef: 'p_user' }),
    event(2, 'run.claimed', { attempt: 1, fence: 1, leaseMs: 60000 }),
    event(3, 'x.pipeshub.engine.progress', {
      tool: 'code_task',
      taskId: RUN,
      kind: 'approval',
      text: 'send_mail',
      data: { interactionId: IX, toolName: 'mail__send', argsJson: '{"to":"a@b.c"}' },
    }, 'worker'),
    event(4, 'interaction.requested', { interactionId: IX, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` }, 'worker'),
  ];
  const counted = (seq: number, by: string, writer: 'node' | 'worker' = 'node'): JournalEvent =>
    ({ ...event(seq, APPROVAL_COUNTED, { interactionId: IX, by, counted: 1, required: 2 }, writer), visibility: 'audit', critical: false }) as JournalEvent;
  const countedFrames = (events: JournalEvent[]) => {
    const p = projector();
    return [...p.begin(), ...p.project(events)]
      .map((f) => f.frame)
      .filter((f) => f.type === 'CUSTOM' && f['name'] === 'approval_counted');
  };

  it('WP100-2P-1: the first admin’s approval says 1 of 2 live, counts only, never who', () => {
    const p = projector();
    const all = [...p.begin(), ...p.project(asked)];
    const card = all.find((f) => f.frame['name'] === 'approval_requested');
    expect(card, 'the ask is a card').to.not.equal(undefined);
    const frames = countedFrames([...asked, counted(5, 'p_admin_1')]);
    expect(frames).to.deep.equal([
      { type: 'CUSTOM', name: 'approval_counted', value: { interactionId: IX, approvals: { counted: 1, required: 2 } }, runId: RUN },
    ]);
    expect(JSON.stringify(frames)).to.not.contain('p_admin_1');
  });

  it('WP100-2P-3 (WP100-FU-5): a sub-agent’s count reaches its parent’s stream on the child’s card, counts only', async () => {
    const CHILD = 'run-child';
    const journal = asked.map((e) => ({ ...e, runId: CHILD }));
    const child = new ChildInteractions(async (_id, after) => journal.filter((e) => e.seq > after), RUN);
    child.track([{ childRunId: CHILD, role: 'coder', spawnSeq: 3, live: true }]);
    const first = (await child.pull(CHILD)).map((f) => f.frame);
    expect(first.some((f) => f['name'] === 'approval_requested')).to.equal(true);
    journal.push({ ...counted(5, 'p_admin_1'), runId: CHILD });
    const moved = (await child.pull(CHILD)).map((f) => f.frame).filter((f) => f['name'] === 'approval_counted');
    expect(moved).to.deep.equal([
      {
        type: 'CUSTOM',
        name: 'approval_counted',
        value: { interactionId: IX, approvals: { counted: 1, required: 2 }, childRunId: CHILD },
        runId: RUN,
      },
    ]);
    expect(JSON.stringify(moved)).to.not.contain('p_admin_1');
    journal.push({ ...counted(6, 'p_admin_2', 'worker'), runId: CHILD });
    expect((await child.pull(CHILD)).filter((f) => f.frame['name'] === 'approval_counted'), 'a worker-written count').to.deep.equal([]);
  });

  it('WP100-2P-2: a worker-written count, an unknown ask and a decided ask move nothing', () => {
    expect(countedFrames([...asked, counted(5, 'p_admin_1', 'worker')])).to.deep.equal([]);
    expect(countedFrames([counted(1, 'p_admin_1')])).to.deep.equal([]);
    expect(
      countedFrames([
        ...asked,
        event(5, 'interaction.resolved', { interactionId: IX, decision: 'approved', by: 'p_admin_2', channel: 'web' }),
        counted(6, 'p_admin_1'),
      ]),
    ).to.deep.equal([]);
  });
});

describe('WP100-INBOX: the approvals inbox says since when a request waits', () => {
  const IX = 'ix.approve.k0123456789abcdef';
  const queueOver = (events: JournalEvent[]) =>
    new ApprovalQueueService(
      { pendingApprovalRuns: () => Promise.resolve([{ ...pendingRun, openInteractions: [IX] } as HarnessRun]) },
      {
        standing: () => Promise.resolve({ isApprover: true, isOrgAdmin: true } as never),
        authorityFor: () => Promise.resolve({ approve: true, deny: true, participant: true } as never),
      },
      { approvals: (run: HarnessRun) => Promise.resolve(foldTimeline(events, run.runId).approvals) },
    );
  const pendingRun = { runId: RUN, orgId: 'org-1', sessionId: 'ses-1', actorId: 'user-1', status: 'waiting_approval', updatedAt: 9_999 };
  const asked = (ts: string): JournalEvent[] => [
    event(1, 'x.pipeshub.engine.progress', {
      tool: 'code_task',
      taskId: RUN,
      kind: 'approval',
      text: 'send_mail',
      data: { interactionId: IX, toolName: 'mail__send', argsJson: '{}' },
    }, 'worker'),
    { ...event(2, 'interaction.requested', { interactionId: IX, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` }, 'worker'), ts },
  ];

  it('WP100-INBOX-1: each item carries requestedAt, the journal time of the ask the chat says “since” from', async () => {
    const [item] = await queueOver(asked('2026-11-06T08:15:00.000Z')).pending({ orgId: 'org-1', actorId: 'admin-1', limit: 50 });
    expect(item).to.deep.include({ interactionId: IX, requestedAt: '2026-11-06T08:15:00.000Z', runUpdatedAt: 9_999 });
    expect(pendingApprovalsSchema.safeParse({ items: [item] }).success, 'the contract takes it').to.equal(true);
    expect(pendingApprovalsSchema.safeParse({ items: [{ ...item, requestedAt: 'yesterday' }] }).success).to.equal(false);
  });
});
