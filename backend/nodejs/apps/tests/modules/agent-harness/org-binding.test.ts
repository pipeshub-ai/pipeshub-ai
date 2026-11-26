/**
 * ADR-SEC-02 (WP-116): every entry point binds the org it works for from server-side state, and a store call with no
 * org bound is refused. Each case starts from an empty context, as the worker's routes, a detached timer and a
 * consumer loop do, and records the org bound at the store call.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import type { NextFunction, Request, Response } from 'express';
import {
  forOrg,
  getCurrentOrgId,
  orgContextStorage,
} from '../../../src/libs/context/orgContext';
import {
  inBoundOrg,
  inBoundOrgUnit,
  OtherOrgBoundError,
  requireBoundOrg,
  UnboundOrgError,
} from '../../../src/libs/repositories/agent-harness/durable-transaction/bound-org';
import { inDurableTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction';
import { orgUnit } from '../../../src/libs/repositories/agent-harness/pg-documents/pg';
import { orgOrJobsUnit } from '../../../src/libs/repositories/agent-harness/pg-documents/org-or-jobs';
import { inRunOrg } from '../../../src/libs/repositories/agent-harness/execution/pg-rows';
import { cpUnit } from '../../../src/libs/repositories/control-plane/pg-units';
import {
  asWorkerNamedOrg,
  requireAttemptToken,
} from '../../../src/modules/agent-harness/transport/internal-auth';
import { AgentHarnessInternalController } from '../../../src/modules/agent-harness/transport/internal-controller';
import { AgentHarnessGatewayTokenController } from '../../../src/modules/agent-harness/transport/gateway-token-controller';
import { AgentHarnessLiveLayerController } from '../../../src/modules/agent-harness/transport/live-layer-controller';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { BudgetLedgerService } from '../../../src/modules/agent-harness/application/budget-ledger-service';
import { GatewayTokenService } from '../../../src/modules/agent-harness/application/gateway-token-service';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';

const ORG_A = 'a'.repeat(24);
const ORG_B = 'b'.repeat(24);

/** Runs `fn` with no org context at all, as a detached timer or the worker's internal listener does. */
const unbound = <T>(fn: () => T): T => orgContextStorage.exit(fn);

const refusal = async (work: () => Promise<unknown>): Promise<unknown> => {
  try {
    await work();
  } catch (error) {
    return error;
  }
  throw new Error('the call was not refused');
};

interface Res {
  statusCode: number;
  body: unknown;
}

function response(): Response & Res {
  const res = {
    statusCode: 0,
    body: undefined as unknown,
    headers: {} as Record<string, string>,
    status(code: number) {
      this.statusCode = code;
      return this;
    },
    json(body: unknown) {
      this.body = body;
      return this;
    },
    end() {
      return this;
    },
    setHeader(name: string, value: string) {
      this.headers[name] = value;
      return this;
    },
  };
  return res as unknown as Response & Res;
}

describe('ADR-SEC-02: a store call with no org bound is refused', () => {
  it('requireBoundOrg refuses no org, refuses another org, and passes the bound one', () => {
    unbound(() => {
      expect(() => requireBoundOrg(ORG_A)).to.throw(UnboundOrgError);
      forOrg(ORG_B, 'test', () => {
        expect(() => requireBoundOrg(ORG_A)).to.throw(OtherOrgBoundError);
        expect(() => requireBoundOrg(ORG_B)).not.to.throw();
      });
    });
  });

  it('the refusal says the entry point must bind the org, in plain words', () => {
    expect(new UnboundOrgError().message).to.equal(
      'a harness store call ran with no organisation bound: its entry point must bind the organisation it works for',
    );
  });

  it('a PostgreSQL store unit (orgUnit) is refused before any connection is taken', async () => {
    let ran = false;
    const error = await unbound(() =>
      refusal(() =>
        orgUnit(undefined, ORG_A, { write: false }, async () => {
          ran = true;
        }),
      ),
    );
    expect(error).to.be.instanceOf(UnboundOrgError);
    expect(ran).to.equal(false);
  });

  it('a per-org call that sweeps also make (orgOrJobsUnit) no longer falls back to the jobs role', async () => {
    const error = await unbound(() =>
      refusal(() =>
        orgOrJobsUnit(
          undefined,
          ORG_A,
          'test',
          { write: false },
          async () => undefined,
        ),
      ),
    );
    expect(error).to.be.instanceOf(UnboundOrgError);
  });

  it('the execution stores (inRunOrg), main-repository calls and a control-plane unit refuse too', async () => {
    for (const call of [
      () => inRunOrg(ORG_A, async () => undefined),
      () => inBoundOrg(ORG_A, async () => undefined),
      () => inBoundOrgUnit(ORG_A, async () => undefined),
      () => cpUnit(undefined, ORG_A, { write: false }, async () => undefined),
    ]) {
      expect(await unbound(() => refusal(call))).to.be.instanceOf(
        UnboundOrgError,
      );
    }
    const other = await forOrg(ORG_B, 'test', () =>
      refusal(() => inRunOrg(ORG_A, async () => undefined)),
    );
    expect(other).to.be.instanceOf(OtherOrgBoundError);
  });

  it('a durable transaction for an org is refused on the default store (MongoDB) too, before it opens', async () => {
    let ran = false;
    const error = await unbound(() =>
      refusal(() =>
        inDurableTransaction(
          async () => {
            ran = true;
          },
          { orgId: ORG_A },
        ),
      ),
    );
    expect(error).to.be.instanceOf(UnboundOrgError);
    expect(ran).to.equal(false);
  });
});

