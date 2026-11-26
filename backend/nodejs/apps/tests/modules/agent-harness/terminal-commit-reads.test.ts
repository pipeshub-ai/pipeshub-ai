/**
 * PERF-B (QA22-F4): the commit that ends a run projects its transcript from
 * what this Node already read of the run's journal plus what it has not, and
 * the transcript is the same whatever had been read.
 */
import { expect } from 'chai';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { HarnessRun, RunTransition } from '../../../src/modules/agent-harness/domain/execution';
import { JournalEvent, JournalEventInput } from '../../../src/modules/agent-harness/domain/journal';
import { ExecutionStore } from '../../../src/modules/agent-harness/ports/execution-store';
import { RunTranscript } from '../../../src/modules/agent-harness/ports/conversation-writer';
import { readJournal } from '../../contracts/support/durable';
import {
  contractClock,
  createContractHarness,
  DURABLE_CAPABILITIES,
} from './support/execution-store-contract';
import { inMemoryBackend } from './support/in-memory-backend';
import { testCipher } from './support/data-protection';
import { HARNESS_ENABLED } from './support/rollout';

const SCENARIOS = ['agent_quick_tool_call', 'agent_react_tool_error_then_success', 'agent_cancel_mid_stream'] as const;
const RUN_ID = 'run-terminal-reads';
const WORKER = { workerId: 'worker-a', attemptId: 'attempt-1' };

const settle = async (): Promise<void> => {
  for (let i = 0; i < 10; i += 1) await new Promise((resolve) => setImmediate(resolve));
};

const asInput = (event: JournalEvent): JournalEventInput => ({
  type: event.type,
  schemaVersion: event.schemaVersion,
  critical: event.critical,
  visibility: event.visibility,
  ...(event.labels !== undefined ? { labels: event.labels } : {}),
  ...(event.data !== undefined ? { data: event.data } : {}),
});

const ENDINGS: Record<string, RunTransition> = {
  'run.completed': { status: 'completed', outcome: 'answer' },
  'run.cancelled': { status: 'cancelled' },
};

/** The recorded run as its worker committed it: one commit an event, the last with the run's ending. */
function workerCommits(scenario: string): Array<{ events: JournalEventInput[]; cancelFirst: boolean; transition?: RunTransition }> {
  const recorded = readJournal(scenario).events;
  const commits: Array<{ events: JournalEventInput[]; cancelFirst: boolean; transition?: RunTransition }> = [];
  let cancelNext = false;
  for (const event of recorded) {
    if (event.type === 'run.cancel_requested') cancelNext = true;
    if (event.writer !== 'worker') continue;
    commits.push({ events: [asInput(event)], cancelFirst: cancelNext });
    cancelNext = false;
  }
  const ending = ENDINGS[recorded[recorded.length - 1]!.type];
  if (ending === undefined) throw new Error(`${scenario} ends with an event this replay does not know`);
  // The worker ends the run in the commit that carries its last events.
  const last = commits.pop()!;
  const before = commits.pop()!;
  commits.push({ events: [...before.events, ...last.events], cancelFirst: before.cancelFirst || last.cancelFirst, transition: ending });
  return commits;
}

interface Replay {
  transcript: RunTranscript;
  /** Journal events of the run the ending commit's Node was handed for the first time while that commit ran. */
  handedAtEnd: number;
  /** The run's journal events before the ending commit. */
  priorEvents: number;
}

/** Which of two Nodes takes the commit at `index` of `count`; they share only the store. */
type Routing = (index: number, count: number) => 0 | 1;

const ROUTINGS: Record<string, Routing> = {
  'one Node takes every commit': () => 0,
  'a Node that read nothing takes the ending commit': (index, count) => (index === count - 1 ? 1 : 0),
  'two Nodes take turns': (index) => (index % 2 === 0 ? 0 : 1),
  'the second Node takes over half way': (index, count) => (index < count / 2 ? 0 : 1),
};

