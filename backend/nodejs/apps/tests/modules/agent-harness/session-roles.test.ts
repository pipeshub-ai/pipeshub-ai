import { expect } from 'chai';
import sinon from 'sinon';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import {
  HarnessNotFoundError,
  HarnessSessionRoleRefusedError,
  HarnessUnavailableError,
} from '../../../src/modules/agent-harness/domain/errors';
import { ExecutorResolver, profileExecutor } from '../../../src/modules/agent-harness/domain/run-spec';
import {
  mayManageSession,
  SessionParticipants,
  SessionRole,
  sessionRoleOf,
  turnRefusal,
} from '../../../src/modules/agent-harness/domain/session-roles';
import { SessionRoleLookup } from '../../../src/modules/agent-harness/ports/session-roles';
import { transportError } from '../../../src/modules/agent-harness/transport/errors';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { fixedRollout } from './support/rollout';

const session: SessionParticipants = {
  ownerId: 'u-owner',
  isShared: true,
  shares: [
    { userId: 'u-reader', accessLevel: 'read' },
    { userId: 'u-writer', accessLevel: 'write' },
    { userId: 'u-future', accessLevel: 'comment' },
    { userId: 'u-owner', accessLevel: 'read' },
  ],
};

describe('agent-harness domain: session roles (SS0)', () => {
  it('RO-1: each share level has a role; an unknown level is the least, a stranger none', () => {
    expect(sessionRoleOf(session, 'u-owner')).to.equal('owner');
    expect(sessionRoleOf(session, 'u-reader')).to.equal('viewer');
    expect(sessionRoleOf(session, 'u-writer')).to.equal('collaborator');
    expect(sessionRoleOf(session, 'u-future')).to.equal('viewer');
    expect(sessionRoleOf(session, 'u-stranger')).to.equal(null);
  });

  it('RO-1: a share counts only while the session is shared', () => {
    expect(sessionRoleOf({ ...session, isShared: false }, 'u-writer')).to.equal(null);
    expect(sessionRoleOf({ ...session, isShared: false }, 'u-owner')).to.equal('owner');
  });

  it('RO-2: only the owner sends turns; collaborators are refused with their own code until SS2', () => {
    expect(turnRefusal('owner')).to.equal(null);
    expect(turnRefusal('collaborator')).to.equal('COLLABORATOR_TURNS_DISABLED');
    expect(turnRefusal('viewer')).to.equal('VIEWER_READ_ONLY');
  });

  it('only the owner shares, deletes or archives', () => {
    const roles: Array<SessionRole | null> = ['owner', 'collaborator', 'viewer', null];
    expect(roles.map(mayManageSession)).to.deep.equal([true, false, false, false]);
  });
});

describe('RunAdmissionService: a participant who may not send turns is told why (SS0, RO-4)', () => {
  const request = {
    orgId: 'org-1',
    actorId: 'u-writer',
    sessionId: 'session-1',
    idempotencyKey: 'k-1',
    deadlineMs: 60_000,
    input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
  } as never;

  const roles = (role: SessionRole | null): SessionRoleLookup & { asked: unknown[] } => {
    const asked: unknown[] = [];
    return {
      asked,
      roleOf: (scope) => {
        asked.push(scope);
        return Promise.resolve(role);
      },
    };
  };

  const refusedWith = async (role: SessionRole | null): Promise<{ error: unknown; lines: unknown[][]; lookup: ReturnType<typeof roles> }> => {
    const store = { answeredRun: async () => null, admit: sinon.stub().rejects(new HarnessNotFoundError()) };
    const lines: unknown[][] = [];
    const logger: HarnessLogger = {
      debug: () => undefined,
      info: () => undefined,
      warn: (...args) => void lines.push(['warn', ...args]),
      error: (...args) => void lines.push(['error', ...args]),
    };
    const lookup = roles(role);
    const service = new RunAdmissionService({
      store: store as never,
      rollout: fixedRollout(),
      logger,
      roles: lookup,
    });
    try {
      await service.submit(request);
    } catch (error) {
      return { error, lines, lookup };
    }
    return expect.fail('expected a refusal');
  };

  it('a collaborator: 403 AGENT_HARNESS_COLLABORATOR_TURNS_DISABLED, logged with the reason', async () => {
    const { error, lines, lookup } = await refusedWith('collaborator');
    expect(error).to.be.instanceOf(HarnessSessionRoleRefusedError);
    expect((error as HarnessSessionRoleRefusedError).code).to.equal('COLLABORATOR_TURNS_DISABLED');
    expect(lookup.asked).to.deep.equal([{ orgId: 'org-1', actorId: 'u-writer', sessionId: 'session-1' }]);
    const http = transportError(error) as { statusCode: number; code: string };
    expect([http.statusCode, http.code]).to.deep.equal([403, 'HTTP_AGENT_HARNESS_COLLABORATOR_TURNS_DISABLED']);
    expect(lines[0]?.[1]).to.equal('admission.refused');
    expect(lines[0]?.[2]).to.include({ reason: 'collaborator_turns_disabled', sessionId: 'session-1' });
  });

  it('a viewer: 403 AGENT_HARNESS_VIEWER_READ_ONLY', async () => {
    const { error } = await refusedWith('viewer');
    expect((error as HarnessSessionRoleRefusedError).code).to.equal('VIEWER_READ_ONLY');
  });

  it('a stranger, or the owner whose session cannot be bound, stays not found', async () => {
    expect((await refusedWith(null)).error).to.be.instanceOf(HarnessNotFoundError);
    expect((await refusedWith('owner')).error).to.be.instanceOf(HarnessNotFoundError);
  });
});

