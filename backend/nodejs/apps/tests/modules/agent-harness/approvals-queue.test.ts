/**
 * P2.11e AQ-0..AQ-9: the approvals waiting on a viewer, from the runs the
 * queue's reach covers (by default the viewer's own), listed by the decision
 * route's own rule; the decision route admits the org's approvers (and
 * admins, to deny) outside the session.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import 'reflect-metadata';
import { expect } from 'chai';
import { canonicalJsonBytes } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { InMemoryApproverDirectory } from '../../../src/modules/agent-harness/adapters/in-memory-interactions';
import {
  ApprovalQueueService,
  ownRunsOnly,
  type ApprovalQueueReach,
} from '../../../src/modules/agent-harness/application/approval-queue';
import { SessionInteractionService } from '../../../src/modules/agent-harness/application/session-interactions';
import {
  HarnessDecisionRefusedError,
  HarnessNotFoundError,
} from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { inlineContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import { SessionExecutionScope } from '../../../src/modules/agent-harness/ports/chat-session-repository';
import { PendingApprovalFilter } from '../../../src/modules/agent-harness/ports/execution-store';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { assertAnswersContract } from './support/api-contracts';
import { findCanaries, VECTOR_EXAMPLES, VECTOR_MASK } from './support/canary-scan';

const ORG = 'org-1';
const ENGINE_IX = 'ix.approve.k0123456789abcdef0123456789abcdef01234567';
const LOOP_IX = 'ix.approve.t1.0';

let seq = 0;
const event = (runId: string, type: string, data: Record<string, unknown>, writer = 'worker'): JournalEvent =>
  ({
    orgId: ORG,
    sessionId: `s-${runId}`,
    streamId: `s-${runId}`,
    seq: (seq += 1),
    runId,
    attemptId: `${runId}.a1`,
    fence: 1,
    type,
    schemaVersion: 1,
    critical: !type.startsWith('x.'),
    ts: '2026-10-04T12:00:00.000Z',
    writer,
    visibility: 'owner',
    data,
  }) as JournalEvent;

const engineApproval = (runId: string): JournalEvent[] => [
  event(runId, 'turn.started', { turn: 1 }),
  event(runId, 'x.pipeshub.engine.progress', {
    tool: 'code_task',
    taskId: runId,
    kind: 'approval',
    text: 'create_issue',
    data: { interactionId: ENGINE_IX, toolName: 'create_issue', argsJson: '{"title":"Q3"}' },
    turnId: 'trn_1',
    seq: 1,
  }),
  event(runId, 'interaction.requested', {
    interactionId: ENGINE_IX,
    kind: 'approval',
    bindingDigest: `sha256:${'a'.repeat(64)}`,
  }),
];

const loopApproval = (runId: string, resolved = false): JournalEvent[] => [
  event(runId, 'turn.started', { turn: 1 }),
  event(runId, 'model.response', {
    messageRef: inlineContentRef(
      canonicalJsonBytes({ text: '', toolCalls: [{ id: 'c9', name: 'jira_create', args: { summary: 'x' } }] }),
    ),
    toolCalls: [{ callId: 'c9', modelName: 'jira_create', argsDigest: `sha256:${'c'.repeat(64)}` }],
    usage: { inputTokens: 0, outputTokens: 0 },
    provider: 'p',
    transport: 't',
  }),
  event(runId, 'x.pipeshub.tool.call_presented', {
    callId: 'c9',
    toolName: 'jira.create_issue',
    argsJson: '{"summary":"x"}',
    argsSummary: 'Create “x”',
    displayName: 'Created a Jira issue',
  }),
  event(runId, 'interaction.requested', {
    interactionId: LOOP_IX,
    kind: 'approval',
    bindingDigest: `sha256:${'a'.repeat(64)}`,
  }),
  ...(resolved
    ? [event(runId, 'interaction.resolved', { interactionId: LOOP_IX, decision: 'approved', by: 'p_x', channel: 'web' }, 'node')]
    : []),
];

const run = (runId: string, extra: Partial<HarnessRun>): HarnessRun =>
  ({
    runId,
    orgId: ORG,
    sessionId: `s-${runId}`,
    actorId: 'u-member',
    harnessMajor: 2,
    status: 'running',
    revision: 3,
    updatedAt: 100,
    ...extra,
  }) as HarnessRun;

/** The member's agent run (engine approval) and a colleague's chat run (loop approval). */
const RUNS: HarnessRun[] = [
  run('r-member', { agentKey: 'agent-a', openInteractions: [ENGINE_IX], updatedAt: 200 }),
  run('r-colleague', { actorId: 'u-colleague', openInteractions: [LOOP_IX], status: 'waiting_approval' }),
  run('r-stale', { actorId: 'u-colleague', openInteractions: [LOOP_IX], updatedAt: 50 }),
];
const JOURNALS: Record<string, JournalEvent[]> = {
  'r-member': engineApproval('r-member'),
  'r-colleague': loopApproval('r-colleague'),
  'r-stale': loopApproval('r-stale', true),
};
/** session -> participants by role; the colleague's session is shared with the member as a viewer. */
const PARTICIPANTS: Record<string, Record<string, 'owner' | 'viewer'>> = {
  's-r-member': { 'u-member': 'owner' },
  's-r-colleague': { 'u-colleague': 'owner', 'u-member': 'viewer' },
  's-r-stale': { 'u-colleague': 'owner' },
};

