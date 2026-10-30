import 'reflect-metadata';
import { expect } from 'chai';
import jwt from 'jsonwebtoken';
import { isSlackFullMemberOfTeam } from '../../../src/integrations/slack-bot/src/helpers';
import { slackJwtGenerator } from '../../../src/libs/utils/createJwt';

const member = {
  id: 'U1',
  team_id: 'T1',
  profile: { email: 'sam@acme.test' },
  is_email_confirmed: true,
};

describe('isSlackFullMemberOfTeam: who may get a Slack-only account', () => {
  it('accepts a full member of the bot workspace with a confirmed email', () => {
    expect(isSlackFullMemberOfTeam(member, 'T1')).to.equal(true);
  });

  for (const [label, user, team] of [
    ['a bot', { ...member, is_bot: true }, 'T1'],
    ['a deactivated user', { ...member, deleted: true }, 'T1'],
    ['a multi-channel guest', { ...member, is_restricted: true }, 'T1'],
    ['a single-channel guest', { ...member, is_ultra_restricted: true }, 'T1'],
    ['someone from another workspace in a shared channel', { ...member, is_stranger: true }, 'T1'],
    ['a member of another workspace', { ...member, team_id: 'T2' }, 'T1'],
    ['an unconfirmed email', { ...member, is_email_confirmed: false }, 'T1'],
    ['Slack leaving out whether the email is confirmed', { ...member, is_email_confirmed: undefined }, 'T1'],
    ['no email', { ...member, profile: {} }, 'T1'],
    ['an unknown bot workspace', member, undefined],
  ] as const) {
    it(`refuses ${label}`, () => {
      expect(isSlackFullMemberOfTeam(user, team)).to.equal(false);
    });
  }

  it('signs the membership facts only when told the person is a full member', () => {
    const vouched = jwt.decode(
      slackJwtGenerator('sam@acme.test', 'secret', undefined, {
        configId: 'cfg-1',
        slackFullMember: true,
        slackUserId: 'U1',
        slackTeamId: 'T1',
        slackFullName: 'Sam',
      }),
    ) as Record<string, unknown>;
    expect(vouched).to.include({ slackFullMember: true, slackUserId: 'U1', slackTeamId: 'T1', slackFullName: 'Sam' });

    const plain = jwt.decode(slackJwtGenerator('sam@acme.test', 'secret', undefined, { configId: 'cfg-1' })) as Record<string, unknown>;
    expect(plain).to.not.have.property('slackFullMember');
    expect(plain).to.not.have.property('slackUserId');
  });
});
