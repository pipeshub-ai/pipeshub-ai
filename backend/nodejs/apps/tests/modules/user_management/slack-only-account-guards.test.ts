import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { Users } from '../../../src/modules/user_management/schema/users.schema';
import { UserActivities } from '../../../src/modules/auth/schema/userActivities.schema';
import { generateAuthToken } from '../../../src/modules/auth/utils/generateAuthToken';
import { findSessionRefusal } from '../../../src/libs/utils/user-session.utils';
import { refuseServiceAccountCaller } from '../../../src/modules/user_management/middlewares/refuseServiceAccountCaller';
import { assertDelegationStillHolds } from '../../../src/modules/oauth_provider/services/delegation-guard';
import { DelegationService } from '../../../src/modules/oauth_provider/services/delegation.service';
import { getActiveUserOrgRole } from '../../../src/modules/user_management/services/user-admin.service';
import { assertServiceAccountRole } from '../../../src/modules/user_management/constants/service-account.constants';
import { createMockQuery } from '../../helpers/mock-mongo';

/**
 * Every place an account is given a session, a token of its own, delegated
 * rights or the admin role must refuse a Slack-only account (`kind: 'slack'`).
 * A new such path belongs in this table.
 */

const orgId = new mongoose.Types.ObjectId().toHexString();
const slackUser = {
  _id: new mongoose.Types.ObjectId(),
  orgId,
  email: 'sam@acme.test',
  fullName: 'Sam',
  kind: 'slack',
  role: 'admin', // even a record that says admin
  isDeleted: false,
};
const logger = { error: sinon.stub(), warn: sinon.stub(), info: sinon.stub(), debug: sinon.stub() };

const refusalPaths: Array<{ path: string; refuses: () => Promise<unknown>; because: RegExp }> = [
  {
    path: 'session issue (every sign-in method and the refresh exchange)',
    because: /Slack/,
    refuses: () => generateAuthToken({ ...slackUser, _id: String(slackUser._id) }, 'jwt-secret'),
  },
  {
    path: 'an existing session (HTTP auth middleware, desktop socket)',
    because: /Slack/,
    refuses: async () => {
      const reason = await findSessionRefusal(
        { userId: String(slackUser._id), orgId, role: 'member', iat: Math.floor(Date.now() / 1000) } as never,
        logger as never,
      );
      if (reason) throw new Error(reason);
    },
  },
  {
    path: 'personal access tokens, OAuth apps and OAuth authorize (refuseServiceAccountCaller)',
    because: /Slack/,
    refuses: async () => {
      const next = sinon.stub();
      await refuseServiceAccountCaller({ user: { userId: String(slackUser._id) } } as never, {} as never, next);
      const error = next.firstCall.args[0];
      if (error) throw error;
    },
  },
  {
    path: 'a delegated token acting for it',
    because: /Slack-only/,
    refuses: () => assertDelegationStillHolds(new mongoose.Types.ObjectId(), slackUser._id, orgId, { kind: 'slack', role: 'member' }),
  },
  {
    path: 'allowing a service account to act for it',
    because: /subject_is_slack_only/,
    refuses: async () => {
      const verdict = await new DelegationService(logger as never).resolveSubject(orgId, String(slackUser._id));
      if (!verdict.ok) throw new Error(verdict.refusedReason);
    },
  },
  {
    path: 'the admin role, on write',
    because: /Slack/,
    refuses: async () => assertServiceAccountRole('slack', 'admin'),
  },
  {
    path: 'the admin role, on read',
    because: /clamped to member/,
    refuses: async () => {
      if ((await getActiveUserOrgRole(String(slackUser._id), orgId)) === 'admin') {
        throw new Error('not refused');
      }
      throw new Error('clamped to member');
    },
  },
];

describe('a Slack-only account is refused by every sign-in and token path', () => {
  beforeEach(() => {
    sinon.stub(Users, 'findOne').callsFake((() => createMockQuery(slackUser)) as never);
    sinon.stub(UserActivities, 'findOne').callsFake((() => createMockQuery(null)) as never);
  });
  afterEach(() => sinon.restore());

  for (const { path, refuses, because } of refusalPaths) {
    it(path, async () => {
      let error: unknown;
      try {
        await refuses();
      } catch (e) {
        error = e;
      }
      expect(error, `${path} must refuse a Slack-only account`).to.be.instanceOf(Error);
      expect((error as Error).message).to.match(because);
    });
  }
});
