/**
 * CPD-9 adapters without a database: the request ids (UL-1), the fail-closed
 * directory stand-in (UD-1), the entity-events subscriber's lifecycle (DE-4),
 * the user-group membership read (OF-3), and the skill candidate
 * routes with and without configuration-change approvals bound (FAC-7, FAC-8). The replica-set halves run in the Mongo itests.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import axios from 'axios';
import { ulid } from '../../../src/libs/utils/ulid';
import { UnreadableDirectory } from '../../../src/modules/control-plane/adapters/unreadable-directory';
import { DirectoryEventsSubscriber } from '../../../src/modules/control-plane/adapters/messaging/directory-events-subscriber';
import { LiveOrgRoles, UserGroupMembership } from '../../../src/libs/repositories/control-plane/user-directory/mongo';
import { silentControlPlaneLog } from '../../../src/modules/control-plane/ports/log';
import type { IMessageConsumer, StreamMessage } from '../../../src/libs/types/messaging.types';
import { UserGroups } from '../../../src/modules/user_management/schema/userGroup.schema';
import * as userAdmin from '../../../src/modules/user_management/services/user-admin.service';
import { createSkillsRouter } from '../../../src/modules/skills/routes/skills.routes';
import { rejection } from './support/cp-objects';
import { inRequestOrg } from '../../helpers/request-org-context';

const ORG = '507f1f77bcf86cd799439011';
const USER = '507f191e810c19729de860ea';
const GROUP = '64b7f0c2a1b2c3d4e5f60718';

describe('CPD-9 adapters', () => {
  afterEach(() => sinon.restore());

  it('UL-1: request ids are 26 Crockford characters, ordered by time, the same for the same time and randomness', () => {
    const zeros = () => Buffer.alloc(10);
    expect(ulid(1_700_000_000_000, zeros)).to.match(/^[0-9A-HJKMNP-TV-Z]{26}$/);
    expect(ulid(1_700_000_000_000, zeros)).to.equal(ulid(1_700_000_000_000, zeros));
    expect(ulid(1_700_000_000_001, zeros) > ulid(1_700_000_000_000, zeros)).to.equal(true);
    expect(ulid()).to.not.equal(ulid());
  });

  it('UD-1: the stand-in directory answers every read with a failure, never "no teams" or "no access"', async () => {
    const directory = new UnreadableDirectory();
    const actor = { orgId: ORG, userId: USER };
    for (const read of [
      directory.teamsOf(ORG, USER),
      directory.authorizeRun(ORG, 'a7', actor),
      directory.canEdit(ORG, 'a7', actor),
      directory.ownerTeam(ORG, 'a7'),
    ]) {
      expect(await rejection(read)).to.be.an('error');
    }
  });

  it('DE-4: the subscriber follows entity-events once, delivers its events, and disconnects on stop', async () => {
    let handler: ((message: StreamMessage<unknown>) => Promise<void>) | null = null;
    const consumer = {
      connected: false,
      subscribed: [] as string[],
      connect: async () => {
        consumer.connected = true;
      },
      disconnect: async () => {
        consumer.connected = false;
      },
      isConnected: () => consumer.connected,
      subscribe: async (topics: string[]) => {
        consumer.subscribed.push(...topics);
      },
      consume: async (h: (message: StreamMessage<unknown>) => Promise<void>) => {
        handler = h;
      },
    };
    const created = sinon.stub().returns(consumer as unknown as IMessageConsumer);
    const dropped: string[] = [];
    const subscriber = new DirectoryEventsSubscriber(
      created,
      { teamMembershipChanged: (_org, team) => dropped.push(`team:${team}`) },
      { agentChanged: (_org, agent) => dropped.push(`agent:${agent}`) },
      silentControlPlaneLog,
    );
    await subscriber.start();
    await subscriber.start();
    expect(created.callCount).to.equal(1);
    expect(consumer.subscribed).to.deep.equal(['entity-events']);
    await handler!({ key: ORG, value: { eventType: 'agentChanged', payload: { orgId: ORG, agentKey: 'a7' } } });
    expect(dropped).to.deep.equal(['agent:a7']);
    await subscriber.stop();
    expect(consumer.connected).to.equal(false);
    await subscriber.stop();
  });

  it('OF-3: group membership reads the org\'s live, not deleted, group; malformed ids are no member without a query', async () => {
    const exists = sinon.stub(UserGroups, 'exists').resolves({ _id: GROUP } as never);
    const groups = new UserGroupMembership();
    expect(await groups.isMember(ORG, GROUP, USER)).to.equal(true);
    expect(exists.firstCall.args[0]).to.deep.equal({ _id: GROUP, orgId: ORG, isDeleted: { $ne: true }, users: USER });
    expect(await groups.isMember(ORG, 'not-an-id', USER)).to.equal(false);
    expect(exists.callCount).to.equal(1);
    const role = sinon.stub(userAdmin, 'getActiveUserOrgRole').resolves('admin');
    expect(await new LiveOrgRoles().roleOf(ORG, USER)).to.equal('admin');
    expect(role.firstCall.args).to.deep.equal([USER, ORG]);
  });

  describe('FAC-7, FAC-8: the skill candidate routes and the configuration-change approvals extension point', () => {
    const candidateRoute = (bound: Record<string, unknown>) => {
      const bindings: Record<string, unknown> = {
        AppConfig: { aiBackend: 'http://ai:8000' },
        AuthMiddleware: { authenticate: (_req: unknown, _res: unknown, next: () => void) => next() },
        Logger: undefined,
        ...bound,
      };
      const container = {
        get: (key: string) => bindings[key],
        isBound: (key: string) => key in bound,
      };
      const router = createSkillsRouter(container as never);
      const layer = (router.stack as Array<{ route?: { path: string; methods: Record<string, boolean>; stack: Array<{ handle: unknown }> } }>)
        .find((l) => l.route?.path === '/candidates/:candidateId/approve' && l.route.methods.post)!;
      return inRequestOrg(layer.route!.stack[layer.route!.stack.length - 1]!.handle as (req: unknown, res: unknown, next: unknown) => unknown);
    };
    const call = async (handle: (req: unknown, res: unknown, next: unknown) => unknown) => {
      const res = { status: sinon.stub(), json: sinon.stub() };
      res.status.returns(res);
      await handle(
        { headers: {}, params: { candidateId: 'cand-1' }, query: {}, body: {}, user: { userId: 'u1', orgId: ORG } },
        res,
        sinon.stub(),
      );
      return res;
    };

    it('FAC-7: decide through the bound factory', async () => {
      const submit = sinon.stub().resolves({ request: {}, applied: { value: { status: 200, data: { ok: true } } } });
      const factory = sinon.stub().returns({ submit });
      const res = await call(candidateRoute({ ControlPlaneApprovalsFactory: factory }));
      expect(factory.calledOnce).to.equal(true);
      expect(submit.firstCall.args[0].target).to.deep.equal({ type: 'catalog-item', id: 'skill-candidate:cand-1' });
      expect(res.status.calledWith(200)).to.equal(true);
    });

    it('FAC-8: nothing bound, the admin\'s approval goes to the skill store at once, whatever the environment says', async () => {
      const saved = process.env.CONTROL_PLANE_APPROVALS;
      process.env.CONTROL_PLANE_APPROVALS = 'on';
      try {
        const post = sinon.stub(axios, 'request').resolves({ status: 200, data: { approved: true }, headers: {} } as never);
        const res = await call(candidateRoute({}));
        expect(post.called, 'the candidate is approved through the skill store').to.equal(true);
        expect(String(post.lastCall.args[0].url)).to.match(/\/candidates\/cand-1\/approve$/);
        expect(res.status.calledWith(200)).to.equal(true);
      } finally {
        if (saved === undefined) delete process.env.CONTROL_PLANE_APPROVALS;
        else process.env.CONTROL_PLANE_APPROVALS = saved;
      }
    });
  });
});
