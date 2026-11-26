/**
 * BND-2, BND-3: the directory caches binder (WP-8's per-feature binders). The
 * caches and their entity-events subscriber are bound only when the
 * dependencies give a directory, and a directory event drops a cached entry;
 * no built-in binder binds configuration-change approvals.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import { bindDirectoryCaches } from '../../../src/modules/agent-harness/binders/directory-caches';
import { bindAgentHarness, type AgentHarnessDependencies } from '../../../src/modules/agent-harness/composition';
import type { HarnessBindingContext } from '../../../src/modules/agent-harness/binders/context';
import {
  CONTROL_PLANE_APPROVALS_FACTORY,
  CONTROL_PLANE_DIRECTORY_EVENTS,
  CONTROL_PLANE_MEMBERSHIP,
} from '../../../src/modules/agent-harness/binders/tokens';
import type { CachingMembershipPort, DirectoryEventsSubscriber } from '../../../src/modules/control-plane';
import type { IMessageConsumer } from '../../../src/libs/types/messaging.types';
import { InMemoryAgentDirectory, InMemoryMembership } from '../control-plane/support/in-memory-services';
import { testControlPlane } from '../control-plane/support/in-memory';

const contextWith = (container: Container, dependencies: Record<string, unknown> = {}): HarnessBindingContext =>
  ({ container, dependencies, logger: undefined, controlPlane: testControlPlane() }) as unknown as HarnessBindingContext;

describe('directory caches binder', () => {
  it('BND-2: directory caches only with a directory; an entity event drops a cached membership', async () => {
    const none = new Container();
    bindDirectoryCaches(contextWith(none));
    expect(none.isBound(CONTROL_PLANE_MEMBERSHIP)).to.equal(false);
    const membership = new InMemoryMembership();
    membership.teams.set('org-a|u1', [{ teamId: 't9', role: 'OWNER' }]);
    const container = new Container();
    bindDirectoryCaches(
      contextWith(container, {
        directory: { events: () => ({}) as IMessageConsumer, membership, agents: new InMemoryAgentDirectory() },
      }),
    );
    const cached = container.get<CachingMembershipPort>(CONTROL_PLANE_MEMBERSHIP);
    expect(await cached.teamsOf('org-a', 'u1')).to.have.length(1);
    membership.teams.set('org-a|u1', []);
    await container
      .get<DirectoryEventsSubscriber>(CONTROL_PLANE_DIRECTORY_EVENTS)
      .handle({ key: 'org-a', value: { eventType: 'teamMembershipChanged', payload: { orgId: 'org-a', teamId: 't9', userIds: ['u1'] } } });
    expect(await cached.teamsOf('org-a', 'u1')).to.deep.equal([]);
  });

  it('BND-3: the built-in binders bind no configuration-change approvals, whatever the environment says', () => {
    const saved = process.env.CONTROL_PLANE_APPROVALS;
    process.env.CONTROL_PLANE_APPROVALS = 'on';
    try {
      const container = new Container();
      bindAgentHarness(container, {
        sessions: {},
        conversations: {},
        history: {},
        agentAccess: {},
        rollout: {},
        authority: {},
        keyEncryptionKey: {},
        memory: { policies: { read: async () => ({ value: null, revision: null }) } },
      } as unknown as AgentHarnessDependencies);
      expect(container.isBound(CONTROL_PLANE_APPROVALS_FACTORY)).to.equal(false);
    } finally {
      if (saved === undefined) delete process.env.CONTROL_PLANE_APPROVALS;
      else process.env.CONTROL_PLANE_APPROVALS = saved;
    }
  });
});
