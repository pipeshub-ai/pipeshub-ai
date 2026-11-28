/**
 * The `RoutingDecider` contract (LAT1 RT-1..RT-5): what every adapter must
 * decide for a chat turn, stated as a world (the org's rollout, the
 * conversation's record, whether a worker was seen) so the first adapter
 * (today's rollout) and CPD-10's (the resolved run configuration) are held
 * to the same vectors.
 */
import { expect } from 'chai';
import { HarnessRolloutSettings } from '../../../../src/modules/agent-harness/domain/rollout';
import {
  RoutingDecider,
  RoutingSubject,
} from '../../../../src/modules/agent-harness/ports/routing-decider';
import { modeExpectation } from './routing-vectors';

export interface RoutingWorld {
  settings: HarnessRolloutSettings;
  /** The conversation the turn continues, as its record says; absent: a new conversation. */
  conversation?: { projectId?: string; durable: boolean };
  workerLive: boolean;
  /** The family the turn's model resolves to; null: it cannot be resolved. Absent: null. */
  modelFamily?: string | null;
}

export type RoutingDeciderFactory = (world: RoutingWorld) => RoutingDecider;

const ENABLED: HarnessRolloutSettings = {
  infrastructureReady: true,
  killSwitch: false,
  orgEnabled: true,
  modelFamilies: null,
};

export const SESSION = '64b7f0c2a1b2c3d4e5f60718';
export const PROJECT = '64b7f0c2a1b2c3d4e5f60799';

/** The agent route's modes, each with the routing table's answer (QA-8: `routing.json` decides, not this list). */
export const AGENT_MODES: ReadonlyArray<[string, string]> = [
  'quick',
  'auto',
  'react',
  'deep',
  'planExecute',
  'verification',
].map((mode) => [mode, modeExpectation('agent', mode)]);

/** The assistant's modes, each with the routing table's answer: search durable; Universal Agent Mode legacy. */
export const ASSISTANT_MODES: ReadonlyArray<[string, string]> = [
  'internal_search',
  'web_search',
  'agent',
  'quick',
].map((mode) => [mode, modeExpectation('assistant', mode)]);

const subject = (overrides: Partial<RoutingSubject> & { chatMode: string }): RoutingSubject => {
  const { chatMode, ...rest } = overrides;
  return { orgId: 'org-1', actorId: 'user-1', model: { chatMode }, ...rest };
};

export function routingDeciderContract(name: string, make: RoutingDeciderFactory): void {
  const decide = async (world: Partial<RoutingWorld>, turn: Parameters<typeof subject>[0]) =>
    make({ settings: ENABLED, workerLive: true, ...world }).decide(subject(turn));
  const path = (useHarness: boolean, reason: string): string =>
    useHarness ? 'durable' : reason;

  describe(`RoutingDecider contract: ${name}`, () => {
    describe('RT-1: every chat mode', () => {
      for (const [mode, expected] of AGENT_MODES) {
        it(`agent ${mode}: ${expected}`, async () => {
          const { routing } = await decide({}, { chatMode: mode, agentKey: 'agent-1' });
          expect(path(routing.useHarness, routing.reason)).to.equal(expected);
        });
      }
      for (const [mode, expected] of ASSISTANT_MODES) {
        it(`assistant ${mode}: ${expected}`, async () => {
          const { routing } = await decide({}, { chatMode: mode });
          expect(path(routing.useHarness, routing.reason)).to.equal(expected);
        });
      }
    });

    describe('RT-2: project scope comes from the server', () => {
      it('a conversation whose record is in a project stays on today’s loop, in a durable mode', async () => {
        const { routing, scope } = await decide(
          { conversation: { projectId: PROJECT, durable: false } },
          { chatMode: 'quick', agentKey: 'agent-1', sessionId: SESSION },
        );
        expect(routing).to.include({ useHarness: false, reason: 'project_scope' });
        expect(scope).to.deep.equal({ agentKey: 'agent-1', projectId: PROJECT });
      });

      it('one that once ran durably and was moved into a project too', async () => {
        const { routing } = await decide(
          { conversation: { projectId: PROJECT, durable: true } },
          { chatMode: 'internal_search', sessionId: SESSION },
        );
        expect(routing).to.include({ useHarness: false, reason: 'project_scope' });
      });

      it('a new conversation the request creates in a project', async () => {
        const { routing, scope } = await decide({}, { chatMode: 'internal_search', projectId: PROJECT });
        expect(routing).to.include({ useHarness: false, reason: 'project_scope' });
        expect(scope).to.deep.equal({ projectId: PROJECT });
      });
    });

    describe('RT-3: the org rollout wins over everything', () => {
      const cases: Array<[string, Partial<HarnessRolloutSettings>]> = [
        ['kill_switch', { killSwitch: true }],
        ['org_disabled', { orgEnabled: false }],
        ['infrastructure_unavailable', { infrastructureReady: false }],
      ];
      for (const [reason, settings] of cases) {
        for (const [mode, agent] of [['quick', true], ['internal_search', false], ['planExecute', true]] as const) {
          it(`${reason}: ${mode}${agent ? ' (agent)' : ''}`, async () => {
            const { routing } = await decide(
              { settings: { ...ENABLED, ...settings }, conversation: { projectId: PROJECT, durable: false } },
              { chatMode: mode, sessionId: SESSION, ...(agent ? { agentKey: 'agent-1' } : {}) },
            );
            expect(routing).to.include({ useHarness: false, reason });
          });
        }
      }
    });

    describe('RT-4: a turn nobody could run', () => {
      it('no worker seen, a new conversation: today’s loop', async () => {
        const { routing } = await decide({ workerLive: false }, { chatMode: 'quick', agentKey: 'agent-1' });
        expect(routing).to.include({ useHarness: false, reason: 'no_live_worker' });
      });

      it('no worker seen, a durable conversation: durable, its turns stay together', async () => {
        const { routing } = await decide(
          { workerLive: false, conversation: { durable: true } },
          { chatMode: 'quick', agentKey: 'agent-1', sessionId: SESSION },
        );
        expect(routing).to.include({ useHarness: true, reason: 'enabled' });
      });

      it('no worker seen, a schedule’s firing: durable (it waits for a worker)', async () => {
        const { routing } = await decide({ workerLive: false }, { chatMode: 'auto', agentKey: 'agent-1', scheduled: true });
        expect(routing.useHarness).to.equal(true);
      });
    });

    describe('RT-5: agent scope', () => {
      it('is the route’s agent, and nothing for the assistant', async () => {
        expect((await decide({}, { chatMode: 'deep', agentKey: 'agent-7' })).scope).to.deep.equal({ agentKey: 'agent-7' });
        expect((await decide({}, { chatMode: 'web_search' })).scope).to.deep.equal({});
      });
    });
  });
}