/** A reach over the org's runs for the viewers who may decide them, as an edition may bind one. */
const ORG_WIDE: ApprovalQueueReach = {
  othersRuns: (_viewer, standing) => Promise.resolve(standing.isApprover || standing.isOrgAdmin),
};

function world(runs: HarnessRun[] = RUNS, reach: ApprovalQueueReach = ORG_WIDE) {
  const resolved: unknown[] = [];
  const store = {
    pendingApprovalRuns: async (orgId: string, filter: PendingApprovalFilter) =>
      runs
        .filter(
          (r) =>
            r.orgId === orgId &&
            (r.openInteractions?.length ?? 0) > 0 &&
            (filter.actorId === undefined || r.actorId === filter.actorId) &&
            (filter.agentKey === undefined || r.agentKey === filter.agentKey),
        )
        .sort((a, b) => b.updatedAt - a.updatedAt)
        .slice(0, filter.limit),
  };
  const interactions = new SessionInteractionService({
    executions: {
      get: async (orgId: string, runId: string) => runs.find((r) => r.orgId === orgId && r.runId === runId) ?? null,
    } as never,
    interactions: {
      resolve: async (command: unknown) => {
        resolved.push(command);
        return { run: runs[0] } as never;
      },
    } as never,
    sessions: {
      findStreamAccess: async (scope: SessionExecutionScope) => {
        const role = PARTICIPANTS[scope.sessionId]?.[scope.actorId];
        return role === undefined ? null : { audience: role === 'owner' ? 'owner' : 'shared' };
      },
    } as never,
    roles: { roleOf: async (scope: SessionExecutionScope) => PARTICIPANTS[scope.sessionId]?.[scope.actorId] ?? null },
    approvers: new InMemoryApproverDirectory(new Map([[ORG, new Set(['u-approver'])]]), new Map([[ORG, new Set(['u-admin'])]])),
  });
  const queue = new ApprovalQueueService(
    store,
    interactions,
    { approvals: async (r: HarnessRun) => foldTimeline(JOURNALS[r.runId] ?? [], r.runId).approvals },
    undefined,
    reach,
  );
  const pending = (actorId: string, extra: { agentKey?: string; mine?: boolean; orgId?: string } = {}) =>
    queue.pending({ orgId: extra.orgId ?? ORG, actorId, limit: 50, ...extra });
  return { queue, interactions, pending, resolved };
}

const brief = (items: Awaited<ReturnType<ReturnType<typeof world>['pending']>>) => {
  assertAnswersContract('GET', '/api/v1/agent-harness/approvals/pending', 200, { items });
  return items.map((item) => [item.runId, item.interactionId, item.authority, item.argsHidden]);
};