/** Replays `scenario` through the service, each commit on the Node `routing` names. */
async function replay(scenario: string, routing: Routing): Promise<Replay> {
  const clock = contractClock();
  const backend = inMemoryBackend(clock);
  const h = createContractHarness(backend, clock);
  h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
  let handed = 0;
  /** A Node's view of the journal, counting the events handed to it that it had not been handed before. */
  const journalOf = (seen: Set<number>) => ({
    readStream: backend.journal.readStream.bind(backend.journal),
    readRun: async (...args: Parameters<typeof backend.journal.readRun>) => {
      const page = await backend.journal.readRun(...args);
      for (const event of page) {
        if (!seen.has(event.seq)) handed += 1;
        seen.add(event.seq);
      }
      return page;
    },
  });
  const transcripts: RunTranscript[] = [];
  const store = new Proxy(backend.executionStore, {
    get: (target, property, receiver) =>
      property === 'commit'
        ? (...args: Parameters<ExecutionStore['commit']>) => {
            if (args[2] !== undefined) transcripts.push(args[2]);
            return target.commit(...args);
          }
        : (Reflect.get(target, property, receiver) as unknown),
  }) as ExecutionStore;
  const node = (): ExecutionService =>
    new ExecutionService({
      store,
      journal: journalOf(new Set()),
      journals: new JournalService(backend.payloads, testCipher()),
      capabilities: DURABLE_CAPABILITIES,
    });
  const nodes = [node(), node()] as const;

  const admitted = await h.admit({ input: { query: readJournal(scenario).query, recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } } }, RUN_ID);
  let run: HarnessRun = (await nodes[0].claim({ orgId: admitted.run.orgId, runId: RUN_ID, ...WORKER, leaseDurationMs: 60_000 })).run;
  const commits = workerCommits(scenario);
  let priorEvents = 0;
  let handedAtEnd = 0;
  for (const [index, commit] of commits.entries()) {
    const service = nodes[routing(index, commits.length)];
    if (commit.cancelFirst) {
      run = (
        await service.control({
          commandId: `cancel-${String(index)}`,
          dispatchCommandId: `cancel-${String(index)}:dispatch`,
          orgId: run.orgId,
          runId: RUN_ID,
          expectedRevision: run.revision,
          action: 'cancel',
        })
      ).run;
    }
    // What the last commit's read-ahead does is done before the next commit, as a worker's next model call gives it time to be.
    await settle();
    const ending = commit.transition !== undefined;
    if (ending) {
      priorEvents = (await backend.journal.readRun(run.orgId, RUN_ID, 0, 10_000)).length;
      handed = 0;
    }
    run = (
      await service.commit({
        commandId: `commit-${String(index)}`,
        orgId: run.orgId,
        runId: RUN_ID,
        ...WORKER,
        fence: run.fence,
        events: commit.events,
        legacyEvents: [],
        outbox: [],
        ...(commit.transition !== undefined ? { transition: commit.transition } : {}),
      })
    ).run;
    if (ending) handedAtEnd = handed;
  }
  expect(transcripts, 'one transcript, written by the ending commit').to.have.length(1);
  return { transcript: transcripts[0]!, handedAtEnd, priorEvents };
}

describe('PERF-B: the commit that ends a run reads only what its Node has not read', () => {
  for (const scenario of SCENARIOS) {
    describe(scenario, () => {
      it('PERFB-12: on the Node that took the run commits, the ending commit reads no earlier event from the store', async () => {
        const warm = await replay(scenario, ROUTINGS['one Node takes every commit']!);
        expect(warm.priorEvents).to.be.greaterThan(10);
        expect(warm.handedAtEnd).to.equal(0);
      });

      it('PERFB-12: on a Node that read nothing of the run, the ending commit reads the run once', async () => {
        const cold = await replay(scenario, ROUTINGS['a Node that read nothing takes the ending commit']!);
        expect(cold.handedAtEnd).to.equal(cold.priorEvents);
      });

      it('PERFB-12: a Node that took every other commit reads only the commit it did not take', async () => {
        const half = await replay(scenario, ROUTINGS['two Nodes take turns']!);
        expect(half.handedAtEnd).to.be.greaterThan(0).and.lessThan(4);
      });

      it('PERFB-13: the transcript is the same whatever its Node had read of the run', async () => {
        const cold = await replay(scenario, ROUTINGS['a Node that read nothing takes the ending commit']!);
        expect(cold.transcript.kind).to.equal('answer');
        for (const [name, routing] of Object.entries(ROUTINGS)) {
          expect((await replay(scenario, routing)).transcript, name).to.deep.equal(cold.transcript);
        }
      });
    });
  }
});