describe('ADR-SEC-02: the worker routes bind the org from server-side state', () => {
  it("an attempt-token route runs as the org of the token's verified claims", async () => {
    const authority = {
      verifyAttemptToken: async (
        _token: string | undefined,
        runId: string,
      ) => ({
        orgId: ORG_A,
        runId,
        attemptId: 'attempt-1',
        fence: 1,
        workerId: 'w',
      }),
    };
    const guard = requireAttemptToken(authority as never);
    const seen = await unbound(
      () =>
        new Promise<string | undefined>((resolve, reject) => {
          const req = {
            headers: { authorization: 'Bearer t' },
            params: { runId: 'run-1' },
          } as unknown as Request;
          void guard(req, response(), ((error?: unknown) => {
            if (error !== undefined) reject(error as Error);
            else resolve(getCurrentOrgId());
          }) as NextFunction);
        }),
    );
    expect(seen).to.equal(ORG_A);
  });

  it('an attempt token whose claims name no org is refused, never served unbound', async () => {
    const guard = requireAttemptToken({
      verifyAttemptToken: async () => ({ runId: 'run-1' }),
    } as never);
    const error = await unbound(
      () =>
        new Promise<unknown>((resolve) => {
          void guard(
            {
              headers: { authorization: 'Bearer t' },
              params: { runId: 'run-1' },
            } as unknown as Request,
            response(),
            ((e?: unknown) => resolve(e)) as NextFunction,
          );
        }),
    );
    expect(error).to.have.property('statusCode', 401);
  });

  it("claim-next's per-run phase runs as the claimed run's org (attempt token, tree read, release on failure)", async () => {
    const seen: Array<[string, string | undefined]> = [];
    const run = {
      orgId: ORG_A,
      runId: 'run-1',
      attemptId: 'a-1',
      workerId: 'w',
      fence: 1,
    };
    const executions = {
      claimNext: async () => {
        seen.push(['claimNext', getCurrentOrgId()]);
        return { run };
      },
      treeSkippedConnects: async () => {
        seen.push(['tree', getCurrentOrgId()]);
        return [];
      },
      release: async () => {
        seen.push(['release', getCurrentOrgId()]);
      },
    };
    const authority = {
      issueFor: async () => {
        seen.push(['issue', getCurrentOrgId()]);
        throw new Error('signer down');
      },
    };
    const controller = new AgentHarnessInternalController(
      executions as never,
      authority as never,
      () => 'a-1',
    );
    const res = response();
    let failed: unknown;
    await unbound(() =>
      controller.claimNext(
        {
          body: { role: 'dispatched', workerId: 'w', leaseDurationMs: 30_000 },
        } as never,
        res,
        ((error?: unknown) => {
          failed = error;
        }) as NextFunction,
      ),
    );
    expect(failed).to.not.equal(undefined);
    expect(seen).to.deep.equal([
      ['claimNext', undefined],
      ['issue', ORG_A],
      ['release', ORG_A],
    ]);
  });

  it("the claim-next pick tries each candidate as that candidate's org", async () => {
    const seen: Array<[string, string | undefined]> = [];
    const store = {
      nextClaimable: async () => [
        { orgId: ORG_A, runId: 'run-a' },
        { orgId: ORG_B, runId: 'run-b' },
      ],
      readClaimSubject: async (orgId: string) => {
        seen.push([orgId, getCurrentOrgId()]);
        return null;
      },
    };
    const service = new ExecutionService({
      store: store as never,
      journal: {} as never,
      journals: {} as never,
    });
    const claimed = await unbound(() =>
      service.claimNext({
        role: 'dispatched',
        workerId: 'w',
        attemptId: 'a-1',
        leaseDurationMs: 30_000,
      } as never),
    );
    expect(claimed).to.equal(null);
    expect(seen).to.deep.equal([
      [ORG_A, ORG_A],
      [ORG_B, ORG_B],
    ]);
  });

  it('a claim by name, the grant auditor, the proxy token and the live layer run as the org the worker names', async () => {
    const seen: Array<[string, string | undefined]> = [];
    const controller = new AgentHarnessInternalController(
      {
        claim: async () => {
          seen.push(['claim', getCurrentOrgId()]);
          throw new HarnessNotFoundError();
        },
        auditEvidence: async () => {
          seen.push(['audit', getCurrentOrgId()]);
          return null;
        },
      } as never,
      {} as never,
      () => 'a-1',
    );
    const ignore = (() => undefined) as NextFunction;
    await unbound(() =>
      controller.claim(
        {
          params: { runId: 'run-1' },
          body: { orgId: ORG_A, workerId: 'w', leaseDurationMs: 30_000 },
        } as never,
        response(),
        ignore,
      ),
    );
    await unbound(() =>
      controller.auditEvidence(
        { params: { runId: 'run-1' }, query: { orgId: ORG_B } } as never,
        response(),
        ignore,
      ),
    );
    const gateway = new AgentHarnessGatewayTokenController({
      forAttempt: async () => {
        seen.push(['gateway', getCurrentOrgId()]);
        return { token: 't', expiresAt: 1 };
      },
    } as never);
    await unbound(() =>
      gateway.forAttempt(
        {
          params: { runId: 'run-1' },
          body: { orgId: ORG_A, attemptId: 'a-1', bindingProof: 'v1.x' },
        } as never,
        response(),
        ignore,
      ),
    );
    const live = new AgentHarnessLiveLayerController({
      read: async () => {
        seen.push(['live', getCurrentOrgId()]);
        return { revision: 1, layer: null };
      },
    } as never);
    await unbound(() =>
      live.read(
        { params: { orgId: ORG_B }, query: {} } as never,
        response(),
        ignore,
      ),
    );
    expect(seen).to.deep.equal([
      ['claim', ORG_A],
      ['audit', ORG_B],
      ['gateway', ORG_A],
      ['live', ORG_B],
    ]);
    expect(
      await unbound(() =>
        asWorkerNamedOrg(ORG_A, async () => getCurrentOrgId()),
      ),
    ).to.equal(ORG_A);
  });
});