describe('P2.11e approvals queue (AQ)', () => {
  it('AQ-0: with the default reach every viewer, an approver and an admin too, is shown only their own runs', async () => {
    const own = (viewer: string) => world(RUNS, ownRunsOnly).pending(viewer);
    expect(await own('u-approver')).to.deep.equal([]);
    expect(await own('u-admin')).to.deep.equal([]);
    expect(brief(await own('u-member'))).to.deep.equal([['r-member', ENGINE_IX, { approve: true, deny: true }, false]]);
  });

  it('AQ-1: an approver outside the sessions sees every pending approval, both decisions, with arguments', async () => {
    const items = await world().pending('u-approver');
    expect(brief(items)).to.deep.equal([
      ['r-member', ENGINE_IX, { approve: true, deny: true }, false],
      ['r-colleague', LOOP_IX, { approve: true, deny: true }, false],
    ]);
    expect(items[0]).to.include({ toolName: 'create_issue', args: '{"title":"Q3"}', agentKey: 'agent-a', actorId: 'u-member' });
    expect(items[1]).to.include({
      toolName: 'jira.create_issue',
      displayName: 'Created a Jira issue',
      argsSummary: 'Create “x”',
      sessionId: 's-r-colleague',
      runStatus: 'waiting_approval',
    });
    expect(items[1]).to.not.have.property('agentKey');
  });

  it('AQ-2: an admin outside the sessions may only deny and does not see the arguments', async () => {
    const items = await world().pending('u-admin');
    expect(brief(items)).to.deep.equal([
      ['r-member', ENGINE_IX, { approve: false, deny: true }, true],
      ['r-colleague', LOOP_IX, { approve: false, deny: true }, true],
    ]);
    for (const item of items) {
      expect(item).to.not.have.any.keys('args', 'argsSummary');
    }
  });

  it('AQ-3: a member sees only the runs that act for them, never a colleague’s', async () => {
    expect(brief(await world().pending('u-member'))).to.deep.equal([
      ['r-member', ENGINE_IX, { approve: true, deny: true }, false],
    ]);
    expect(await world().pending('u-colleague')).to.have.length(1);
    expect(await world().pending('u-nobody')).to.deep.equal([]);
  });

  it('AQ-4: another org’s viewer sees nothing of this org', async () => {
    expect(await world().pending('u-approver', { orgId: 'org-2' })).to.deep.equal([]);
    const { interactions } = world();
    expect(await interactions.authorityFor({ orgId: 'org-2', actorId: 'u-approver' }, RUNS[0]!)).to.deep.equal({
      approve: false,
      deny: false,
      participant: false,
    });
  });

  it('AQ-5: filters by agent and by “only my runs”', async () => {
    expect(brief(await world().pending('u-approver', { agentKey: 'agent-a' })).map(([id]) => id)).to.deep.equal(['r-member']);
    expect(await world().pending('u-approver', { mine: true })).to.deep.equal([]);
    expect(brief(await world().pending('u-member', { mine: true })).map(([id]) => id)).to.deep.equal(['r-member']);
  });

  it('AQ-7: a run the decision route would refuse (harness v1) is never listed, whatever the viewer’s standing', async () => {
    const legacy = run('r-legacy', { harnessMajor: 1 as never, openInteractions: [LOOP_IX] });
    JOURNALS['r-legacy'] = loopApproval('r-legacy');
    for (const viewer of ['u-approver', 'u-admin', 'u-member']) {
      expect(await world([legacy]).pending(viewer), viewer).to.deep.equal([]);
    }
  });

  it('AQ-6, AQ-7: nothing pending; a resolved interaction is never listed', async () => {
    expect(await world([]).pending('u-approver')).to.deep.equal([]);
    const items = await world([RUNS[2]!]).pending('u-approver');
    expect(items).to.deep.equal([]);
  });

  it('AQ-8, AP-06: every item’s authority is the authority route’s answer; the route adds whether the viewer may edit', async () => {
    for (const viewer of ['u-approver', 'u-admin', 'u-member']) {
      const { pending, interactions } = world();
      for (const item of await pending(viewer)) {
        const scope = {
          orgId: ORG,
          actorId: viewer,
          sessionId: item.sessionId,
          ...(item.agentKey !== undefined ? { agentKey: item.agentKey } : {}),
        };
        const { edit, ...decide } = await interactions.authority(scope, item.runId);
        expect(decide, `${viewer} ${item.runId}`).to.deep.equal(item.authority);
        expect(edit, `${viewer} ${item.runId}`).to.equal(item.actorId === viewer);
      }
    }
  });

  it('AQ-9: the decision route admits the approver and (to deny) the admin outside the session; a member stays out', async () => {
    const { interactions, resolved } = world();
    const decide = (actorId: string, decision: 'approved' | 'denied', runId = 'r-colleague') =>
      interactions.decide({
        orgId: ORG,
        actorId,
        sessionId: `s-${runId}`,
        runId,
        interactionId: LOOP_IX,
        decision,
        idempotencyKey: `${LOOP_IX}:${decision}`,
        channel: 'web',
      });
    await decide('u-approver', 'approved');
    expect(resolved).to.have.length(1);
    let refused: unknown;
    try {
      await decide('u-admin', 'approved');
    } catch (error) {
      refused = error;
    }
    expect(refused).to.be.instanceOf(HarnessDecisionRefusedError);
    await decide('u-admin', 'denied');
    expect(resolved).to.have.length(2);
    for (const stranger of ['u-nobody', 'u-member']) {
      refused = undefined;
      try {
        await decide(stranger, 'denied', 'r-stale');
      } catch (error) {
        refused = error;
      }
      expect(refused, stranger).to.be.instanceOf(HarnessNotFoundError);
    }
    refused = undefined;
    try {
      await interactions.decide({
        orgId: ORG,
        actorId: 'u-approver',
        sessionId: 's-r-member',
        agentKey: 'agent-other',
        runId: 'r-member',
        interactionId: ENGINE_IX,
        decision: 'approved',
        idempotencyKey: 'k',
        channel: 'web',
      });
    } catch (error) {
      refused = error;
    }
    expect(refused, 'another agent’s path').to.be.instanceOf(HarnessNotFoundError);
  });
});

