/**
 * UI-B5 (ADR-UI-01 D5, D6; UIP2-FU-1, UIP2-FU-4): a job's title on the person's list, how its goal changed (the
 * messages added to it, the choices made when it asked first, the jobs it was re-planned into) and one request's own
 * view ("N of M back"). Pure readings, and the services over fakes of the stores they read.
 */
import { expect } from 'chai';
import { InMemoryConversationSeenMarks } from '../../../src/modules/agent-harness/adapters/in-memory-conversation-seen';
import { JobBriefService } from '../../../src/modules/agent-harness/application/job-briefs';
import { RunListService } from '../../../src/modules/agent-harness/application/run-lists';
import { jobBriefHistorySchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/job-brief-history';
import { personRunsSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/person-runs';
import { requestJobsSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/request-jobs';
import {
  HarnessNotFoundError,
  HarnessUnavailableError,
} from '../../../src/modules/agent-harness/domain/errors';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  jobTitle,
  MAX_JOB_TITLE,
} from '../../../src/modules/agent-harness/domain/job-brief';
import { inlineContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import {
  type ListedJob,
  listedCarry,
} from '../../../src/modules/agent-harness/domain/run-lists';
import { jobGoalChanges } from '../../../src/modules/agent-harness/projection/job-brief-history';
import type { RunLists } from '../../../src/modules/agent-harness/ports/run-lists';
import { ActivityJournal } from './support/activity-journal';

const ORG = 'org-1';
const SESSION = 'ses-1';
const scope = { orgId: ORG, actorId: 'user-1', sessionId: SESSION };

const run = (overrides: Partial<HarnessRun> = {}): HarnessRun =>
  ({
    runId: 'run-1',
    orgId: ORG,
    sessionId: SESSION,
    actorId: 'user-1',
    status: 'running',
    outcome: null,
    createdAt: 1_000,
    updatedAt: 1_000,
    input: {
      query: 'Compare four competitors’ pricing',
      recordIds: [],
      filters: {},
      attachments: [],
      model: { chatMode: 'auto' },
    },
    ...overrides,
  }) as HarnessRun;

const job = (
  runId: string,
  goal: string | null,
  overrides: Partial<HarnessRun> = {},
  continuesRunId: string | null = null,
): ListedJob => ({
  run: run({
    runId,
    parent: { runId: 'run-1', invocationId: `t1.${runId}`, depth: 1 },
    input:
      goal === null
        ? null
        : ({
            query: goal,
            recordIds: [],
            filters: {},
            attachments: [],
            model: { chatMode: 'auto' },
          } as never),
    ...overrides,
  }),
  engine: 'claude_code',
  continuesRunId,
});

describe("UI-B5: job titles, how a job changed, a request's jobs (ADR-UI-01 D5, D6)", () => {
  describe('pure readings', () => {
    it("B5-J1: a title is the goal's first line, spaces folded, at most 200 code points; no goal, no title", () => {
      expect(jobTitle('\n  Fix   PAY-1423\nthen open a PR')).to.equal(
        'Fix PAY-1423',
      );
      const long = jobTitle('é'.repeat(500)) as string;
      expect(Array.from(long)).to.have.length(MAX_JOB_TITLE);
      expect(long.endsWith('…')).to.equal(true);
      expect(jobTitle('   \n ')).to.equal(undefined);
      expect(jobTitle(null)).to.equal(undefined);
    });

    it("B5-J2: the job a pinned spec continues is its engineCarry's; anything else continues none", () => {
      expect(
        listedCarry({
          extensions: { 'x.pipeshub': { engineCarry: { runId: 'run-0' } } },
        }),
      ).to.equal('run-0');
      expect(listedCarry({ extensions: { 'x.pipeshub': {} } })).to.equal(null);
      expect(listedCarry('garbage')).to.equal(null);
      expect(listedCarry(null)).to.equal(null);
    });

    it("B5-J3: a job's own changes: a message added and a choice made when it asked, oldest first, never an open ask", () => {
      const journal = new ActivityJournal('run-2', SESSION, ORG);
      journal.turn(1, [
        { callId: 'c-1', name: 'github__create_pull_request', args: {} },
        { callId: 'c-2', name: 'slack__send_message', args: {} },
      ]);
      journal.add(
        'input.queued',
        {
          messageSeq: 1,
          textRef: inlineContentRef(
            Buffer.from('Keep the public API unchanged'),
          ),
        },
        'node',
      );
      journal
        .held('ix.approve.t1.0', {})
        .decided('ix.approve.t1.0', 'approved');
      journal.held('ix.approve.t1.1', {});
      const changes = jobGoalChanges(journal.events, 'run-2');
      expect(changes.map((c) => c.kind)).to.deep.equal(['message', 'choice']);
      expect(changes[0]).to.deep.include({
        kind: 'message',
        text: 'Keep the public API unchanged',
      });
      expect(changes[1]).to.deep.include({
        kind: 'choice',
        decision: 'approved',
        tool: 'github__create_pull_request',
        action: 'Create pull request',
        app: 'GitHub',
        actionKey: {
          name: 'create_pull_request',
          verb: 'create',
          app: 'github',
        },
      });
      expect(changes[0]!.at).to.be.below(changes[1]!.at);
      expect(jobGoalChanges(journal.events, 'another-run')).to.deep.equal([]);
    });
  });

  describe('JobBriefService', () => {
    const service = (
      options: {
        audience?: 'owner' | 'shared';
        listed?: ListedJob[];
        changes?: Awaited<
          ReturnType<
            NonNullable<
              ConstructorParameters<typeof JobBriefService>[0]['timelines']
            >['goalChanges']
          >
        >;
        sealed?: Set<string>;
        partial?: boolean;
      } = {},
    ) => {
      const runs = new Map<string, HarnessRun>([
        ['run-1', run()],
        ...(options.listed ?? []).map((j) => [j.run.runId, j.run] as const),
      ]);
      return new JobBriefService({
        runs: {
          streamAccess: async (_scope, runId) => {
            const found = runs.get(runId);
            if (found === undefined) throw new HarnessNotFoundError();
            return {
              run: found,
              access: { audience: options.audience ?? 'owner' } as never,
            };
          },
        },
        executions: { get: async (_orgId, runId) => runs.get(runId) ?? null },
        subjects: { readClaimSubject: async () => null },
        ...(options.partial === true
          ? {}
          : {
              jobs: {
                sessionJobs: async (orgId, sessionId) =>
                  orgId === ORG && sessionId === SESSION
                    ? (options.listed ?? [])
                    : [],
              },
              inputs: {
                open: async <
                  R extends Pick<HarnessRun, 'orgId' | 'runId' | 'input'>,
                >(
                  r: R,
                ): Promise<R> => {
                  if (options.sealed?.has(r.runId) === true)
                    throw new Error('sealed under another key');
                  return r;
                },
              },
              timelines: { goalChanges: async () => options.changes ?? [] },
            }),
      });
    };

    it('B5-J4: history: how it started, its own changes and the job the coordinator re-planned it into, its contract', async () => {
      const listed = [
        job(
          'run-4',
          'Also cover the refund path',
          { createdAt: 9_000 },
          'run-2',
        ),
        job('run-3', 'Something else', { createdAt: 8_000 }),
        job('run-2', 'Fix the failing payment test', { createdAt: 2_000 }),
      ];
      const history = await service({
        listed,
        changes: [{ kind: 'message', at: 5_000, text: 'Keep the API' }],
      }).history(scope, 'run-2');
      expect(jobBriefHistorySchema.parse(history)).to.deep.equal({
        runId: 'run-2',
        startedAs: 'Fix the failing payment test',
        changes: [
          { kind: 'message', at: 5_000, text: 'Keep the API' },
          {
            kind: 'replanned',
            at: 9_000,
            runId: 'run-4',
            goal: 'Also cover the refund path',
          },
        ],
      });
    });

    it("B5-J5: the history is the owner's alone and a job's only; a deployment without the reads answers unavailable", async () => {
      const listed = [job('run-2', 'Fix it')];
      for (const [label, attempt] of [
        [
          'shared reader',
          () => service({ audience: 'shared', listed }).history(scope, 'run-2'),
        ],
        ['a root run', () => service({ listed }).history(scope, 'run-1')],
        ['unknown run', () => service({ listed }).history(scope, 'run-9')],
      ] as const) {
        let error: unknown;
        try {
          await attempt();
        } catch (caught) {
          error = caught;
        }
        expect(error, label).to.be.instanceOf(HarnessNotFoundError);
      }
      let unavailable: unknown;
      try {
        await service({ listed, partial: true }).history(scope, 'run-2');
      } catch (caught) {
        unavailable = caught;
      }
      expect(unavailable).to.be.instanceOf(HarnessUnavailableError);
    });

    it('B5-J6: a request\'s jobs, oldest first, with their titles and "N of M back"; another request\'s are not its', async () => {
      const listed = [
        job('run-5', 'Pricing of Delta', {
          status: 'running',
          createdAt: 5_000,
        }),
        job('run-4', null, {
          status: 'failed',
          createdAt: 4_000,
          updatedAt: 4_500,
        }),
        job(
          'run-3',
          'Pricing of Beta',
          { status: 'completed', createdAt: 3_000, updatedAt: 3_900 },
          'run-2',
        ),
        job('run-9', 'Another request’s', {
          parent: { runId: 'run-8', invocationId: 't1', depth: 1 },
          createdAt: 2_000,
        }),
      ];
      const view = requestJobsSchema.parse(
        await service({ listed, sealed: new Set(['run-5']) }).requestJobs(
          scope,
          'run-1',
        ),
      );
      expect(view.request).to.deep.equal({
        runId: 'run-1',
        sessionId: SESSION,
        state: 'working',
        status: 'running',
        ask: 'Compare four competitors’ pricing',
      });
      expect(
        view.jobs.map((j) => [
          j.runId,
          j.title,
          j.state,
          j.endedAt,
          j.continuesRunId,
        ]),
      ).to.deep.equal([
        ['run-3', 'Pricing of Beta', 'finished', 3_900, 'run-2'],
        ['run-4', null, 'failed', 4_500, null],
        ['run-5', null, 'working', null, null],
      ]);
      expect([view.back, view.total]).to.deep.equal([2, 3]);
      let error: unknown;
      try {
        await service({ audience: 'shared', listed }).requestJobs(
          scope,
          'run-1',
        );
      } catch (caught) {
        error = caught;
      }
      expect(error).to.be.instanceOf(HarnessNotFoundError);
    });
  });

  describe('RunListService: a job lists with its title (UIP2-FU-1)', () => {
    it("B5-J7: each job of the page carries its goal's first line; a chat answer none; a goal that cannot be opened lists without one", async () => {
      const page = [
        {
          run: run({
            runId: 'run-3',
            parent: { runId: 'run-1', invocationId: 't1', depth: 1 },
            input: { query: 'Sealed goal' } as never,
          }),
          engine: null,
        },
        {
          run: run({
            runId: 'run-2',
            parent: { runId: 'run-1', invocationId: 't1', depth: 1 },
            input: { query: 'Fix PAY-1423\nand open a PR' } as never,
          }),
          engine: 'codex',
        },
        { run: run(), engine: null },
      ];
      const lists = {
        personRuns: async () => page,
        openRunsOfSessions: async () => [],
        lastEndedRuns: async () => [],
        sessionJobs: async () => [],
      } as RunLists;
      const service = new RunListService({
        runs: lists,
        seen: new InMemoryConversationSeenMarks(),
        access: { assertAccess: async () => undefined } as never,
        inputs: {
          open: async <R extends Pick<HarnessRun, 'orgId' | 'runId' | 'input'>>(
            r: R,
          ): Promise<R> => {
            if (r.runId === 'run-3')
              throw new Error('sealed under another key');
            return r;
          },
        },
      });
      const { runs } = personRunsSchema.parse(
        await service.personRuns({ orgId: ORG, actorId: 'user-1' }, {}),
      );
      expect(runs.map((r) => [r.runId, r.title])).to.deep.equal([
        ['run-3', undefined],
        ['run-2', 'Fix PAY-1423'],
        ['run-1', undefined],
      ]);
    });
  });
});