describe('owner-only engine work in a shared session (SS1, EO)', () => {
  const ENGINE = {
    kind: 'engine' as const,
    engine: {
      kind: 'coder',
      version: '1.0.0',
      binaryDigest: `sha256:${'a'.repeat(64)}`,
      profileDigest: `sha256:${'b'.repeat(64)}`,
    },
  };
  const onEngine: ExecutorResolver = () => ENGINE;
  const input = { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } };
  const request = (actorId: string, sessionId?: string) =>
    ({
      orgId: 'org-1',
      actorId,
      ...(sessionId !== undefined ? { sessionId } : {}),
      idempotencyKey: `k-${actorId}`,
      deadlineMs: 60_000,
      input,
    }) as never;

  const admitWith = (
    role: SessionRole | null,
    options: { roles?: boolean; executors?: ExecutorResolver; failLookup?: boolean } = {},
  ) => {
    const store = {
      answeredRun: async () => null,
      admit: sinon.stub().callsFake(async (command: { runId: string; sessionId: string | null }) => {
        if (role === null && command.sessionId !== null) throw new HarnessNotFoundError();
        return { created: true, run: { runId: command.runId } };
      }),
    };
    const lines: unknown[][] = [];
    const logger: HarnessLogger = {
      debug: () => undefined,
      info: () => undefined,
      warn: (...args) => void lines.push(['warn', ...args]),
      error: (...args) => void lines.push(['error', ...args]),
    };
    const asked: unknown[] = [];
    const lookup: SessionRoleLookup = {
      roleOf: (scope) => {
        asked.push(scope);
        return options.failLookup === true ? Promise.reject(new Error('mongo down')) : Promise.resolve(role);
      },
    };
    const service = new RunAdmissionService({
      store: store as never,
      rollout: fixedRollout(),
      logger,
      executors: options.executors ?? onEngine,
      ...(options.roles === false ? {} : { roles: lookup }),
    });
    return { service, store, lines, asked };
  };

  const outcome = async (work: Promise<unknown>): Promise<unknown> => {
    try {
      return await work;
    } catch (error) {
      return error;
    }
  };

  it('EO-1: only the owner may start engine work; a viewer stays read-only', () => {
    expect(turnRefusal('owner', 'engine')).to.equal(null);
    expect(turnRefusal('collaborator', 'engine')).to.equal('SHARED_SESSION_ENGINE');
    expect(turnRefusal('collaborator', 'loop')).to.equal('COLLABORATOR_TURNS_DISABLED');
    expect(turnRefusal('viewer', 'engine')).to.equal('VIEWER_READ_ONLY');
    expect(turnRefusal('viewer', 'loop')).to.equal('VIEWER_READ_ONLY');
  });

  it('EO-2: a collaborator is refused before the store: 403 SHARED_SESSION_ENGINE, logged once', async () => {
    const { service, store, lines, asked } = admitWith('collaborator');
    const error = await outcome(service.submit(request('u-writer', 'session-1')));
    expect(error).to.be.instanceOf(HarnessSessionRoleRefusedError);
    expect((error as HarnessSessionRoleRefusedError).code).to.equal('SHARED_SESSION_ENGINE');
    expect(store.admit.called, 'nothing written').to.equal(false);
    expect(asked).to.deep.equal([{ orgId: 'org-1', actorId: 'u-writer', sessionId: 'session-1' }]);
    const http = transportError(error) as { statusCode: number; code: string };
    expect([http.statusCode, http.code]).to.deep.equal([403, 'HTTP_AGENT_HARNESS_SHARED_SESSION_ENGINE']);
    expect(lines).to.have.length(1);
    expect(lines[0]?.slice(0, 2)).to.deep.equal(['warn', 'admission.refused']);
    expect(lines[0]?.[2]).to.include({ reason: 'shared_session_engine', sessionId: 'session-1', actorId: 'u-writer' });
  });

  it('EO-2: a viewer asking for engine work is told the conversation is read-only', async () => {
    const { service, store } = admitWith('viewer');
    const error = await outcome(service.submit(request('u-reader', 'session-1')));
    expect((error as HarnessSessionRoleRefusedError).code).to.equal('VIEWER_READ_ONLY');
    expect(store.admit.called).to.equal(false);
  });

  it('EO-3: the owner of a shared session is admitted; a stranger gets the store\'s 404; a new session asks no one', async () => {
    const owner = admitWith('owner');
    expect(await outcome(owner.service.submit(request('u-owner', 'session-1')))).to.deep.include({ route: 'durable' });
    expect(owner.store.admit.calledOnce).to.equal(true);

    const stranger = admitWith(null);
    expect(await outcome(stranger.service.submit(request('u-stranger', 'session-1')))).to.be.instanceOf(
      HarnessNotFoundError,
    );

    const fresh = admitWith('owner');
    expect(await outcome(fresh.service.submit(request('u-owner')))).to.deep.include({ route: 'durable' });
    expect(fresh.asked).to.deep.equal([]);
  });

  it('EO-4: engine work into an existing session cannot be decided without roles: 503, nothing written', async () => {
    for (const options of [{ roles: false }, { failLookup: true }]) {
      const { service, store } = admitWith('owner', options);
      expect(await outcome(service.submit(request('u-owner', 'session-1')))).to.be.instanceOf(
        HarnessUnavailableError,
      );
      expect(store.admit.called).to.equal(false);
    }
  });

  it('EO-4: a loop admission reads no role before the store', async () => {
    const { service, store, asked } = admitWith('owner', { executors: profileExecutor });
    expect(await outcome(service.submit(request('u-owner', 'session-1')))).to.deep.include({ route: 'durable' });
    expect(store.admit.calledOnce).to.equal(true);
    expect(asked).to.deep.equal([]);
  });
});