describe('QA-14 QA14-N2: the approvals queue masks credentials in what it shows another person', () => {
  const secretApproval = (runId: string, secret: string): JournalEvent[] => [
    ...loopApproval(runId).slice(0, 2),
    event(runId, 'x.pipeshub.tool.call_presented', {
      callId: 'c9',
      toolName: 'jira.create_issue',
      argsJson: JSON.stringify({ summary: 'x', note: `use ${secret}` }),
      argsSummary: `Create with ${secret}`,
    }),
    event(runId, 'interaction.explained', {
      interactionId: LOOP_IX,
      rule: 'flow',
      sensitivity: 'private',
      sources: [{ title: `Doc ${secret}`, url: `https://docs.example/a?k=${secret}` }],
      moreSources: 0,
      destination: { label: `#ops ${secret}` },
    }),
    event(runId, 'interaction.requested', {
      interactionId: LOOP_IX,
      kind: 'approval',
      bindingDigest: `sha256:${'a'.repeat(64)}`,
    }),
  ];

  for (const example of VECTOR_EXAMPLES) {
    it(`${example.id}: the approver gets the mask in the arguments, the summary and the reason`, async () => {
      const runs = [run('r-secret', { actorId: 'u-colleague', openInteractions: [LOOP_IX] })];
      JOURNALS['r-secret'] = secretApproval('r-secret', example.secret);
      PARTICIPANTS['s-r-secret'] = { 'u-colleague': 'owner' };
      const [item] = await world(runs).pending('u-approver');
      expect(item).to.include({ argsHidden: false });
      expect(findCanaries(item, { secrets: [example.secret] })).to.deep.equal([]);
      expect(JSON.stringify(item)).to.include(VECTOR_MASK);
      expect(item!.reason).to.deep.include({ destination: `#ops ${VECTOR_MASK}` });
    });
  }
});


describe('QA-14 QA14-11: the approvals queue masks a secret-named argument whatever it looks like', () => {
  const names = (
    JSON.parse(
      readFileSync(resolve(__dirname, '../../../../../../contracts/harness/vectors/token-patterns.json'), 'utf8'),
    ) as { secretKeyNames: { matches: string[]; ignores: string[] } }
  ).secretKeyNames;

  for (const key of names.matches) {
    it(`${key}: the approver gets the mask, the other arguments as they were`, async () => {
      const runId = `r-name-${key}`;
      const runs = [run(runId, { actorId: 'u-colleague', openInteractions: [LOOP_IX] })];
      JOURNALS[runId] = [
        ...loopApproval(runId).slice(0, 2),
        event(runId, 'x.pipeshub.tool.call_presented', {
          callId: 'c9',
          toolName: 'jira.create_issue',
          argsJson: JSON.stringify({ summary: 'deploy', auth: { [key]: 'hunter2' } }),
        }),
        event(runId, 'interaction.requested', {
          interactionId: LOOP_IX,
          kind: 'approval',
          bindingDigest: `sha256:${'a'.repeat(64)}`,
        }),
      ];
      PARTICIPANTS[`s-${runId}`] = { 'u-colleague': 'owner' };
      const [item] = await world(runs).pending('u-approver');
      expect(JSON.parse(item!.args!)).to.deep.equal({ summary: 'deploy', auth: VECTOR_MASK });
    });
  }

  it('an ordinary name keeps its value', async () => {
    const runId = 'r-name-plain';
    const plain = Object.fromEntries(names.ignores.map((k) => [k, 'hunter2']));
    const runs = [run(runId, { actorId: 'u-colleague', openInteractions: [LOOP_IX] })];
    JOURNALS[runId] = [
      ...loopApproval(runId).slice(0, 2),
      event(runId, 'x.pipeshub.tool.call_presented', {
        callId: 'c9',
        toolName: 'jira.create_issue',
        argsJson: JSON.stringify(plain),
      }),
      event(runId, 'interaction.requested', {
        interactionId: LOOP_IX,
        kind: 'approval',
        bindingDigest: `sha256:${'a'.repeat(64)}`,
      }),
    ];
    PARTICIPANTS[`s-${runId}`] = { 'u-colleague': 'owner' };
    const [item] = await world(runs).pending('u-approver');
    expect(JSON.parse(item!.args!)).to.deep.equal(plain);
  });
});

describe('QAFIX-6: an engine approval names its tool as the loop does', () => {
  it('the engine line\'s display name reaches the approval; without one the approval has none', () => {
    const named = engineApproval('r-named');
    const line = named[1]!;
    line.data = { ...line.data, data: { ...(line.data!['data'] as Record<string, unknown>), displayName: 'E2Esaas · create_ticket' } };
    expect(foldTimeline(named, 'r-named').approvals[0]).to.include({ toolName: 'create_issue', displayName: 'E2Esaas · create_ticket' });
    expect(foldTimeline(engineApproval('r-plain'), 'r-plain').approvals[0]).to.not.have.property('displayName');
  });
});
