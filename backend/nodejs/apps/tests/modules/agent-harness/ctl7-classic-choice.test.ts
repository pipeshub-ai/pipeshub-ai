/**
 * CTL-7: the chat switch asks for the classic path with `harness: "legacy"`.
 * Routing itself is `routing.json` (RV-57..RV-63); this holds the request
 * field over HTTP, the logged reason, and the engine agent that ignores it.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { AgentAuthorizer, RouteToLegacy } from '../../../src/modules/agent-harness/application/admission/policy-steps';
import { policyContext } from '../../../src/modules/agent-harness/application/admission/context';
import type { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { RolloutRoutingDecider } from '../../../src/modules/agent-harness/application/rollout-routing';
import { chatTurnInput, legacyRequested } from '../../../src/modules/agent-harness/transport/chat-turn-route';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { ChatTurnHarness, logged, startChatTurnHarness } from './support/chat-turn-harness';

describe('CTL-7: the person chooses the classic chat', () => {
  let http: ChatTurnHarness | undefined;

  afterEach(async () => {
    await http?.close();
    http = undefined;
    sinon.restore();
  });

  for (const [name, path, chatMode] of [
    ['an agent', '/agents/agent-1/conversations/stream', 'quick'],
    ['the default chat', '/conversations/stream', 'internal_search'],
  ] as const) {
    it(`${name}: a new conversation with the switch off answers on today's loop, logged user_chose_classic`, async () => {
      http = await startChatTurnHarness({ publicSurface: 'web' });
      const reply = await http.post(path, 'session', { query: 'q', chatMode, harness: 'legacy' });
      expect(await reply.text()).to.equal('legacy');
      const [line] = logged(http.lines, 'admission.legacy');
      expect(line).to.include({ reason: 'user_chose_classic', legacyRequested: true });
    });
  }

  it('the field is a routing field: the run input never carries it', () => {
    const body = { query: 'q', chatMode: 'quick', harness: 'legacy' };
    expect(chatTurnInput(body, 'agent')).to.deep.equal(chatTurnInput({ query: 'q', chatMode: 'quick' }, 'agent'));
    expect(legacyRequested(body)).to.equal(true);
    expect(legacyRequested({ harness: 'durable' })).to.equal(false);
    expect(legacyRequested(undefined)).to.equal(false);
  });

  it('a turn that opts into the retrieval telemetry runs durable and its run carries the opt-in (SYNC2-F1)', async () => {
    expect(legacyRequested({ query: 'q', includeRetrievalContext: true })).to.equal(false);
    expect(chatTurnInput({ query: 'q', includeRetrievalContext: true }, 'chat')).to.include({ includeRetrievalContext: true });
    // Off is the default: the run input holds no field, so a default turn's admission is byte-identical.
    expect(chatTurnInput({ query: 'q', includeRetrievalContext: false }, 'chat')).to.deep.equal(chatTurnInput({ query: 'q' }, 'chat'));
    http = await startChatTurnHarness({ publicSurface: 'web' });
    const reply = await http.post('/conversations/stream', 'session', {
      query: 'q', chatMode: 'internal_search', includeRetrievalContext: true,
    });
    expect(await reply.text()).to.not.equal('legacy');
    expect(http.submit.calledOnce).to.equal(true);
  });

  it('a conversation whose placement cannot be read is never moved to the classic path', async () => {
    const enabled = { useHarness: true, harnessMajor: 2 as const, reason: 'enabled' as const };
    const decider = new RolloutRoutingDecider({
      rollout: { decide: async () => enabled, decideForOrg: async () => ({ ...enabled, harnessOn: true, resumeInFlightRuns: true }) },
      sessions: { placement: async () => { throw new Error('mongo down'); } },
    });
    const subject = { orgId: 'org-1', actorId: 'user-1', agentKey: 'agent-1', model: { chatMode: 'quick' }, legacyRequested: true };
    expect(await decider.decide({ ...subject, sessionId: 's-1' })).not.to.have.property('classicRequested');
    expect(await decider.decide(subject)).to.include({ classicRequested: true });
  });

  describe('an agent that needs the harness', () => {
    const run = async (engineProfile: unknown) => {
      const lines: Array<[string, Record<string, unknown>]> = [];
      const logger: HarnessLogger = {
        debug: () => undefined,
        info: (event: string, meta: Record<string, unknown> = {}) => {
          lines.push([event, meta]);
        },
        warn: () => undefined,
        error: () => undefined,
      };
      const authorizer = new AgentAuthorizer(
        grantingAccess((agentKey) => agentDefinition({ agentKey, ...(engineProfile !== null ? { engineProfile } : {}) })),
        logger,
      );
      const context = policyContext(
        {
          orgId: 'org-1',
          actorId: 'user-1',
          agentKey: 'agent-1',
          authorization: 'Bearer caller',
          idempotencyKey: 'k-1',
          deadlineMs: 60_000,
          input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'quick' } },
        },
        {},
      );
      context.classicRequested = true;
      let thrown: unknown;
      await authorizer.run(context).catch((error: unknown) => {
        thrown = error;
      });
      return { thrown, lines };
    };

    it('an engine agent stays on the harness and the ignored request is logged', async () => {
      const { thrown, lines } = await run({ engines: ['codex'] });
      expect(thrown).to.equal(undefined);
      expect(lines).to.deep.include([
        'run.classic_request_ignored',
        { event: 'run.classic_request_ignored', orgId: 'org-1', agentKey: 'agent-1', reason: 'agent_needs_harness' },
      ]);
    });

    it('a loop agent goes to today\'s loop', async () => {
      const { thrown } = await run(null);
      expect(thrown).to.be.instanceOf(RouteToLegacy);
      expect((thrown as RouteToLegacy).reason).to.equal('user_chose_classic');
    });
  });
});
