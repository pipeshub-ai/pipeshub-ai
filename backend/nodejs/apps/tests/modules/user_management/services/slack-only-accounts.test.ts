import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import {
  findOrCreateSlackOnlyAccount,
  slackMemberClaims,
  upgradeSlackOnlyAccount,
} from '../../../../src/modules/user_management/services/slack-only-accounts.service';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { getUserRepository } from '../../../../src/libs/repositories/user';
import { getGroupRepository } from '../../../../src/libs/repositories/group';
import { getActivityRepository } from '../../../../src/libs/repositories/activity';
import { getCredentialRepository } from '../../../../src/libs/repositories/credential';
import { UniqueConstraintError } from '../../../../src/libs/repositories/errors';
import { ForbiddenError } from '../../../../src/libs/errors/http.errors';
import { assertServiceAccountRole } from '../../../../src/modules/user_management/constants/service-account.constants';

const orgId = new mongoose.Types.ObjectId().toHexString();
const claims = { slackUserId: 'U1', slackTeamId: 'T1', fullName: 'Sam' };
const params = { orgId, email: 'Sam@Acme.test', claims, configId: 'cfg-1' };

describe('slack-only accounts', () => {
  afterEach(() => sinon.restore());

  describe('slackMemberClaims: only what the bot vouched for', () => {
    const full = { slackFullMember: true, slackUserId: 'U1', slackTeamId: 'T1', slackFullName: 'Sam' };
    it('reads a full member', () => {
      expect(slackMemberClaims(full)).to.deep.equal(claims);
    });
    for (const [label, payload] of [
      ['no token', undefined],
      ['no membership claim (guest, other workspace, unconfirmed email, bot)', { ...full, slackFullMember: undefined }],
      ['a membership claim that is not literally true', { ...full, slackFullMember: 'true' }],
      ['no Slack user id', { ...full, slackUserId: '' }],
      ['no Slack team id', { ...full, slackTeamId: undefined }],
    ] as const) {
      it(`is null for ${label}`, () => {
        expect(slackMemberClaims(payload as Record<string, unknown> | undefined)).to.equal(null);
      });
    }
  });

  describe('findOrCreateSlackOnlyAccount', () => {
    let create: sinon.SinonStub;
    let lookup: sinon.SinonStub;
    let groups: sinon.SinonStub;
    let activity: sinon.SinonStub;
    let blocked: sinon.SinonStub;
    const duplicate = () => new UniqueConstraintError('email');
    beforeEach(() => {
      const users = getUserRepository();
      create = sinon.stub(users, 'create');
      lookup = sinon.stub(users, 'listByEmails').resolves([]);
      groups = sinon.stub(getGroupRepository(), 'addToEveryone');
      activity = sinon.stub(getActivityRepository(), 'record').resolves();
      blocked = sinon.stub(getCredentialRepository(), 'blockedUserIds').resolves([]);
    });

    it('creates a Slack-only account with no group and records who created it', async () => {
      const createdId = new mongoose.Types.ObjectId().toHexString();
      create.callsFake(async (org: string, doc: Record<string, unknown>) => ({ ...doc, orgId: org, _id: createdId }));

      const account = await findOrCreateSlackOnlyAccount(params);

      expect(account?._id).to.equal(createdId);
      expect(create.firstCall.args[0]).to.equal(orgId);
      expect(lookup.firstCall.args).to.deep.equal([orgId, ['sam@acme.test']]);
      const doc = create.firstCall.args[1] as Record<string, unknown>;
      expect(doc).to.include({
        email: 'sam@acme.test',
        kind: 'slack',
        role: 'member',
        hasLoggedIn: false,
        slackUserId: 'U1',
        slackTeamId: 'T1',
        fullName: 'Sam',
      });
      expect(doc).to.not.have.property('password');
      expect(groups.called, 'never joins the everyone group').to.equal(false);
      expect(activity.firstCall.args[0]).to.include({
        activityType: 'ACCOUNT CREATED FROM SLACK',
        details: 'created from Slack bot cfg-1',
      });
    });

    it('reuses the live account for that email instead of creating one', async () => {
      const existing = { _id: new mongoose.Types.ObjectId(), orgId, kind: 'slack' };
      lookup.resolves([existing]);

      expect(await findOrCreateSlackOnlyAccount(params)).to.equal(existing);
      expect(create.called).to.equal(false);
    });

    it('gives two concurrent first messages one account: the loser reads the winner back', async () => {
      const winner = { _id: new mongoose.Types.ObjectId(), orgId, kind: 'slack' };
      lookup.onFirstCall().resolves([]).onSecondCall().resolves([winner]);
      create.rejects(duplicate());

      expect(await findOrCreateSlackOnlyAccount(params)).to.equal(winner);
      expect(create.calledOnce).to.equal(true);
      expect(activity.called).to.equal(false);
    });

    for (const [label, row] of [
      ['deleted', { isDeleted: true }],
      ['disabled', { isDisabled: true }],
    ] as const) {
      it(`never revives a ${label} account`, async () => {
        lookup.resolves([{ _id: new mongoose.Types.ObjectId().toHexString(), orgId, kind: 'slack', ...row }]);
        let error: unknown;
        try {
          await findOrCreateSlackOnlyAccount(params);
        } catch (e) {
          error = e;
        }
        expect(error).to.be.instanceOf(ForbiddenError);
        expect(create.called).to.equal(false);
      });
    }

    it('never revives a blocked account', async () => {
      const id = new mongoose.Types.ObjectId().toHexString();
      lookup.resolves([{ _id: id, orgId, kind: 'slack' }]);
      blocked.resolves([id]);
      let error: unknown;
      try {
        await findOrCreateSlackOnlyAccount(params);
      } catch (e) {
        error = e;
      }
      expect(error).to.be.instanceOf(ForbiddenError);
    });

    it('refuses when the address is taken in another org', async () => {
      create.rejects(duplicate());
      let error: unknown;
      try {
        await findOrCreateSlackOnlyAccount(params);
      } catch (e) {
        error = e;
      }
      expect(error).to.be.instanceOf(ForbiddenError);
    });
  });

  describe('upgradeSlackOnlyAccount', () => {
    it('changes only a Slack-only record, keeping its _id, and says whether it did', async () => {
      const id = new mongoose.Types.ObjectId();
      const update = sinon.stub(Users, 'updateOne').resolves({ modifiedCount: 1 } as never);

      expect(await upgradeSlackOnlyAccount(id, orgId)).to.equal(true);
      expect(update.firstCall.args[0]).to.deep.equal({ _id: String(id), orgId, kind: 'slack' });
      expect(update.firstCall.args[1]).to.deep.equal({ $set: { kind: 'human' } });

      update.resolves({ modifiedCount: 0 } as never);
      expect(await upgradeSlackOnlyAccount(id, orgId)).to.equal(false);
    });
  });

  it('a Slack-only account can never be an administrator', () => {
    expect(() => assertServiceAccountRole('slack', 'admin')).to.throw(/Slack/);
    expect(() => assertServiceAccountRole('slack', 'member')).to.not.throw();
  });
});
