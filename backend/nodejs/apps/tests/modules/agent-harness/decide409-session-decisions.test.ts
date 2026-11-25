/**
 * DECIDE409, the person's side: "Allow all N" is one request, a decision someone already made stands and is
 * answered as success, and a run that ended says so (410) instead of a generic 409. The store side (decisions
 * commute, one transaction for a batch) is in the execution-store contract (`decisions.ts`, DC-*).
 */
import { expect } from 'chai';
import sinon from 'sinon';
import {
  batchDecisionCommandId,
  DecideInteractions,
  decisionCommandId,
  SessionInteractionService,
} from '../../../src/modules/agent-harness/application/session-interactions';
import { InMemoryApproverDirectory } from '../../../src/modules/agent-harness/adapters/in-memory-interactions';
import {
  HarnessConflictError,
  HarnessDecisionRefusedError,
  HarnessNotFoundError,
} from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { transportError } from '../../../src/modules/agent-harness/transport/errors';
import { AgentHarnessSessionController } from '../../../src/modules/agent-harness/transport/session-controller';
import { decideSessionInteractionsSchema } from '../../../src/modules/agent-harness/transport/schemas';

const SEVEN = Array.from({ length: 7 }, (_, i) => `ix.approve.t2.${String(i)}`);
const RUN = {
  orgId: 'org-1',
  sessionId: 'ses-1',
  runId: 'run-1',
  actorId: 'u-actor',
  harnessMajor: 2,
  status: 'running',
  revision: 9,
} as unknown as HarnessRun;
const RECEIPT = { commandId: 'c', run: RUN, firstEventSeq: 1, lastEventSeq: 1 };

const batch = (over: Partial<DecideInteractions> = {}): DecideInteractions => ({
  orgId: 'org-1',
  actorId: 'u-actor',
  sessionId: 'ses-1',
  runId: 'run-1',
  interactionIds: SEVEN,
  decision: 'approved',
  idempotencyKey: 'batch:approved',
  channel: 'web',
  ...over,
});

function world(
  options: {
    run?: HarnessRun;
    resolveAll?: sinon.SinonStub;
    decided?: Map<string, string>;
    access?: { audience: string } | null;
    role?: string;
    approvers?: ConstructorParameters<typeof SessionInteractionService>[0]['approvers'];
  } = {},
) {
  const run = options.run ?? RUN;
  const resolveAll = options.resolveAll ?? sinon.stub().resolves(RECEIPT);
  const get = sinon.stub().resolves(run);
  const interactionDecisions = sinon
    .stub()
    .resolves(options.decided ?? new Map());
  const service = new SessionInteractionService({
    executions: { get, interactionDecisions } as never,
    interactions: { resolveAll, resolve: resolveAll } as never,
    sessions: {
      findStreamAccess: sinon
        .stub()
        .resolves(
          options.access === undefined ? { audience: 'owner' } : options.access,
        ),
    } as never,
    roles: { roleOf: sinon.stub().resolves(options.role ?? 'owner') },
    approvers: options.approvers ?? new InMemoryApproverDirectory(new Map(), new Map()),
  });
  return { service, resolveAll, interactionDecisions };
}

const notOpen = () =>
  new HarnessConflictError(
    'INTERACTION_NOT_OPEN',
    'The run has no open interaction with that id',
  );