describe("ADR-SEC-02: a gateway's run token binds its run's org", () => {
  it("the budget routes run as the org of the caller's verified token", async () => {
    const seen: Array<string | undefined> = [];
    const service = new BudgetLedgerService({
      ledger: {
        remaining: async () => {
          seen.push(getCurrentOrgId());
          return { limit: null, committed: { usdMicros: 0, tokens: 0 } };
        },
        release: async () => {
          seen.push(getCurrentOrgId());
          return {};
        },
      } as never,
      runs: {
        // SEC-C-11: a budget read checks the run's standing first, as the token's org too.
        pinned: async () => {
          seen.push(getCurrentOrgId());
          return null;
        },
      } as never,
      attempts: {
        verifyAttemptToken: async (
          _token: string | undefined,
          runId: string,
        ) => ({
          orgId: ORG_A,
          runId,
          attemptId: 'a-1',
          fence: 1,
          workerId: 'w',
        }),
      },
      gateways: null,
    });
    await unbound(() => service.release('t', 'run-1', 'k')).catch(
      () => undefined,
    );
    await unbound(() => service.remaining('t', 'run-1')).catch(() => undefined);
    expect(seen.slice(0, 2)).to.deep.equal([ORG_A, ORG_A]);
  });

  it("a gateway's model grant and decision reports run as the org of the run's verified gateway token", async () => {
    const seen: Array<string | undefined> = [];
    const service = new GatewayTokenService({
      tokens: {
        verify: async () => ({
          binding: 'run',
          orgId: ORG_B,
          runId: 'run-1',
          attemptId: 'a-1',
          fence: 1,
        }),
      } as never,
      runs: {
        pinned: async () => {
          seen.push(getCurrentOrgId());
          return null;
        },
      },
      refusals: {} as never,
    });
    const grant = await unbound(() =>
      refusal(() => service.modelGrant('t', 'run-1')),
    );
    const report = await unbound(() =>
      refusal(() => service.recordRefusal('t', 'run-1', {} as never)),
    );
    expect(grant).to.be.instanceOf(HarnessNotFoundError);
    expect(report).to.be.instanceOf(HarnessNotFoundError);
    expect(seen).to.deep.equal([ORG_B, ORG_B]);
  });
});
