import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { UserController } from '../../../../src/modules/user_management/controller/users.controller';
import { Users } from '../../../../src/modules/user_management/schema/users.schema';
import { makeOrgContext, runWithOrgContext } from '../../../../src/libs/context/orgContext';

const ORG = '507f1f77bcf86cd799439012';

const makeController = () =>
  new UserController(
    { scopedJwtSecret: 's', frontendUrl: 'http://app' } as any,
    {} as any,
    {} as any,
    { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() } as any,
    {} as any,
    {} as any,
  );

const found = (rows: unknown[]) => {
  const lean = sinon.stub().resolves(rows);
  const select = sinon.stub().returns({ lean });
  const find = sinon.stub(Users, 'find').returns({ select } as any);
  return find;
};

const call = async (body: unknown, user: unknown = { userId: 'u1', orgId: ORG }) => {
  const res: any = { status: sinon.stub().returnsThis(), json: sinon.stub() };
  const next = sinon.stub();
  const run = () => makeController().checkInviteStatus({ user, body } as any, res, next);
  const orgId = (user as { orgId?: string } | undefined)?.orgId;
  // The request's org comes from the org context the auth middleware binds.
  if (orgId) {
    await runWithOrgContext(makeOrgContext({ orgId, userId: 'u1', kind: 'user', source: 'jwt' }), run);
  } else {
    await run();
  }
  return { res, next };
};

describe('UserController.checkInviteStatus (invite form membership check)', () => {
  afterEach(() => sinon.restore());

  it('reports members and still-pending invites, and leaves new addresses out', async () => {
    const find = found([
      { email: 'dave@lab.test', hasLoggedIn: true },
      { email: 'pat@lab.test', hasLoggedIn: false },
    ]);
    const { res, next } = await call({
      emails: ['Dave@Lab.test', 'pat@lab.test', 'new@lab.test'],
    });

    expect(next.called).to.equal(false);
    expect(res.status.firstCall.args[0]).to.equal(200);
    expect(res.json.firstCall.args[0]).to.deep.equal({
      existing: [
        { email: 'dave@lab.test', status: 'member' },
        { email: 'pat@lab.test', status: 'invited' },
      ],
    });
    // Scoped to the caller's organisation, active accounts only, normalised addresses.
    expect(find.firstCall.args[0]).to.deep.equal({
      email: { $in: ['dave@lab.test', 'pat@lab.test', 'new@lab.test'] },
      orgId: ORG,
      isDeleted: false,
    });
  });

  it('does not call a service account an existing member', async () => {
    found([
      { email: 'bot@lab.test', hasLoggedIn: true, kind: 'service' },
      { email: 'real@lab.test', hasLoggedIn: true, kind: 'user' },
    ]);
    const { res } = await call({ emails: ['bot@lab.test', 'real@lab.test'] });
    expect(res.json.firstCall.args[0].existing).to.deep.equal([
      { email: 'real@lab.test', status: 'member' },
    ]);
  });

  it('skips the lookup when nothing in the list is a valid address', async () => {
    const find = sinon.stub(Users, 'find');
    const { res } = await call({ emails: ['not-an-email', '"x@y.co")'] });
    expect(find.called).to.equal(false);
    expect(res.json.firstCall.args[0]).to.deep.equal({ existing: [] });
  });

  it('refuses a body without an emails array, and an unauthenticated caller', async () => {
    const bad = await call({ emails: 'a@b.co' });
    expect(bad.next.firstCall.args[0].statusCode).to.equal(400);
    const anon = await call({ emails: [] }, null);
    expect(anon.next.firstCall.args[0].statusCode).to.equal(404);
  });
});
