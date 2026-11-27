/**
 * LCFIX: a Coder turn's code_task child starts from the engine state the conversation's previous code_task child
 * ended with. Each child journals on its own stream, so admission finds it and pins it as
 * `x.pipeshub.engineCarry`; these are the cases it refuses.
 */
import { expect } from 'chai';
import { engineCarry } from '../../../src/modules/agent-harness/application/engine-carry';
import { pipeshubRunSpecExtensionSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';

const DIGEST = `sha256:${'0'.repeat(64)}`;

interface Fixture {
  runId: string;
  actorId?: string;
  parentRunId?: string;
  sessionId?: string;
  executor: 'loop' | 'opencode' | 'codex';
  checkpoint?: boolean;
  snapshot?: boolean;
  /** Days before NOW the snapshot was journaled (default 1). */
  snapshotAgeDays?: number;
}

const NOW = Date.parse('2026-10-28T12:00:00.000Z');
const DAY = 24 * 60 * 60 * 1000;
const TTL = 7;

const run = (f: Fixture): HarnessRun =>
  ({
    runId: f.runId,
    orgId: 'org-1',
    sessionId: f.sessionId ?? 'ses-1',
    actorId: f.actorId ?? 'user-1',
    status: 'completed',
    ...(f.parentRunId !== undefined
      ? { parent: { runId: f.parentRunId, invocationId: 'i', depth: 1 } }
      : {}),
  }) as HarnessRun;

const checkpointed = (runId: string, n: number) => ({
  blob: { objectId: `obj-${runId}-${n}`, contentHash: 'h', sizeBytes: 10 },
  engine: { kind: 'opencode', version: '1.18.34', binaryDigest: DIGEST },
  schemaVersion: 1,
  turnId: `turn-${n}`,
});

function world(fixtures: Fixture[]) {
  const byId = new Map(fixtures.map((f) => [f.runId, f]));
  const reads: string[] = [];
  const carry = engineCarry({
    runs: {
      // Latest first, as the stores sort them: the fixtures list them oldest first.
      completedRootChildren: async (orgId, sessionId) =>
        fixtures
          .filter((f) => f.parentRunId !== undefined && (f.sessionId ?? 'ses-1') === sessionId)
          .reverse()
          .map(run),
      readClaimSubject: async (_orgId, runId) => {
        const f = byId.get(runId);
        if (f === undefined) return null;
        const executor =
          f.executor === 'loop'
            ? { kind: 'loop' }
            : { kind: 'engine', engine: { kind: f.executor, version: '1', binaryDigest: DIGEST, profileDigest: DIGEST } };
        return { run: run(f), pinned: { spec: { schemaVersion: 99, executor } } } as never;
      },
    },
    journal: {
      read: async (_orgId: string, runId: string) => {
        reads.push(runId);
        const f = byId.get(runId);
        const events: Array<Partial<JournalEvent>> = [];
        if (f?.checkpoint === true) {
          events.push({ type: 'engine.checkpointed', data: checkpointed(runId, 1) });
          events.push({ type: 'engine.checkpointed', data: checkpointed(runId, 2) });
        }
        if (f?.snapshot === true) {
          events.push({
            type: 'engine.snapshotted',
            ts: new Date(NOW - (f.snapshotAgeDays ?? 1) * DAY).toISOString(),
            data: { snapshot: { objectId: `snap-${runId}`, contentHash: 'h', sizeBytes: 1 }, turnId: 't', reason: 'turn_end' },
          });
        }
        return events as JournalEvent[];
      },
    },
    now: () => NOW,
  });
  return { carry, reads };
}

const parent = (f: Partial<Fixture> = {}): HarnessRun => run({ runId: 'turn-3', executor: 'loop', ...f });

describe('LCFIX: engine state carried to the next code_task child', () => {
  it('the previous code_task child of the same person: its last checkpoint and snapshot', async () => {
    const { carry } = world([
      { runId: 'turn-1', executor: 'loop' },
      { runId: 'task-1', parentRunId: 'turn-1', executor: 'opencode', checkpoint: true },
      { runId: 'turn-2', executor: 'loop' },
      { runId: 'task-2', parentRunId: 'turn-2', executor: 'opencode', checkpoint: true, snapshot: true },
    ]);
    const found = await carry(parent(), 'opencode', TTL);
    expect(found?.runId).to.equal('task-2');
    expect(found?.checkpoint.blob.objectId).to.equal('obj-task-2-2');
    expect(found?.snapshot?.snapshot.objectId).to.equal('snap-task-2');
    const pinned = pipeshubRunSpecExtensionSchema.shape.engineCarry.safeParse(found);
    expect(pinned.success, JSON.stringify(pinned)).to.equal(true);
  });

  it('SNT-4 (OWD-12): a snapshot past the child\'s snapshot TTL stays behind; the checkpoint is still carried', async () => {
    const { carry } = world([
      { runId: 'turn-1', executor: 'loop' },
      { runId: 'task-1', parentRunId: 'turn-1', executor: 'opencode', checkpoint: true, snapshot: true, snapshotAgeDays: 8 },
    ]);
    const found = await carry(parent(), 'opencode', TTL);
    expect(found?.checkpoint.blob.objectId).to.equal('obj-task-1-2');
    expect(found).to.not.have.property('snapshot');
    expect((await carry(parent(), 'opencode', 30))?.snapshot?.snapshot.objectId).to.equal('snap-task-1');
  });

  it("another person's code_task child is skipped, never carried", async () => {
    const { carry } = world([
      { runId: 'turn-1', executor: 'loop' },
      { runId: 'task-1', parentRunId: 'turn-1', executor: 'opencode', checkpoint: true },
      { runId: 'turn-2', executor: 'loop', actorId: 'user-2' },
      { runId: 'task-2', parentRunId: 'turn-2', executor: 'opencode', actorId: 'user-2', checkpoint: true },
    ]);
    expect((await carry(parent(), 'opencode', TTL))?.runId).to.equal('task-1');
    expect(await carry(parent({ actorId: 'user-3' }), 'opencode', TTL)).to.equal(null);
  });

  it('the latest engine child ran another engine: nothing, not an older history that skips it', async () => {
    const { carry } = world([
      { runId: 'turn-1', executor: 'loop' },
      { runId: 'task-1', parentRunId: 'turn-1', executor: 'opencode', checkpoint: true },
      { runId: 'turn-2', executor: 'loop' },
      { runId: 'task-2', parentRunId: 'turn-2', executor: 'codex', checkpoint: true },
    ]);
    expect(await carry(parent(), 'opencode', TTL)).to.equal(null);
    expect((await carry(parent(), 'codex', TTL))?.runId).to.equal('task-2');
  });

  it('loop sub-agents hold no engine state and are passed over', async () => {
    const { carry } = world([
      { runId: 'turn-1', executor: 'loop' },
      { runId: 'task-1', parentRunId: 'turn-1', executor: 'opencode', checkpoint: true },
      { runId: 'sub-1', parentRunId: 'turn-1', executor: 'loop' },
    ]);
    expect((await carry(parent(), 'opencode', TTL))?.runId).to.equal('task-1');
  });

  it("an engine root run's children work on a fork under a narrowed grant: not the conversation's engine", async () => {
    const { carry } = world([
      { runId: 'eng-1', executor: 'opencode' },
      { runId: 'task-1', parentRunId: 'eng-1', executor: 'opencode', checkpoint: true },
    ]);
    expect(await carry(parent(), 'opencode', TTL)).to.equal(null);
  });

  it('a child that left no state carries nothing', async () => {
    const { carry } = world([
      { runId: 'turn-1', executor: 'loop' },
      { runId: 'task-0', parentRunId: 'turn-1', executor: 'opencode', checkpoint: true },
      { runId: 'task-1', parentRunId: 'turn-1', executor: 'opencode' },
    ]);
    expect(await carry(parent(), 'opencode', TTL)).to.equal(null);
  });

  it('a forked conversation is another session, and a child of a child carries nothing', async () => {
    const { carry, reads } = world([
      { runId: 'turn-1', executor: 'loop' },
      { runId: 'task-1', parentRunId: 'turn-1', executor: 'opencode', checkpoint: true },
    ]);
    expect(await carry(parent({ sessionId: 'ses-fork' }), 'opencode', TTL)).to.equal(null);
    expect(await carry(parent({ parentRunId: 'turn-0' }), 'opencode', TTL)).to.equal(null);
    expect(reads).to.deep.equal([]);
  });
});
