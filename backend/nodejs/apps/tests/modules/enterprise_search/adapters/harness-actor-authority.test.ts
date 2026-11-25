import { expect } from 'chai';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import * as orgUtils from '../../../../src/modules/user_management/utils/org.utils';
import * as userAdmin from '../../../../src/modules/user_management/services/user-admin.service';
import { HarnessActorAuthority } from '../../../../src/libs/repositories/agent-harness/actor-authority/mongo';

const ORG = '507f1f77bcf86cd799439011';
const ACTOR = '507f191e810c19729de860ea';
const SECRET = 'scoped-secret';

const stubUser = (user: Record<string, unknown> | null) => {
  const exec = sinon.stub().resolves(user);
  const lean = sinon.stub().returns({ exec });
  const select = sinon.stub().returns({ lean });
  return sinon.stub(Users, 'findOne').returns({ select } as never);
};

const person = {
  orgId: ORG,
  email: 'a@example.com',
  fullName: 'Ada',
  role: 'Admin',
  kind: 'person',
  isDisabled: false,
  isDeleted: false,
};

describe('HarnessActorAuthority', () => {
  afterEach(() => sinon.restore());

  const authority = () => new HarnessActorAuthority(() => SECRET);

  it('refuses malformed ids as a deleted actor without reading the database', async () => {
    const find = sinon.stub(Users, 'findOne');
    expect(await authority().authorize(ORG, 'not-an-id')).to.deep.equal({
      refused: 'actor_deleted',
    });
    expect(await authority().authorize('nope', ACTOR)).to.deep.equal({
      refused: 'actor_deleted',
    });
    expect(find.called).to.equal(false);
  });

  it('refuses a user of another org as deleted (never trusts the schedule org)', async () => {
    stubUser({ ...person, orgId: '507f1f77bcf86cd799439099' });
    expect(await authority().authorize(ORG, ACTOR)).to.deep.equal({
      refused: 'actor_deleted',
    });
  });

  it('refuses missing, deleted, service and disabled actors with distinct reasons', async () => {
    const find = stubUser(null);
    expect(await authority().authorize(ORG, ACTOR)).to.deep.equal({
      refused: 'actor_deleted',
    });
    find.restore();
    stubUser({ ...person, kind: 'service' });
    expect(await authority().authorize(ORG, ACTOR)).to.deep.equal({
      refused: 'actor_not_person',
    });
    sinon.restore();
    stubUser({ ...person, isDisabled: true });
    expect(await authority().authorize(ORG, ACTOR)).to.deep.equal({
      refused: 'actor_disabled',
    });
    sinon.restore();
    stubUser({ ...person, isDeleted: true });
    expect(await authority().authorize(ORG, ACTOR)).to.deep.equal({
      refused: 'actor_deleted',
    });
  });

  it('refuses when the org is not active', async () => {
    stubUser(person);
    sinon.stub(orgUtils, 'findActiveOrgById').resolves(null);
    expect(await authority().authorize(ORG, ACTOR)).to.deep.equal({
      refused: 'org_inactive',
    });
  });

  it("mints a five-minute bearer of the actor's own identity and normalized role", async () => {
    const find = stubUser(person);
    sinon
      .stub(orgUtils, 'findActiveOrgById')
      .resolves({ accountType: 'business' } as never);
    const result = await authority().authorize(ORG, ACTOR);
    expect(find.firstCall.args[0]).to.deep.equal({ _id: ACTOR });
    expect(result).to.have.property('authorization');
    const header = (result as { authorization: string }).authorization;
    expect(header.startsWith('Bearer ')).to.equal(true);
    const claims = jwt.verify(header.slice(7), SECRET) as jwt.JwtPayload;
    expect(claims).to.include({
      userId: ACTOR,
      orgId: ORG,
      email: 'a@example.com',
      fullName: 'Ada',
      accountType: 'business',
      role: 'admin',
    });
    expect((claims.exp ?? 0) - (claims.iat ?? 0)).to.equal(300);
  });

  it('defaults an unknown role to member', async () => {
    stubUser({ ...person, role: 'owner' });
    sinon
      .stub(orgUtils, 'findActiveOrgById')
      .resolves({ accountType: 'individual' } as never);
    const result = (await authority().authorize(ORG, ACTOR)) as {
      authorization: string;
    };
    const claims = jwt.decode(result.authorization.slice(7)) as jwt.JwtPayload;
    expect(claims.role).to.equal('member');
  });

  it('asks the user-admin service with (userId, orgId) in that order', async () => {
    const admin = sinon.stub(userAdmin, 'isUserOrgAdmin').resolves(true);
    expect(await authority().isOrgAdmin(ORG, ACTOR)).to.equal(true);
    expect(admin.firstCall.args).to.deep.equal([ACTOR, ORG]);
  });
});