describe('DECIDE409: deciding as a person', () => {
  it('SD-1: "Allow all 7" is one resolution of all seven, under the batch command id', async () => {
    const { service, resolveAll } = world();
    const decided = await service.decideAll(batch());
    expect(resolveAll.callCount).to.equal(1);
    expect(resolveAll.firstCall.args[0]).to.include({
      decision: 'approved',
      commandId: batchDecisionCommandId(batch()),
    });
    expect(resolveAll.firstCall.args[0].interactionIds).to.deep.equal(SEVEN);
    expect(decided.interactions).to.deep.equal(
      SEVEN.map((interactionId) => ({ interactionId, decision: 'approved' })),
    );
  });

  it('SD-2: the batch command id names the set, not its order, and is not any card’s own', () => {
    const reversed = batch({ interactionIds: [...SEVEN].reverse() });
    expect(batchDecisionCommandId(reversed)).to.equal(
      batchDecisionCommandId(batch()),
    );
    expect(
      batchDecisionCommandId(batch({ interactionIds: SEVEN.slice(1) })),
    ).not.to.equal(batchDecisionCommandId(batch()));
    expect(
      batchDecisionCommandId(batch({ interactionIds: [SEVEN[0]!] })),
    ).not.to.equal(decisionCommandId({ ...batch(), interactionId: SEVEN[0]! }));
  });

  it('SD-3: deciding an interaction already decided answers its decision, not an error', async () => {
    const { service } = world({
      resolveAll: sinon.stub().rejects(notOpen()),
      decided: new Map([[SEVEN[0]!, 'denied']]),
    });
    const decided = await service.decide({
      ...batch(),
      interactionId: SEVEN[0]!,
    });
    expect(decided).to.deep.equal({
      receipt: null,
      interaction: {
        interactionId: SEVEN[0],
        decision: 'denied',
        alreadyDecided: true,
      },
    });
  });

  it('SD-4: a batch with one card decided elsewhere decides the other six and reports the one', async () => {
    const resolveAll = sinon.stub();
    resolveAll.onFirstCall().rejects(notOpen());
    resolveAll.onSecondCall().resolves(RECEIPT);
    const { service } = world({
      resolveAll,
      decided: new Map([[SEVEN[3]!, 'approved']]),
    });
    const decided = await service.decideAll(batch());
    expect(resolveAll.secondCall.args[0].interactionIds).to.deep.equal(
      SEVEN.filter((_, i) => i !== 3),
    );
    expect(resolveAll.secondCall.args[0].commandId).to.equal(
      resolveAll.firstCall.args[0].commandId,
    );
    expect(decided.receipt).to.equal(RECEIPT);
    expect(decided.interactions[3]).to.deep.equal({
      interactionId: SEVEN[3],
      decision: 'approved',
      alreadyDecided: true,
    });
  });

  it('SD-5: an undecided interaction of a run that ended is 410 RUN_ENDED', async () => {
    const ended = { ...RUN, status: 'completed' } as HarnessRun;
    const { service } = world({
      run: ended,
      resolveAll: sinon.stub().rejects(notOpen()),
    });
    let caught: unknown;
    try {
      await service.decide({ ...batch(), interactionId: SEVEN[0]! });
    } catch (error) {
      caught = error;
    }
    expect((caught as HarnessConflictError).code).to.equal('RUN_ENDED');
    expect(transportError(caught)).to.include({
      statusCode: 410,
      code: 'HTTP_AGENT_HARNESS_RUN_ENDED',
    });
  });

  it('SD-6: an interaction never asked of a running run stays 409', async () => {
    const { service } = world({ resolveAll: sinon.stub().rejects(notOpen()) });
    let caught: unknown;
    try {
      await service.decide({ ...batch(), interactionId: 'ix.never' });
    } catch (error) {
      caught = error;
    }
    expect((caught as HarnessConflictError).code).to.equal(
      'INTERACTION_NOT_OPEN',
    );
    expect(transportError(caught)).to.include({ statusCode: 409 });
  });

  it('SD-7: one call the person may not decide refuses the whole batch, and nothing is resolved', async () => {
    const operatorOnly = world();
    let caught: unknown;
    try {
      await operatorOnly.service.decideAll(
        batch({ interactionIds: [...SEVEN, 'ix.unknown.t2.7'] }),
      );
    } catch (error) {
      caught = error;
    }
    expect((caught as HarnessDecisionRefusedError).code).to.equal(
      'OPERATOR_REQUIRED',
    );
    expect(operatorOnly.resolveAll.called).to.equal(false);

    const viewer = world({
      role: 'viewer',
      run: { ...RUN, actorId: 'someone-else' } as HarnessRun,
    });
    caught = undefined;
    try {
      await viewer.service.decideAll(batch());
    } catch (error) {
      caught = error;
    }
    expect((caught as HarnessDecisionRefusedError).code).to.equal(
      'APPROVER_REQUIRED',
    );
    expect(viewer.resolveAll.called).to.equal(false);
  });

  it('the usual refusal says only the person the run acts for approves; a directory that names more approvers words its own', async () => {
    const refusal = async (approvers?: ConstructorParameters<typeof SessionInteractionService>[0]['approvers']) => {
      const w = world({ role: 'viewer', run: { ...RUN, actorId: 'someone-else' } as HarnessRun, ...(approvers ? { approvers } : {}) });
      try {
        await w.service.decideAll(batch());
      } catch (error) {
        return (error as HarnessDecisionRefusedError).message;
      }
      return null;
    };
    expect(await refusal()).to.equal('Only the person this run acts for may approve');
    const named = {
      isApprover: async () => false,
      isOrgAdmin: async () => false,
      refusalWords: { APPROVER_REQUIRED: 'Only the person this run acts for, or a named approver, may approve' },
    };
    expect(await refusal(named)).to.equal('Only the person this run acts for, or a named approver, may approve');
  });

  it('SD-8: a stranger to the session, or a run of another session, is not told the run exists', async () => {
    const stranger = world({
      access: null,
      run: { ...RUN, actorId: 'someone-else' } as HarnessRun,
    });
    let caught: unknown;
    try {
      await stranger.service.decideAll(batch());
    } catch (error) {
      caught = error;
    }
    expect(caught).to.be.instanceOf(HarnessNotFoundError);
    const elsewhere = world({
      run: { ...RUN, sessionId: 'other-session' } as HarnessRun,
    });
    caught = undefined;
    try {
      await elsewhere.service.decideAll(batch());
    } catch (error) {
      caught = error;
    }
    expect(caught).to.be.instanceOf(HarnessNotFoundError);
    expect(stranger.resolveAll.called || elsewhere.resolveAll.called).to.equal(
      false,
    );
  });
});

describe('DECIDE409: the batch route', () => {
  const params = { agentKey: 'a1', sessionId: 's1', runId: 'r1' };
  const parse = (body: Record<string, unknown>) =>
    decideSessionInteractionsSchema.safeParse({ params, body }).success;

  it('SR-1: takes 1 to 100 distinct ids, approve or deny, and conversation only with an approval', () => {
    expect(
      parse({
        interactionIds: SEVEN,
        decision: 'approved',
        idempotencyKey: 'k',
      }),
    ).to.equal(true);
    expect(
      parse({ interactionIds: SEVEN, decision: 'denied', idempotencyKey: 'k' }),
    ).to.equal(true);
    expect(
      parse({
        interactionIds: SEVEN,
        decision: 'approved',
        idempotencyKey: 'k',
        scope: 'conversation',
      }),
    ).to.equal(true);
    expect(
      parse({
        interactionIds: SEVEN,
        decision: 'denied',
        idempotencyKey: 'k',
        scope: 'conversation',
      }),
    ).to.equal(false);
    expect(
      parse({
        interactionIds: SEVEN,
        decision: 'answered',
        idempotencyKey: 'k',
      }),
    ).to.equal(false);
    expect(
      parse({ interactionIds: [], decision: 'approved', idempotencyKey: 'k' }),
    ).to.equal(false);
    expect(
      parse({
        interactionIds: [SEVEN[0], SEVEN[0]],
        decision: 'approved',
        idempotencyKey: 'k',
      }),
    ).to.equal(false);
    const many = Array.from(
      { length: 101 },
      (_, i) => `ix.approve.t1.${String(i)}`,
    );
    expect(
      parse({
        interactionIds: many,
        decision: 'approved',
        idempotencyKey: 'k',
      }),
    ).to.equal(false);
    expect(
      parse({
        interactionIds: ['bad id!'],
        decision: 'approved',
        idempotencyKey: 'k',
      }),
    ).to.equal(false);
  });

  it('SR-2: the org and person come from the token, never the body', async () => {
    expect(
      parse({
        interactionIds: SEVEN,
        decision: 'approved',
        idempotencyKey: 'k',
        orgId: 'org-2',
      }),
    ).to.equal(false);
    const decideAll = sinon
      .stub()
      .resolves({ receipt: null, interactions: [] });
    const controller = new AgentHarnessSessionController(
      {} as never,
      undefined,
      'chat',
      { decideAll } as never,
    );
    const res = { status: sinon.stub().returnsThis(), json: sinon.stub() };
    const next = sinon.stub();
    await controller.decideAll(
      {
        user: { orgId: 'org-1', userId: 'u-1' },
        params: { sessionId: 's1', runId: 'r1' },
        body: {
          interactionIds: SEVEN,
          decision: 'approved',
          idempotencyKey: 'k',
        },
      } as never,
      res as never,
      next,
    );
    expect(next.called).to.equal(false);
    expect(decideAll.firstCall.args[0]).to.include({
      orgId: 'org-1',
      actorId: 'u-1',
      sessionId: 's1',
      runId: 'r1',
      channel: 'web',
    });
    expect(res.json.firstCall.args[0]).to.deep.equal({
      receipt: null,
      interactions: [],
    });

    await controller.decideAll(
      {
        user: { orgId: 'org-1' },
        params: {},
        body: {
          interactionIds: SEVEN,
          decision: 'approved',
          idempotencyKey: 'k',
        },
      } as never,
      res as never,
      next,
    );
    expect(next.firstCall.args[0]).to.have.property('statusCode', 401);
  });
});
